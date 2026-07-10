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
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from schemas import AwsSetupRequest
from state import templates, _aws_status, check_aws_reachable
from utils.logger import get_logger

router = APIRouter()

logger = get_logger("setup")

APP_DIR = Path(__file__).resolve().parent.parent  # ui/
ROOT_DIR = APP_DIR.parent  # repo root

# Both files, in this order: root .env feeds the collectors/detectors,
# ui/.env feeds this process. Keeping them in sync here is the whole
# point — the manual "mirror the root .env" convention lost users.
ENV_FILES = [ROOT_DIR / ".env", APP_DIR / ".env"]

ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role/.+$")
REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")

# Short timeouts: these endpoints run while the user waits on the page.
_STS_CONFIG = Config(connect_timeout=5, read_timeout=5, retries={"max_attempts": 1})


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


def _test_connection(payload: AwsSetupRequest) -> dict:
    """STS get-caller-identity with the submitted values, then AssumeRole
    when a role is given. Raises botocore exceptions on failure."""
    session = boto3.Session(
        aws_access_key_id=payload.access_key_id or None,
        aws_secret_access_key=payload.secret_access_key or None,
        region_name=payload.region,
    )
    sts = session.client("sts", config=_STS_CONFIG)
    ident = sts.get_caller_identity()
    result = {"identity": ident["Arn"], "account_id": ident["Account"]}
    if payload.role_arn:
        kwargs = {"RoleArn": payload.role_arn, "RoleSessionName": "wasteless-setup-check"}
        if payload.external_id:
            kwargs["ExternalId"] = payload.external_id
        sts.assume_role(**kwargs)
        result["role_assumed"] = payload.role_arn
        # The account that matters downstream is the one the role lives in
        # (cross-account setups), not the source identity's.
        result["account_id"] = payload.role_arn.split(":")[4]
    return result


def _write_env_files(values: dict) -> None:
    """Set KEY=VALUE in every env file: replace the line when the key
    exists, append otherwise. Empty values are left untouched (this page
    only adds or updates the AWS connection, it never unsets keys)."""
    for path in ENV_FILES:
        lines = path.read_text().splitlines() if path.exists() else []
        remaining = {k: v for k, v in values.items() if v}
        out = []
        for line in lines:
            key = line.split("=", 1)[0] if "=" in line else None
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
            else:
                out.append(line)
        out.extend(f"{k}={v}" for k, v in remaining.items())
        path.write_text("\n".join(out) + "\n")
        os.chmod(path, 0o600)


def _apply_to_process(values: dict) -> None:
    """Make the new connection live without a restart: env vars for boto3's
    chain, reset of the backend client cache, refreshed status for the
    banner and the sync job."""
    for key, value in values.items():
        if value:
            os.environ[key] = value
    from utils.aws_clients import reset_cache

    reset_cache()
    _aws_status["reachable"] = check_aws_reachable()


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    """AWS onboarding page: status, instructions, connection form."""
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
        },
    )


@router.post("/api/aws-setup/test")
def api_aws_setup_test(payload: AwsSetupRequest):
    """Dry connection test — touches AWS, never writes anything."""
    error = _validation_error(payload)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)
    try:
        result = _test_connection(payload)
    except Exception as e:
        # The botocore message (AccessDenied, InvalidClientTokenId, ...) is
        # exactly what the user needs to fix their input; no secrets in it.
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    return {"success": True, **result}


@router.post("/api/aws-setup")
def api_aws_setup_save(payload: AwsSetupRequest):
    """Test, then persist to both env files and apply to this process."""
    error = _validation_error(payload)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)
    try:
        result = _test_connection(payload)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

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
    logger.info(
        f"AWS connection saved via /setup (account {result['account_id']}, "
        f"role={'yes' if payload.role_arn else 'no'})"
    )
    return {"success": True, **result}
