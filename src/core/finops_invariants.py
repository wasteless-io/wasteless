#!/usr/bin/env python3
"""
Invariants FinOps — garde-fous arithmétiques et métier sur les chiffres.

Tout montant destiné à être affiché (dashboard, rapports, claims marketing)
doit passer ces invariants avant publication : un chiffre qu'un CTO peut
démonter en une division décrédibilise tout le produit. Les règles couvrent
les erreurs relevées par l'audit de cohérence : annualisation fausse,
pourcentages impossibles, double comptage d'une ressource, forecast sous le
réalisé, économies « réalisées » sans action exécutée, delete en production
classé low risk, montants sans devise ni période.

Fonctions pures, sans accès base : les appelants (détecteurs, UI, rapports)
fournissent les chiffres et décident quoi faire d'une violation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MONTHS_PER_YEAR = 12
HOURS_PER_MONTH = 730  # convention des détecteurs (src/detectors/ec2_idle.py)

# Actions irréversibles ou interruptrices de service. Alignées sur
# ui/utils/action_registry.py : toute nouvelle action destructive doit
# figurer ici, sinon la règle production ne la couvre pas.
DESTRUCTIVE_ACTIONS = frozenset({
    'delete', 'terminate', 'release', 'drop', 'purge', 'remove', 'destroy',
    'terminate_instance', 'delete_volume', 'delete_snapshot',
    'delete_nat_gateway', 'delete_load_balancer', 'delete_vpc',
    'release_ip',
})

# stop interrompt le service mais est réversible : destructif en production,
# acceptable ailleurs.
SERVICE_INTERRUPTING_ACTIONS = frozenset({'stop', 'stop_instance'})

RISK_LEVELS = ('low', 'medium', 'high', 'critical')

# Champs sans lesquels un montant est invérifiable : plafond low, quel que
# soit le signal d'usage (cf. stamp_pricing qui fournit source et devise).
LOW_CAP_FIELDS = ('currency', 'period')

# Champs dont l'absence dégrade la confiance sans l'écraser à low : la donnée
# reste vérifiable mais incomplète (pas de source de prix, pas de owner à
# notifier, pas d'environnement pour calibrer le risque).
MEDIUM_CAP_FIELDS = ('pricing_source', 'owner', 'environment')

# Fenêtre d'observation minimale avant de qualifier une ressource d'idle.
# Alignée sur le safeguard existant (min_idle_days: 14 en production dans
# config/remediation.yaml) ; le dev tolère une fenêtre plus courte car
# l'impact d'un faux positif y est réversible et sans conséquence business.
MIN_OBSERVATION_DAYS = {'production': 14, 'prod': 14, 'unknown': 14, 'dev': 7}
DEFAULT_MIN_OBSERVATION_DAYS = 14

# Mots interdits dans un claim portant sur du potential savings : ils
# suggèrent une certitude ou une absence de risque qu'aucune donnée
# détectée-mais-non-remédiée ne peut soutenir.
FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS = (
    'guaranteed', 'instantly', 'no risk', 'automatic savings',
)


class FinOpsInvariantError(ValueError):
    """Un chiffre viole un invariant : ne pas le publier tel quel."""


@dataclass
class Violation:
    rule: str
    severity: str            # 'critical' | 'high' | 'medium' | 'low'
    message: str
    context: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Arithmétique de base
# ---------------------------------------------------------------------------

def annualize(monthly: float) -> float:
    """Seule conversion mensuel→annuel autorisée : jamais stocker le chiffre
    annuel indépendamment du mensuel."""
    return monthly * MONTHS_PER_YEAR


def waste_percentage(waste_monthly: float, spend_monthly: float) -> float:
    """% de waste sur le spend, même période et même devise exigées."""
    if spend_monthly <= 0:
        raise FinOpsInvariantError(
            f"Cloud spend must be positive, got {spend_monthly}")
    if waste_monthly < 0:
        raise FinOpsInvariantError(
            f"Detected waste cannot be negative, got {waste_monthly}")
    if waste_monthly > spend_monthly:
        raise FinOpsInvariantError(
            f"Detected waste ({waste_monthly}) exceeds cloud spend "
            f"({spend_monthly}): on ne peut pas gaspiller plus qu'on ne dépense "
            f"— périodes ou devises probablement mélangées")
    return waste_monthly / spend_monthly * 100


def budget_used_percentage(spent: float, budget: float) -> float:
    if budget <= 0:
        raise FinOpsInvariantError(f"Budget must be positive, got {budget}")
    if spent < 0:
        raise FinOpsInvariantError(f"Spend cannot be negative, got {spent}")
    return spent / budget * 100


def validate_forecast(forecast_end_of_month: float,
                      current_spend_mtd: float) -> float:
    """Le forecast fin de mois ne peut pas être sous le déjà-dépensé."""
    if forecast_end_of_month < current_spend_mtd:
        raise FinOpsInvariantError(
            f"Forecast ({forecast_end_of_month}) is below month-to-date spend "
            f"({current_spend_mtd}): impossible, le réalisé ne diminue pas")
    return forecast_end_of_month


def validate_service_breakdown(service_costs: Dict[str, float],
                               total_spend: float,
                               tolerance: float = 0.005) -> float:
    """Vérifie que la ventilation par service boucle sur le total.

    Retourne le montant non ventilé (à afficher en ligne « Other »), lève si
    la somme des services dépasse le total ou si l'écart silencieux excède
    la tolérance (fraction du total).
    """
    breakdown_sum = sum(service_costs.values())
    gap = total_spend - breakdown_sum
    if gap < -abs(total_spend) * tolerance:
        raise FinOpsInvariantError(
            f"Service costs sum ({breakdown_sum}) exceeds total spend "
            f"({total_spend}): double comptage probable")
    if gap > abs(total_spend) * tolerance:
        raise FinOpsInvariantError(
            f"Service breakdown leaves {gap:.2f} unaccounted "
            f"({gap / total_spend * 100:.1f}% of total): ajouter une ligne "
            f"'Other' explicite plutôt qu'un écart silencieux")
    return max(gap, 0.0)


# ---------------------------------------------------------------------------
# Savings : plafonds et déduplication
# ---------------------------------------------------------------------------

def validate_recommendation_saving(saving_monthly: float,
                                   resource_monthly_cost: float) -> float:
    """Une recommandation ne peut pas économiser plus que ne coûte la ressource."""
    if saving_monthly < 0:
        raise FinOpsInvariantError(
            f"Saving cannot be negative, got {saving_monthly}")
    if saving_monthly > resource_monthly_cost:
        raise FinOpsInvariantError(
            f"Saving ({saving_monthly}) exceeds resource cost "
            f"({resource_monthly_cost})")
    return saving_monthly


def validate_potential_vs_detected(potential_savings_monthly: float,
                                   detected_waste_monthly: float) -> float:
    """Le potentiel est plafonné par le waste détecté (contrainte en cascade
    potential ≤ detected ≤ spend)."""
    if potential_savings_monthly > detected_waste_monthly:
        raise FinOpsInvariantError(
            f"Potential savings ({potential_savings_monthly}) exceed detected "
            f"waste ({detected_waste_monthly}): le potentiel ne peut pas "
            f"dépasser ce qui a été détecté")
    return potential_savings_monthly


def deduplicated_total_savings(recommendations: List[Dict[str, Any]]) -> float:
    """Total des savings avec une ressource comptée une seule fois.

    Deux actions sur la même ressource (ex. stop et delete de i-123) sont
    mutuellement exclusives : seule la meilleure compte. Agrégation par
    resource_id au max, jamais par recommandation.
    """
    best_per_resource: Dict[str, float] = {}
    for rec in recommendations:
        rid = rec['resource_id']
        saving = rec.get('potential_saving', rec.get('saving', 0.0))
        best_per_resource[rid] = max(best_per_resource.get(rid, 0.0), saving)
    return sum(best_per_resource.values())


def validate_realized_savings(realized_monthly: float,
                              completed_actions: int) -> float:
    """Aucune économie « réalisée » sans action de remédiation exécutée.

    Afficher du potentiel comme du réalisé est du misreporting : realized
    reste à 0 tant que rien n'a été appliqué et vérifié (savings_realized
    via Cost Explorer).
    """
    if realized_monthly > 0 and completed_actions == 0:
        raise FinOpsInvariantError(
            f"Realized savings ({realized_monthly}) reported with zero "
            f"completed remediation: seuls les montants post-action vérifiés "
            f"sont des économies réalisées")
    return realized_monthly


# ---------------------------------------------------------------------------
# Risque et confiance
# ---------------------------------------------------------------------------

def minimum_risk_for(action: str, environment: Optional[str],
                     owner: Optional[str] = None) -> str:
    """Plancher de risque imposé par l'action, l'environnement et le owner.

    Une action destructive en production est toujours critical — jamais
    éligible à l'auto-remédiation. Un environnement manquant ou inconnu est
    traité comme production (on ne dégrade pas le risque faute
    d'information) : il relève le plancher des actions non destructives à
    medium plutôt que low. L'absence de owner relève aussi le plancher à au
    moins medium — personne à prévenir avant d'agir est un facteur de risque
    en soi, quelle que soit l'action.
    """
    env = (environment or 'unknown').lower()
    action_key = (action or '').lower()
    is_prod_like = env in ('production', 'prod', 'unknown')

    if action_key in DESTRUCTIVE_ACTIONS:
        floor = 'critical' if is_prod_like else 'medium'
    elif action_key in SERVICE_INTERRUPTING_ACTIONS:
        floor = 'high' if is_prod_like else 'low'
    else:
        floor = 'medium' if is_prod_like else 'low'

    if owner is None and RISK_LEVELS.index(floor) < RISK_LEVELS.index('medium'):
        floor = 'medium'

    return floor


def validate_risk_level(action: str, environment: Optional[str],
                        displayed_risk: str,
                        owner: Optional[str] = None) -> str:
    """Lève si le risque affiché est en dessous du plancher requis."""
    floor = minimum_risk_for(action, environment, owner)
    if RISK_LEVELS.index(displayed_risk) < RISK_LEVELS.index(floor):
        raise FinOpsInvariantError(
            f"Risk '{displayed_risk}' for action '{action}' on "
            f"environment '{environment}' (owner={owner!r}) is below "
            f"required floor '{floor}'")
    return displayed_risk


def assess_confidence(metadata: Dict[str, Any],
                      signal_confidence: float = 1.0,
                      observation_days: Optional[int] = None,
                      min_observation_days: Optional[int] = None,
                      metrics_complete: bool = True) -> str:
    """Confiance affichable, plafonnée par la complétude des métadonnées.

    Un montant sans devise ou période est invérifiable : low d'office, quel
    que soit le signal. Une source de pricing, un owner ou un environnement
    manquant, une fenêtre d'observation trop courte, ou des métriques
    d'usage incomplètes dégradent la confiance à medium au maximum — la
    donnée reste vérifiable mais incomplète, pas invérifiable.
    """
    if any(not metadata.get(k) for k in LOW_CAP_FIELDS):
        return 'low'

    medium_capped = any(not metadata.get(k) for k in MEDIUM_CAP_FIELDS)
    medium_capped = medium_capped or not metrics_complete
    if (observation_days is not None and min_observation_days is not None
            and observation_days < min_observation_days):
        medium_capped = True

    if medium_capped:
        return 'medium' if signal_confidence >= 0.5 else 'low'
    if signal_confidence >= 0.8:
        return 'high'
    if signal_confidence >= 0.5:
        return 'medium'
    return 'low'


# ---------------------------------------------------------------------------
# Fenêtre d'observation et classification idle
# ---------------------------------------------------------------------------

def minimum_observation_days(environment: Optional[str]) -> int:
    env = (environment or 'unknown').lower()
    return MIN_OBSERVATION_DAYS.get(env, DEFAULT_MIN_OBSERVATION_DAYS)


def validate_observation_window(environment: Optional[str],
                                observation_days: float,
                                action: str) -> float:
    """Une action destructive ou interruptrice de service exige une fenêtre
    d'observation suffisante : 24h (voire un week-end) ne distingue pas une
    ressource réellement idle d'une charge ponctuellement calme.
    """
    action_key = (action or '').lower()
    if action_key not in DESTRUCTIVE_ACTIONS | SERVICE_INTERRUPTING_ACTIONS:
        return observation_days

    required = minimum_observation_days(environment)
    if observation_days < required:
        raise FinOpsInvariantError(
            f"Observation window ({observation_days}d) is too short for "
            f"action '{action}' on environment '{environment}': minimum "
            f"{required}d required")
    return observation_days


def validate_batch_workload_classification(is_batch_workload: bool,
                                           has_schedule_context: bool,
                                           action: str) -> bool:
    """Un workload batch (job périodique) ne doit pas être marqué idle sans
    contexte de planification : une absence d'activité pendant la fenêtre
    d'observation peut simplement tomber entre deux exécutions.
    """
    action_key = (action or '').lower()
    is_actionable = action_key in DESTRUCTIVE_ACTIONS | SERVICE_INTERRUPTING_ACTIONS
    if is_batch_workload and is_actionable and not has_schedule_context:
        raise FinOpsInvariantError(
            f"Batch workload flagged for '{action}' without schedule "
            f"context: la fenêtre d'observation peut tomber entre deux "
            f"exécutions planifiées")
    return True


# ---------------------------------------------------------------------------
# Réalisme du pricing AWS
# ---------------------------------------------------------------------------

def validate_cost_within_tolerance(actual: float, expected: float,
                                   tolerance_pct: float = 0.05,
                                   label: str = 'cost') -> float:
    if expected <= 0:
        raise FinOpsInvariantError(f"Expected {label} must be positive")
    deviation = abs(actual - expected) / expected
    if deviation > tolerance_pct:
        raise FinOpsInvariantError(
            f"{label} {actual} deviates {deviation * 100:.1f}% from expected "
            f"{expected:.2f} (tolerance {tolerance_pct * 100:.0f}%)")
    return actual


def validate_ec2_cost(instance_type: str, hours_running: float,
                      monthly_cost: float, pricing_table: Dict[str, float],
                      hours_per_month: float = HOURS_PER_MONTH,
                      tolerance_pct: float = 0.05) -> float:
    """Le coût affiché doit correspondre au tarif on-demand du type
    d'instance, proraté aux heures réellement tournées sur le mois."""
    if instance_type not in pricing_table:
        raise FinOpsInvariantError(
            f"Unknown instance type '{instance_type}': cannot validate cost")
    full_month_price = pricing_table[instance_type]
    expected = full_month_price * (hours_running / hours_per_month)
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label=f'EC2 {instance_type}')


