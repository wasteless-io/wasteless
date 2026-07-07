"""
Guards against recommendations that are technically detected but unsafe to act on as-is (e.g. an EBS delete without a snapshot, an EKS nodegroup resize without capacity checked).
"""

from typing import Any, Dict, Optional
from ._shared import FinOpsInvariantError

def validate_underutilization_action(detector_type: str, resource_type: str,
                                     action: str) -> str:
    """Un détecteur de sous-utilisation (idle/underutilized) ne doit jamais
    recommander delete sur une base de données : un CPU bas ne prouve pas
    l'absence de valeur (réplique standby, charge mémoire/IOPS-bound)."""
    detector_key = (detector_type or '').lower()
    if (resource_type == 'rds' and action == 'delete'
            and ('underutil' in detector_key or 'idle' in detector_key)):
        raise FinOpsInvariantError(
            f"Detector '{detector_type}' must not recommend 'delete' on "
            f"RDS from underutilization signal alone")
    return action


def validate_ebs_delete(has_snapshot: bool, has_retention_policy: bool) -> bool:
    if not has_snapshot and not has_retention_policy:
        raise FinOpsInvariantError(
            "EBS delete requires a snapshot or a retention policy: "
            "irréversible sans l'un des deux")
    return True


def validate_nat_gateway_delete(route_tables_validated: bool) -> bool:
    if not route_tables_validated:
        raise FinOpsInvariantError(
            "NAT Gateway delete requires route table validation: "
            "supprimer sans vérifier les routes peut couper du trafic "
            "sortant légitime")
    return True


def validate_cloudwatch_retention_saving(claimed_saving_monthly: float,
                                         storage_cost_monthly: float,
                                         total_log_cost_monthly: float) -> float:
    """Un changement de rétention CloudWatch réduit le coût de stockage des
    logs, jamais le coût d'ingestion : le saving ne peut donc pas dépasser
    la part stockage, ni a fortiori le coût total des logs."""
    if claimed_saving_monthly > storage_cost_monthly:
        raise FinOpsInvariantError(
            f"Claimed saving ({claimed_saving_monthly}) exceeds the storage "
            f"portion of CloudWatch cost ({storage_cost_monthly}): a "
            f"retention change only reduces storage, not ingestion")
    if claimed_saving_monthly >= total_log_cost_monthly:
        raise FinOpsInvariantError(
            f"Claimed saving ({claimed_saving_monthly}) cannot equal or "
            f"exceed total log cost ({total_log_cost_monthly})")
    return claimed_saving_monthly


def validate_eks_resize(nodegroup: Optional[str],
                        workload_metrics: Optional[Dict[str, Any]]) -> bool:
    """Un resize EKS doit s'appuyer sur le nodegroup ciblé et des métriques
    de workload (CPU, mémoire) — pas seulement un agrégat cluster qui
    ignorerait la capacité minimale requise."""
    if not nodegroup:
        raise FinOpsInvariantError(
            "EKS resize requires a target nodegroup")
    required_metrics = ('cpu', 'memory')
    missing = [m for m in required_metrics
               if not workload_metrics or m not in workload_metrics]
    if missing:
        raise FinOpsInvariantError(
            f"EKS resize requires workload metrics: missing {missing}")
    return True
