#!/usr/bin/env python3
"""
Terraform PR Integration for Wasteless UI
=========================================

Bridges the approval flow (/api/actions) and the background scheduler to
the backend Terraform PR remediator (src/remediators/terraform_pr.py):

  - maybe_open_pr(): called on approval; when the recommendation matches
    the terraform_pr routing policy, opens (or dry-runs) a remediation PR
    instead of touching AWS. Returns None when not routed, so the caller
    continues with the normal remediation path.
  - sync_open_prs(): called by the scheduler; a merged PR marks the
    recommendation approved, a closed one marks it rejected.

Author: Wasteless Team
"""

import json
import logging
import os
import subprocess
from typing import Dict, Optional

from psycopg2.extras import Json

from utils.remediator import BACKEND_PATH, _backend_context

logger = logging.getLogger(__name__)


def _load_backend_config():
    """Load the backend TerraformPRConfig from config/remediation.yaml."""
    with _backend_context():
        from src.core.config import RemediationConfig
        config_path = os.path.join(BACKEND_PATH, 'config', 'remediation.yaml')
        return RemediationConfig.from_yaml(config_path).terraform_pr


def maybe_open_pr(conn, rec_id: int, row: Dict, dry_run: bool) -> Optional[Dict]:
    """
    Route an approved recommendation through the Terraform PR flow when
    the policy requires it.

    Args:
        conn: DB connection (RealDictCursor)
        rec_id: recommendation id
        row: joined recommendations/waste_detected row with resource_id,
             resource_type, recommendation_type, action_required,
             estimated_monthly_savings_eur, confidence_score
        dry_run: global dry-run flag (Settings)

    Returns None when the recommendation is not routed to a PR (caller
    proceeds with the API path), otherwise a result dict for /api/actions.
    """
    try:
        config = _load_backend_config()
    except Exception as e:
        logger.warning(f"terraform_pr config unavailable: {e}")
        return None

    savings = float(row.get('estimated_monthly_savings_eur') or 0)
    if not config.requires_pr(row['resource_type'], savings):
        return None

    from src.remediators.terraform_pr import (
        TerraformPRError, TerraformPRRemediator
    )

    result = {
        'recommendation_id': rec_id,
        'instance_id': row['resource_id'],
        'action': row['recommendation_type'],
        'terraform_pr': True,
        'dry_run': dry_run,
        'success': False,
    }
    try:
        with _backend_context():
            remediator = TerraformPRRemediator(config, dry_run=dry_run)
            proposal = remediator.propose_removal(
                resource_id=row['resource_id'],
                resource_label=row['resource_type'],
                monthly_savings_eur=savings,
                confidence=float(row.get('confidence_score') or 0),
                reason=row.get('action_required') or '',
            )
    except TerraformPRError as e:
        # Unsafe to automate (dangling refs, validate failure...): surface
        # the reason, do NOT silently fall back to the API path
        result['error'] = str(e)
        return result

    if proposal is None:
        # Not managed by Terraform: the API remediation path applies
        logger.info(f"{row['resource_id']}: not Terraform-managed, "
                    f"falling back to API remediation")
        return None

    result['success'] = True
    cursor = conn.cursor()
    if proposal.pr_url:
        result['pr_url'] = proposal.pr_url
        cursor.execute("""
            UPDATE recommendations
            SET status = 'pr_open', pr_url = %s
            WHERE id = %s
        """, (proposal.pr_url, rec_id))
    cursor.execute("""
        INSERT INTO actions_log
        (resource_id, recommendation_id, resource_type, action_type,
         action_status, dry_run, action_date, metadata)
        VALUES (%s, %s, %s, 'terraform_pr', %s, %s, NOW(), %s)
    """, (row['resource_id'], rec_id, row['resource_type'],
          'success', dry_run,
          Json({'pr_url': proposal.pr_url, 'branch': proposal.branch,
                'address': proposal.address})))
    cursor.close()
    return result


def sync_open_prs(conn) -> int:
    """
    Reconcile 'pr_open' recommendations with GitHub: merged -> approved,
    closed without merge -> rejected. Returns the number updated.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, pr_url FROM recommendations
        WHERE status = 'pr_open' AND pr_url IS NOT NULL
    """)
    open_prs = cursor.fetchall()
    updated = 0

    for rec in open_prs:
        try:
            result = subprocess.run(
                ['gh', 'pr', 'view', rec['pr_url'], '--json', 'state'],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.warning(f"gh pr view failed for {rec['pr_url']}: "
                               f"{result.stderr.strip()}")
                continue
            state = json.loads(result.stdout).get('state')
        except Exception as e:
            logger.warning(f"PR sync error for {rec['pr_url']}: {e}")
            continue

        if state == 'MERGED':
            new_status = 'approved'
        elif state == 'CLOSED':
            new_status = 'rejected'
        else:
            continue  # still OPEN

        cursor.execute("""
            UPDATE recommendations
            SET status = %s, applied_at = NOW()
            WHERE id = %s AND status = 'pr_open'
        """, (new_status, rec['id']))
        updated += cursor.rowcount
        logger.info(f"PR {rec['pr_url']} {state} -> recommendation "
                    f"{rec['id']} {new_status}")

    cursor.close()
    return updated