def validate_ebs_cost(size_gb: float, volume_type: str, monthly_cost: float,
                      pricing_eur_per_gib: Dict[str, float],
                      tolerance_pct: float = 0.05) -> float:
    if volume_type not in pricing_eur_per_gib:
        raise FinOpsInvariantError(
            f"Unknown EBS volume type '{volume_type}': cannot validate cost")
    expected = size_gb * pricing_eur_per_gib[volume_type]
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label=f'EBS {volume_type}')


def validate_elastic_ip_cost(monthly_cost: float, known_eip_cost: float,
                             tolerance_pct: float = 0.10) -> float:
    """Une Elastic IP inutilisée coûte quelques euros/mois — un montant à
    deux chiffres (ex. 40 €/mois) est suspect et doit être rejeté."""
    return validate_cost_within_tolerance(
        monthly_cost, known_eip_cost, tolerance_pct, label='Elastic IP')


def validate_nat_gateway_cost(monthly_cost: float, base_hourly_cost: float,
                              data_processed_gb: float,
                              data_processing_rate_eur_per_gb: float,
                              tolerance_pct: float = 0.05) -> float:
    """Le coût d'une NAT Gateway doit inclure la composante horaire ET le
    data processing — un chiffre qui ignore le trafic sous-estime le coût
    réel dès que la ressource traite des données."""
    expected = base_hourly_cost + data_processed_gb * data_processing_rate_eur_per_gb
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label='NAT Gateway')


