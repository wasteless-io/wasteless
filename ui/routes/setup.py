"""Guided AWS onboarding page (/setup).

Terminal-free path to connect an AWS account, aimed at non-technical
operators: paste the role ARNs produced by onboarding/ (or access keys),
test the connection with one click, save. Saving writes BOTH env files
(root .env for the collectors, ui/.env for this process) and applies the
values to the running process so no restart is needed.
"""

import os
import re
from pathlib import Path

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from schemas import AwsDisconnectRequest, AwsSetupRequest
from state import templates, _aws_status, check_aws_reachable, get_db
from utils.collect import start_background_collection
from utils.env_files import apply_to_env, clear_env_keys, write_env_files
from utils.logger import get_logger

router = APIRouter()

logger = get_logger("setup")

APP_DIR = Path(__file__).resolve().parent.parent  # ui/
ROOT_DIR = APP_DIR.parent  # repo root

ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role/.+$")
REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
ACCOUNT_ID_RE = re.compile(r"^\d{12}$")

# Default role names of the onboarding template (RoleNamePrefix=wasteless):
# with the account ID they make the ARNs fully predictable, so the form can
# be pre-filled and the user never copies stack outputs by hand.
ROLE_NAME_PREFIX = "wasteless"

# Template served to the CloudFormation quick-create link. Published by
# scripts/publish_onboarding_template.sh; override for a fork or a private
# mirror. Must be an S3 HTTPS URL (console requirement for templateURL).
ONBOARDING_TEMPLATE_URL_DEFAULT = (
    "https://wasteless-io-onboarding.s3.eu-west-1.amazonaws.com" "/latest/wasteless-onboarding.yaml"
)

# Short timeouts: these endpoints run while the user waits on the page.
_STS_CONFIG = Config(connect_timeout=5, read_timeout=5, retries={"max_attempts": 1})


def _account_id() -> str:
    """AWS_ACCOUNT_ID from the environment (ui/.env), falling back to the
    root .env, installs made before the mirror wrote it only there.
    Returns '' unless it looks like a real 12-digit account ID."""
    acct = os.getenv("AWS_ACCOUNT_ID", "")
    if not acct:
        root_env = ROOT_DIR / ".env"
        if root_env.exists():
            for line in root_env.read_text().splitlines():
                if line.startswith("AWS_ACCOUNT_ID="):
                    acct = line.split("=", 1)[1].strip()
    return acct if ACCOUNT_ID_RE.match(acct) else ""


# Credential keys owned by /setup: what a successful save writes and what
# Disconnect clears from both env files and the process. AWS_REGION is left
# alone (it is a harmless default, not a credential).
AWS_CREDENTIAL_KEYS = [
    "AWS_ACCOUNT_ID",
    "AWS_ROLE_ARN",
    "AWS_WRITE_ROLE_ARN",
    "AWS_EXTERNAL_ID",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
]

# Data collected from the connected account, truncated on a wipe. CASCADE
# handles the recommendations -> waste_detected FK and anything hanging off
# it; RESTART IDENTITY resets the serial ids so the next account starts
# clean. Deliberately NOT wiped: budget_settings (a user preference, not
# account data) and llm_usage (this app's own AI-spend ledger).
_ACCOUNT_DATA_TABLES = (
    "ec2_metrics",
    "waste_detected",
    "recommendations",
    "actions_log",
    "rollback_snapshots",
    "savings_realized",
    "cloud_costs_raw",
    "waste_snapshots",
    "daily_briefings",
    "collection_runs",
)


