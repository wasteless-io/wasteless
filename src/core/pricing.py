#!/usr/bin/env python3
"""
Provenance tarifaire des détections.

Chaque montant USD écrit dans waste_detected doit être traçable jusqu'à sa
source de prix et sa date de relevé, sans quoi le chiffre est indéfendable
devant un CTO. Les save() des détecteurs passent leur metadata par
stamp_pricing() avant insertion.
"""

from typing import Any, Dict

from constants import PRICING_AS_OF, PRICING_CURRENCY, PRICING_SOURCE


def stamp_pricing(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Ajoute la provenance tarifaire ; les clés du détecteur priment."""
    return {
        "pricing_source": PRICING_SOURCE,
        "pricing_as_of": PRICING_AS_OF,
        "pricing_currency": PRICING_CURRENCY,
        **metadata,
    }