def validate_rds_cost(instance_class: str, storage_gb: float,
                      monthly_cost: float,
                      instance_pricing_table: Dict[str, float],
                      storage_rate_eur_per_gb: float,
                      tolerance_pct: float = 0.05) -> float:
    """Le coût RDS doit couvrir l'instance ET le stockage — les deux
    composantes de la facture, pas seulement le compute."""
    if instance_class not in instance_pricing_table:
        raise FinOpsInvariantError(
            f"Unknown RDS instance class '{instance_class}': cannot "
            f"validate cost")
    expected = (instance_pricing_table[instance_class]
                + storage_gb * storage_rate_eur_per_gb)
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label=f'RDS {instance_class}')


# ---------------------------------------------------------------------------
# Recommandations dangereuses
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Forecast avancé
# ---------------------------------------------------------------------------

def linear_forecast(current_spend: float, days_elapsed: float,
                    days_in_month: float) -> float:
    """Forecast fin de mois par burn rate linéaire — recalculable par
    n'importe qui à partir de trois nombres publiés."""
    if days_elapsed <= 0 or days_in_month <= 0:
        raise FinOpsInvariantError(
            "days_elapsed and days_in_month must be positive")
    if days_elapsed > days_in_month:
        raise FinOpsInvariantError(
            "days_elapsed cannot exceed days_in_month")
    return current_spend / days_elapsed * days_in_month