def _repair_hint(error_msg: str, payload: AwsSetupRequest) -> str | None:
    """Map a raw AWS/botocore error to one plain next step. The botocore
    message is accurate but jargon; the person onboarding needs to know
    which field to fix, not what STS returned."""
    m = error_msg.lower()
    if "unable to locate credentials" in m or "you must specify a region" in m:
        return (
            "No credentials reached AWS. Fill the access key ID and secret, "
            "or run the CloudFormation stack (step 1) and paste the role ARN."
        )
    if "invalidclienttokenid" in m or "signaturedoesnotmatch" in m or "the security token" in m:
        return "The access key ID or secret is wrong. Re-copy both from the IAM user."
    if "not authorized to perform: sts:assumerole" in m or "access denied" in m:
        if payload.external_id:
            return (
                "The role refused the assume-role. Check the External ID matches the "
                "one on the role's trust policy, and that this identity is the trusted "
                "principal."
            )
        return (
            "The role refused the assume-role. If the CloudFormation stack was created "
            "with an External ID, paste it below; otherwise confirm this identity is the "
            "role's trusted principal."
        )
    if "does not exist" in m or "nosuchentity" in m or "cannot be found" in m:
        return (
            "That role ARN does not exist yet. Create the CloudFormation stack (step 1) "
            "first, or fix the account ID / role name in the ARN."
        )
    if "expired" in m:
        return "The credentials have expired. Generate a fresh access key or re-run the stack."
    if "could not connect" in m or "timed out" in m or "connect timeout" in m:
        return "Could not reach AWS. Check the region and this machine's network."
    return None


def _validation_error(payload: AwsSetupRequest) -> str | None:
    """Format checks before any network call; returns a message or None."""
    if not REGION_RE.match(payload.region):
        return f"invalid AWS region: {payload.region!r}"
    for label, arn in (("read-only", payload.role_arn), ("remediation", payload.write_role_arn)):
        if arn and not ROLE_ARN_RE.match(arn):
            return f"invalid {label} role ARN (expected arn:aws:iam::<12 digits>:role/<name>)"
    if payload.write_role_arn and not payload.role_arn:
        return "a remediation role requires the read-only role as well"
    if payload.access_key_id and not payload.secret_access_key:
        return "secret access key is required with an access key ID"
    if not payload.role_arn and not payload.access_key_id:
        return "provide either the wasteless role ARNs or access keys"
    return None


class SetupError(Exception):
    """A connection-test failure carrying a UX category for /setup.

    kind:
      - 'not_ready' : the wasteless role can't be assumed yet — almost always
                      CloudFormation still creating it (the page polls on this).
      - 'creds'     : the source identity itself is wrong (bad/absent keys).
      - 'config'    : a genuine input problem (region, account, trust, ...).
    """

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


# STS error codes that mean the SOURCE identity is wrong, not that the
# wasteless role isn't ready.
_CRED_ERROR_CODES = {
    "InvalidClientTokenId",
    "SignatureDoesNotMatch",
    "AuthFailure",
    "UnrecognizedClientException",
    "ExpiredToken",
    "ExpiredTokenException",
    "TokenRefreshRequired",
}


def _aws_error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    return resp.get("Error", {}).get("Code", "") if isinstance(resp, dict) else ""


def _test_connection(payload: AwsSetupRequest) -> dict:
    """STS get-caller-identity with the submitted values, then AssumeRole when
    a role is given. Raises SetupError (categorised) on failure so the page can
    tell 'not ready yet' apart from a real error."""
    session = boto3.Session(
        aws_access_key_id=payload.access_key_id or None,
        aws_secret_access_key=payload.secret_access_key or None,
        region_name=payload.region,
    )
    sts = session.client("sts", config=_STS_CONFIG)
    try:
        ident = sts.get_caller_identity()
    except Exception as e:
        # Failure here is about the source identity, not the wasteless role.
        if _aws_error_code(e) in _CRED_ERROR_CODES or "Unable to locate credentials" in str(e):
            raise SetupError(str(e), "creds") from e
        raise SetupError(str(e), "config") from e

    result = {"identity": ident["Arn"], "account_id": ident["Account"]}
    # Both roles come from the same CloudFormation stack; the form pre-fills the
    # remediation ARN, so if the stack was created with CreateRemediationRole=
    # false it never exists — saving it silently would break remediation later,
    # hence we test it too. AccessDenied on either AssumeRole during setup is
    # treated as 'not_ready' (role/trust not in place yet); the page polls and,
    # on timeout, points at the permanent causes (detection-only, External ID).
    for arn in (payload.role_arn, payload.write_role_arn):
        if not arn:
            continue
        kwargs = {"RoleArn": arn, "RoleSessionName": "wasteless-setup-check"}
        if payload.external_id:
            kwargs["ExternalId"] = payload.external_id
        try:
            sts.assume_role(**kwargs)
        except Exception as e:
            kind = (
                "not_ready"
                if _aws_error_code(e) in ("AccessDenied", "AccessDeniedException")
                else "config"
            )
            raise SetupError(str(e), kind) from e

    if payload.role_arn:
        result["role_assumed"] = payload.role_arn
        # The account that matters downstream is the one the role lives in
        # (cross-account setups), not the source identity's.
        result["account_id"] = payload.role_arn.split(":")[4]
    if payload.write_role_arn:
        result["write_role_assumed"] = payload.write_role_arn
    return result


