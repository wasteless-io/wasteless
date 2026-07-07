"""
Shared constants, exceptions and dataclasses for finops_invariants/.

Split out of what used to be a single 969-line finops_invariants.py so each
thematic module (arithmetic, savings, risk, pricing, claims, audit...) can
import just what it needs. See finops_invariants/__init__.py for the
re-exported public API — nothing outside this package should import from
these submodules directly.
"""

MONTHS_PER_YEAR = 12
HOURS_PER_MONTH = 730  # convention des détecteurs (src/detectors/ec2_idle.py)

# Actions irréversibles ou interruptrices de service. Alignées sur
# ui/utils/action_registry.py : toute nouvelle action destructive doit
# figurer ici, sinon la règle production ne la couvre pas.
DESTRUCTIVE_ACTIONS = frozenset(
    {
        "delete",
        "terminate",
        "release",
        "drop",
        "purge",
        "remove",
        "destroy",
        "terminate_instance",
        "delete_volume",
        "delete_snapshot",
        "delete_nat_gateway",
        "delete_load_balancer",
        "delete_vpc",
        "release_ip",
    }
)

# stop interrompt le service mais est réversible : destructif en production,
# acceptable ailleurs.
SERVICE_INTERRUPTING_ACTIONS = frozenset({"stop", "stop_instance"})

RISK_LEVELS = ("low", "medium", "high", "critical")

# Champs sans lesquels un montant est invérifiable : plafond low, quel que
# soit le signal d'usage (cf. stamp_pricing qui fournit source et devise).
LOW_CAP_FIELDS = ("currency", "period")

# Champs dont l'absence dégrade la confiance sans l'écraser à low : la donnée
# reste vérifiable mais incomplète (pas de source de prix, pas de owner à
# notifier, pas d'environnement pour calibrer le risque).
MEDIUM_CAP_FIELDS = ("pricing_source", "owner", "environment")

# Fenêtre d'observation minimale avant de qualifier une ressource d'idle.
# Alignée sur le safeguard existant (min_idle_days: 14 en production dans
# config/remediation.yaml) ; le dev tolère une fenêtre plus courte car
# l'impact d'un faux positif y est réversible et sans conséquence business.
MIN_OBSERVATION_DAYS = {"production": 14, "prod": 14, "unknown": 14, "dev": 7}
DEFAULT_MIN_OBSERVATION_DAYS = 14

# Mots interdits dans un claim portant sur du potential savings : ils
# suggèrent une certitude ou une absence de risque qu'aucune donnée
# détectée-mais-non-remédiée ne peut soutenir.
FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS = (
    "guaranteed",
    "instantly",
    "no risk",
    "automatic savings",
)


class FinOpsInvariantError(ValueError):
    """Un chiffre viole un invariant : ne pas le publier tel quel."""


from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Violation:
    rule: str
    severity: str  # 'critical' | 'high' | 'medium' | 'low'
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