def forecast_after_remediation(forecast: float,
                               validated_low_risk_savings: float,
                               current_spend_mtd: float) -> float:
    """Le forecast après remédiation ne soustrait que les savings validés
    (low-risk, approuvés) — jamais un potentiel non qualifié — et ne peut
    jamais tomber sous le spend déjà réalisé ce mois-ci."""
    result = forecast - validated_low_risk_savings
    return validate_forecast(result, current_spend_mtd)


def flags_budget_overrun(forecast: float, budget: float) -> bool:
    return forecast > budget


# ---------------------------------------------------------------------------
# Claims publics
# ---------------------------------------------------------------------------

def validate_claim_percentage(claimed_pct: float,
                              detected_waste_monthly: float,
                              spend_monthly: float,
                              tolerance_pts: float = 1.0) -> float:
    """Un pourcentage annoncé doit être recalculable depuis les données.

    Tout claim au-dessus du waste réellement détecté (à la tolérance près,
    en points) est indéfendable devant un CTO.
    """
    actual_pct = waste_percentage(detected_waste_monthly, spend_monthly)
    if claimed_pct > actual_pct + tolerance_pts:
        raise FinOpsInvariantError(
            f"Claimed {claimed_pct}% but data supports {actual_pct:.1f}%: "
            f"claim {claimed_pct / actual_pct:.1f}x above evidence")
    return claimed_pct