# Both files, in this order: root .env feeds the collectors/detectors,
# ui/.env feeds this process. Kept as a module attribute (tests patch it);
# the shared implementation lives in utils/env_files.py since the Settings
# AI card saves the same way.
ENV_FILES = [ROOT_DIR / ".env", APP_DIR / ".env"]


def _write_env_files(values: dict) -> None:
    write_env_files(values, ENV_FILES)


def _start_background_collection() -> bool:
    """Fire-and-forget first collection right after a successful save: the
    user just connected AWS, the next scheduled run is up to 5 minutes away,
    and this is exactly when they are staring at an empty dashboard. Shared
    implementation in utils/collect.py (also behind /api/collect-now)."""
    return start_background_collection(ROOT_DIR)


def _apply_to_process(values: dict) -> None:
    """Make the new connection live without a restart: env vars for boto3's
    chain, reset of the backend client cache, refreshed status for the
    banner and the sync job."""
    apply_to_env(values)
    from utils.aws_clients import reset_cache

    reset_cache()
    _aws_status["reachable"] = check_aws_reachable()


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    """AWS onboarding page: status, instructions, connection form."""
    account_id = _account_id()
    suggested_arn = f"arn:aws:iam::{account_id}:role/{ROLE_NAME_PREFIX}" if account_id else ""
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            # Not named aws_reachable: that would shadow the template
            # global (a callable) that base.html's banner invokes.
            "aws_status": _aws_status.get("reachable"),
            "current_region": os.getenv("AWS_REGION", "eu-west-1"),
            "current_role_arn": os.getenv("AWS_ROLE_ARN", ""),
            "current_write_role_arn": os.getenv("AWS_WRITE_ROLE_ARN", ""),
            "has_access_keys": bool(os.getenv("AWS_ACCESS_KEY_ID")),
            # Credentials wasteless itself holds (written by this page into the
            # env files) can be cleared by Disconnect. When the account is
            # reachable but none of these are set, the credentials come from
            # the ambient chain (~/.aws, instance profile, SSO) and wasteless
            # cannot revoke them, the disconnect zone says so.
            "creds_managed": bool(os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_ACCESS_KEY_ID")),
            "account_id": account_id,
            "suggested_role_arn": f"{suggested_arn}-readonly" if suggested_arn else "",
            "suggested_write_role_arn": f"{suggested_arn}-remediation" if suggested_arn else "",
            "onboarding_template_url": os.getenv(
                "WASTELESS_ONBOARDING_TEMPLATE_URL", ONBOARDING_TEMPLATE_URL_DEFAULT
            ),
        },
    )


