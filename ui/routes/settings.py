"""Settings page: config editing, whitelist, policy-as-code export/import,
AI insights (LLM) connection."""

import os
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from state import get_db, templates
from schemas import ConfigUpdate, LlmSetupRequest, PolicyImport

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request, conn=Depends(get_db)):
    """Settings and configuration page."""
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager()
    config = config_manager.load_config()

    # Database stats
    cursor = conn.cursor()
    cursor.execute("""
        WITH counts AS (
            SELECT
                (SELECT COUNT(*) FROM ec2_metrics) as ec2_metrics,
                (SELECT COUNT(*) FROM waste_detected) as waste_detected,
                (SELECT COUNT(*) FROM recommendations) as recommendations,
                (SELECT COUNT(*) FROM actions_log) as actions_log,
                (SELECT COUNT(*) FROM savings_realized) as savings_realized
        )
        SELECT * FROM counts;
    """)
    stats = cursor.fetchone()
    cursor.close()

    from utils.action_registry import EXECUTION_MODES

    automatable_actions = [
        {"type": t, "mode": m} for t, m in EXECUTION_MODES.items() if m in ("boto3", "remediator")
    ]

    from core.llm import MODEL_ENV_VAR, key_env_var

    llm_model = os.getenv(MODEL_ENV_VAR, "")
    llm_key_var = key_env_var(llm_model) if llm_model else None

    return templates.TemplateResponse(
        request,
        "settings.html",
        context={
            "config": config,
            "stats": stats,
            "automatable_actions": automatable_actions,
            "llm_model": llm_model,
            "llm_key_set": bool(llm_key_var and os.getenv(llm_key_var)),
            "instance_schedule": config_manager.get_instance_schedule(),
        },
    )


@router.post("/api/config")
def api_update_config(update: ConfigUpdate):
    """Update configuration value."""
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager()

    try:
        if update.key == "auto_remediation_enabled":
            success = config_manager.set_auto_remediation_enabled(update.value)
        elif update.key == "dry_run_days":
            success = config_manager.set_dry_run_days(update.value)
        elif update.key == "grace_period_days":
            success = config_manager.set_grace_period_days(update.value)
        elif update.key == "dry_run":
            success = config_manager.set_dry_run(update.value)
        elif update.key.startswith("terraform_pr:"):
            field = update.key[len("terraform_pr:") :]
            success = config_manager.set_terraform_pr_field(field, update.value)
        elif update.key.startswith("action:"):
            action_type = update.key[len("action:") :]
            from utils.action_registry import EXECUTION_MODES

            if EXECUTION_MODES.get(action_type) not in ("boto3", "remediator"):
                raise HTTPException(
                    status_code=400, detail=f"'{action_type}' is not an automatable action type"
                )
            success = config_manager.set_action_enabled(action_type, bool(update.value))
        else:
            success = config_manager.update_protection_rule(update.key, update.value)

        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class InstanceScheduleRequest(BaseModel):
    enabled: Optional[bool] = None
    stop_time: Optional[str] = None
    start_time: Optional[str] = None
    days: Optional[list] = None
    timezone: Optional[str] = None
    tag_key: Optional[str] = None
    tag_value: Optional[str] = None
    dry_run: Optional[bool] = None


@router.post("/api/instance-schedule")
def api_instance_schedule(payload: InstanceScheduleRequest):
    """Save the instance start/stop schedule and (re)build its cron jobs."""
    from utils.config_manager import ConfigManager, ConfigValidationError

    dump = getattr(payload, "model_dump", None) or payload.dict
    values = {k: v for k, v in dump().items() if v is not None}
    cm = ConfigManager()
    try:
        ok = cm.set_instance_schedule(values)
    except ConfigValidationError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    if not ok:
        return JSONResponse(
            {"success": False, "error": "could not save the schedule"}, status_code=500
        )
    try:
        from jobs import reschedule_instance_jobs

        reschedule_instance_jobs()
    except Exception as e:
        return JSONResponse(
            {"success": True, "warning": f"Saved, but the scheduler was not updated: {e}"}
        )
    return {"success": True, "schedule": cm.get_instance_schedule()}


@router.post("/api/llm/test")
def api_llm_test(payload: LlmSetupRequest):
    """Dry LLM connection test: one 'ping' completion, never writes
    anything. Same contract as /api/aws-setup/test: failures surface as a
    400 with a user-safe message, never a 500."""
    from core.llm import check_connection

    error = check_connection(payload.model.strip(), payload.api_key or None)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)
    return {"success": True, "model": payload.model.strip()}


@router.post("/api/llm/save")
def api_llm_save(payload: LlmSetupRequest):
    """Test, then persist the AI insights connection to both env files and
    apply it to this process, mirroring /api/aws-setup, so a bad key can
    never be saved silently."""
    from core.llm import MODEL_ENV_VAR, check_connection, key_env_var
    from utils.env_files import apply_to_env, write_env_files

    model = payload.model.strip()
    key_var = key_env_var(model)
    if payload.api_key and not key_var:
        return JSONResponse(
            {
                "success": False,
                "error": "wasteless doesn't know which env var stores the key for this "
                "provider. Add it to .env and ui/.env manually, then save the model alone",
            },
            status_code=400,
        )

    error = check_connection(model, payload.api_key or None)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)

    values = {MODEL_ENV_VAR: model}
    if payload.api_key and key_var:
        values[key_var] = payload.api_key
    write_env_files(values)
    apply_to_env(values)
    return {"success": True, "model": model, "key_saved": bool(payload.api_key and key_var)}


@router.get("/api/policies/export")
def api_policies_export():
    """Download the current remediation policy as versionable YAML."""
    from utils.policies import export_policy_yaml
    from utils.config_manager import ConfigManager

    content = export_policy_yaml(ConfigManager().load_config())
    filename = f"wasteless-policies_{datetime.now().strftime('%Y-%m-%d')}.yaml"
    return PlainTextResponse(
        content,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/policies/import")
def api_policies_import(payload: PolicyImport):
    """Validate and apply a policy YAML document (rejects unknown keys)."""
    from utils.policies import parse_policy_yaml
    from utils.config_manager import ConfigManager, ConfigValidationError

    try:
        config = parse_policy_yaml(payload.yaml_text)
    except ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not ConfigManager().save_config(config):
        raise HTTPException(status_code=500, detail="failed to write the policy file")
    return {"success": True, "sections": sorted(config.keys())}


@router.post("/api/whitelist")
def api_whitelist(instance_id: str, action: Literal["add", "remove"] = "add"):
    """Add or remove instance from whitelist.

    `action` is a closed set: the whitelist is what protects an instance
    from remediation, so an unknown value must be a 422, not a silent
    fall-through to removal (fail-open).
    """
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager()

    try:
        if action == "add":
            success = config_manager.add_instance_to_whitelist(instance_id)
        else:
            success = config_manager.remove_instance_from_whitelist(instance_id)

        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