def validate_claim_wording(text: str, savings_type: str,
                          completed_actions: int = 0) -> str:
    """« guaranteed », « instantly », « no risk » sont interdits sur du
    potential savings : ils promettent une certitude qu'un chiffre non
    remédié ne peut pas soutenir. « realized »/« guaranteed » n'importe où
    exige des actions effectivement exécutées."""
    lowered = text.lower()

    if savings_type != 'realized':
        hits = [w for w in FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS if w in lowered]
        if hits:
            raise FinOpsInvariantError(
                f"Forbidden wording {hits} in a '{savings_type}' savings "
                f"claim: {text!r}")

    if ('realized' in lowered or 'guaranteed' in lowered) and completed_actions == 0:
        raise FinOpsInvariantError(
            f"Claim implies certainty ('realized'/'guaranteed') with zero "
            f"completed remediation actions: {text!r}")

    return text


def validate_annualized_claim(annual_value: float,
                              monthly_basis: Optional[float]) -> float:
    """Un claim annualisé doit exhiber sa base mensuelle et en être le
    produit exact par 12 — jamais un chiffre annuel stocké seul."""
    if monthly_basis is None:
        raise FinOpsInvariantError(
            "Annualized claim has no explicit monthly basis")
    expected = annualize(monthly_basis)
    if abs(annual_value - expected) > 0.01:
        raise FinOpsInvariantError(
            f"Annualized claim ({annual_value}) != monthly basis "
            f"({monthly_basis}) x 12 ({expected})")
    return annual_value


def validate_up_to_claim(claimed_value: float, dataset_max_value: float) -> float:
    """Un claim « up to X » ne peut pas dépasser le maximum que le dataset
    supporte réellement."""
    if claimed_value > dataset_max_value:
        raise FinOpsInvariantError(
            f"'Up to {claimed_value}' claim exceeds dataset-supported "
            f"maximum ({dataset_max_value})")
    return claimed_value


def validate_low_risk_claim(claimed_savings: float,
                            recommendations: List[Dict[str, Any]]) -> float:
    """Un claim « low-risk savings » ne doit sommer que les recommandations
    effectivement classées low risk — pas l'ensemble du potentiel."""
    low_risk_total = sum(
        r.get('potential_saving', r.get('saving', 0.0))
        for r in recommendations if r.get('risk') == 'low')
    if claimed_savings > low_risk_total + 0.01:
        raise FinOpsInvariantError(
            f"Low-risk claim ({claimed_savings}) exceeds sum of low-risk "
            f"recommendations ({low_risk_total})")
    return claimed_savings


def generate_cto_safe_summary(dataset: Dict[str, Any]) -> str:
    """Résumé textuel distinguant detected / potential / annualized /
    realized / confirmed — le format validé pour toute communication
    chiffrée (voir memory: feedback-cto-safe-formulation)."""
    spend = dataset.get('cloud_spend_monthly', 0.0)
    waste = dataset.get('detected_waste_monthly', 0.0)
    potential = dataset.get('potential_savings_monthly', waste)
    realized = dataset.get('realized_savings_monthly', 0.0)
    confirmed = dataset.get('confirmed_savings_monthly', 0.0)
    pct = waste_percentage(waste, spend) if spend else 0.0
    return (
        f"Detected waste: {waste:.0f}/mo ({pct:.1f}% of {spend:.0f} spend). "
        f"Potential savings: up to {potential:.0f}/mo. "
        f"Annualized potential: up to {annualize(potential):.0f}/yr. "
        f"Realized savings: {realized:.0f} (requires completed actions). "
        f"Confirmed savings: {confirmed:.0f} (verified via Cost Explorer)."
    )


