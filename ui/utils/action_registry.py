"""
Single source of truth: how each recommendation type is executed when
approved from the UI.

Modes:
- 'boto3':      direct AWS call in ui/main.py (EC2 stop/terminate only)
- 'remediator': backend remediator (safeguards + rollback snapshot +
                live waste re-verification)
- 'manual':     approving records the human decision; execution stays
                manual (nothing touches AWS)

At runtime an undeclared type falls back to 'manual' — the safe default.
But the guard test (ui/tests/test_action_registry.py) fails if a detector
introduces a recommendation type absent from this dict: adding a detector
means consciously declaring its execution mode here.
"""

EXECUTION_MODES = {
    # EC2 — automated directly via boto3 in ui/main.py
    'stop_instance':        'boto3',
    'terminate_instance':   'boto3',
    # Backend remediators (src/remediators/resource_remediator.py)
    'migrate_gp2_to_gp3':   'remediator',
    'delete_volume':        'remediator',  # snapshot-first: rollback real
    'delete_nat_gateway':   'remediator',
    'delete_load_balancer': 'remediator',
    # Manual review — approval is a decision, execution is up to the human
    'downsize_instance':    'manual',
    'delete_snapshot':      'manual',
    'release_ip':           'manual',
    'delete_vpc':           'manual',
}


def execution_mode(recommendation_type: str) -> str:
    """Return 'boto3', 'remediator' or 'manual' (safe default)."""
    return EXECUTION_MODES.get(recommendation_type, 'manual')
