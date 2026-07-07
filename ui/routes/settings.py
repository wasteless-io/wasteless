"""Settings page: config editing, whitelist, policy-as-code export/import."""

from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from state import get_db, templates
from schemas import ConfigUpdate, PolicyImport

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings(request: Request, conn=Depends(get_db)):
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

    return templates.TemplateResponse(
        request,
        "settings.html",
        context={"config": config, "stats": stats, "automatable_actions": automatable_actions},
    )


@router.post("/api/config")
async def api_update_config(update: ConfigUpdate):
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
        raise HTTPException(status_code=400, detail=str(e))


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
        raise HTTPException(status_code=400, detail=str(e))

    if not ConfigManager().save_config(config):
        raise HTTPException(status_code=500, detail="failed to write the policy file")
    return {"success": True, "sections": sorted(config.keys())}


@router.post("/api/whitelist")
async def api_whitelist(instance_id: str, action: str = "add"):
    """Add or remove instance from whitelist."""
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager()

    try:
        if action == "add":
            success = config_manager.add_instance_to_whitelist(instance_id)
        else:
            success = config_manager.remove_instance_from_whitelist(instance_id)

        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