# ---------------------------------------------------------------------------
# Audit d'un dataset complet
# ---------------------------------------------------------------------------

def audit_dataset(dataset: Dict[str, Any]) -> List[Violation]:
    """Passe un dataset dashboard complet au crible de tous les invariants.

    Ne lève pas : retourne la liste des violations classées par sévérité,
    pour affichage ou blocage en amont de la publication. Les clés attendues
    suivent le format de l'exercice 20 de l'audit (cloud_spend_monthly,
    detected_waste_monthly, potential_savings_monthly/yearly,
    reduction_percentage, budget_monthly, budget_used_percentage,
    forecast_end_of_month, services, recommendations).
    """
    violations: List[Violation] = []

    def _check(rule: str, severity: str, fn) -> None:
        try:
            fn()
        except FinOpsInvariantError as e:
            violations.append(Violation(rule, severity, str(e)))

    spend = dataset.get('cloud_spend_monthly')
    waste = dataset.get('detected_waste_monthly')
    potential = dataset.get('potential_savings_monthly')
    yearly = dataset.get('potential_savings_yearly')
    reduction = dataset.get('reduction_percentage')
    budget = dataset.get('budget_monthly')
    budget_used = dataset.get('budget_used_percentage')
    forecast = dataset.get('forecast_end_of_month')
    services = dataset.get('services') or []
    recommendations = dataset.get('recommendations') or []

    if not dataset.get('currency'):
        violations.append(Violation(
            'missing_currency', 'high',
            "Dataset has no currency: tous les montants sont invérifiables"))

    if spend is not None and waste is not None:
        _check('waste_within_spend', 'critical',
               lambda: waste_percentage(waste, spend))

    if waste is not None and potential is not None:
        _check('potential_within_detected', 'critical',
               lambda: validate_potential_vs_detected(potential, waste))

    if potential is not None and yearly is not None:
        expected = annualize(potential)
        if abs(yearly - expected) > 0.01:
            violations.append(Violation(
                'yearly_is_monthly_x12', 'high',
                f"potential_savings_yearly ({yearly}) != monthly x 12 "
                f"({expected}): le chiffre annuel doit être calculé, "
                f"jamais stocké indépendamment"))

    if reduction is not None and spend and potential is not None:
        actual = potential / spend * 100
        if abs(reduction - actual) > 1.0:
            violations.append(Violation(
                'reduction_recomputable', 'high',
                f"reduction_percentage ({reduction}%) not recomputable: "
                f"potential/spend = {actual:.1f}%"))

    if forecast is not None and spend is not None:
        _check('forecast_not_below_spend', 'critical',
               lambda: validate_forecast(forecast, spend))

    if budget is not None and spend is not None and budget_used is not None:
        actual = budget_used_percentage(spend, budget)
        if abs(budget_used - actual) > 0.5:
            violations.append(Violation(
                'budget_used_recomputable', 'medium',
                f"budget_used_percentage ({budget_used}%) != spend/budget "
                f"({actual:.1f}%)"))

    if services and spend is not None:
        costs = {s['name']: s['monthly_cost'] for s in services}
        _check('service_sum_matches_total', 'medium',
               lambda: validate_service_breakdown(costs, spend))

    seen_resources = set()
    for rec in recommendations:
        rid = rec.get('resource_id', '?')
        if rid in seen_resources:
            violations.append(Violation(
                'duplicate_resource', 'high',
                f"Resource {rid} has multiple recommendations: actions "
                f"mutuellement exclusives, dédupliquer les savings au max "
                f"par ressource",
                context={'resource_id': rid}))
        seen_resources.add(rid)

        _check('saving_within_resource_cost', 'high',
               lambda r=rec: validate_recommendation_saving(
                   r.get('potential_saving', 0.0), r.get('monthly_cost', 0.0)))
        _check('risk_floor', 'critical',
               lambda r=rec: validate_risk_level(
                   r.get('action', ''), r.get('environment'),
                   r.get('risk', 'low'), r.get('owner')))

    severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    violations.sort(key=lambda v: severity_order[v.severity])
    return violations