@router.post("/api/aws-setup/test")
def api_aws_setup_test(payload: AwsSetupRequest):
    """Dry connection test, touches AWS, never writes anything."""
    error = _validation_error(payload)
    if error:
        return JSONResponse(
            {"success": False, "error": error, "error_kind": "config"}, status_code=400
        )
    try:
        result = _test_connection(payload)
    except SetupError as e:
        # error_kind lets the page poll on 'not_ready' and only alarm on the rest;
        # hint adds one plain repair step for the cases that stay red.
        return JSONResponse(
            {
                "success": False,
                "error": str(e),
                "error_kind": e.kind,
                "hint": _repair_hint(str(e), payload),
            },
            status_code=400,
        )
    except Exception as e:
        # Unexpected (non-SetupError) failure: default the poll category to
        # 'config' and still attach a plain repair step when the raw botocore
        # message maps to one. Accurate but jargon message, no secrets in it.
        return JSONResponse(
            {
                "success": False,
                "error": str(e),
                "error_kind": "config",
                "hint": _repair_hint(str(e), payload),
            },
            status_code=400,
        )
    return {"success": True, **result}


@router.post("/api/aws-setup")
def api_aws_setup_save(payload: AwsSetupRequest):
    """Test, then persist to both env files and apply to this process."""
    error = _validation_error(payload)
    if error:
        return JSONResponse(
            {"success": False, "error": error, "error_kind": "config"}, status_code=400
        )
    try:
        result = _test_connection(payload)
    except SetupError as e:
        return JSONResponse(
            {
                "success": False,
                "error": str(e),
                "error_kind": e.kind,
                "hint": _repair_hint(str(e), payload),
            },
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            {
                "success": False,
                "error": str(e),
                "error_kind": "config",
                "hint": _repair_hint(str(e), payload),
            },
            status_code=400,
        )

    values = {
        "AWS_REGION": payload.region,
        "AWS_ACCOUNT_ID": result["account_id"],
        "AWS_ROLE_ARN": payload.role_arn,
        "AWS_WRITE_ROLE_ARN": payload.write_role_arn,
        "AWS_EXTERNAL_ID": payload.external_id,
        "AWS_ACCESS_KEY_ID": payload.access_key_id,
        "AWS_SECRET_ACCESS_KEY": payload.secret_access_key,
    }
    _write_env_files(values)
    _apply_to_process(values)
    collection_started = _start_background_collection()
    logger.info(
        f"AWS connection saved via /setup (account {result['account_id']}, "
        f"role={'yes' if payload.role_arn else 'no'}, "
        f"collection={'started' if collection_started else 'not started'})"
    )
    return {"success": True, "collection_started": collection_started, **result}


def _wipe_account_data(conn) -> int:
    """Truncate every table holding data collected from the account being
    disconnected. Returns the row count erased (for the confirmation)."""
    cur = conn.cursor()
    # Table names are the hardcoded _ACCOUNT_DATA_TABLES constant, never user
    # input, the interpolated SQL below is safe (noqa: S608 on each line).
    parts = [f"SELECT COUNT(*) AS n FROM {t}" for t in _ACCOUNT_DATA_TABLES]  # noqa: S608
    union = " UNION ALL ".join(parts)
    count_sql = f"SELECT COALESCE(SUM(n), 0) AS total FROM ({union}) s"  # noqa: S608
    cur.execute(count_sql)
    row = cur.fetchone()
    erased = int(row["total"] if isinstance(row, dict) else row[0])
    tables = ", ".join(_ACCOUNT_DATA_TABLES)
    cur.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")  # noqa: S608
    conn.commit()
    return erased


@router.post("/api/aws-setup/disconnect")
def api_aws_setup_disconnect(payload: AwsDisconnectRequest, conn=Depends(get_db)):
    """Disconnect the current AWS account: clear its credentials from both
    env files and the process, optionally wipe the data collected from it,
    and refresh the reachability status. Leaves the form ready for a new
    account (the Switch account flow)."""
    erased = 0
    if payload.wipe_data:
        erased = _wipe_account_data(conn)

    clear_env_keys(AWS_CREDENTIAL_KEYS)
    from utils.aws_clients import reset_cache

    reset_cache()
    _aws_status["reachable"] = check_aws_reachable()

    logger.info(
        f"AWS disconnected via /setup (data {'wiped: ' + str(erased) + ' rows' if payload.wipe_data else 'kept'})"
    )
    return {"success": True, "wiped_rows": erased, "data_wiped": payload.wipe_data}
