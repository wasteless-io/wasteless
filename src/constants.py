#!/usr/bin/python3
import os

NULL_VALUE = "NULL"

# Tous les montants du pipeline sont en USD, la monnaie de facturation AWS
# et des grilles on-demand : aucune conversion dans le code (retiree le
# 2026-07-18), l'utilisateur convertit lui-meme s'il le souhaite. Les
# colonnes historiques *_eur de la base gardent leur nom mais contiennent
# des USD depuis cette date.

# Provenance des tarifs codés en dur dans les détecteurs : prix on-demand
# AWS en USD. Estampillée dans le metadata de chaque détection
# (steampipe_base.save, ec2_idle) pour que chaque chiffre soit traçable
# jusqu'à sa source et sa date de relevé.
# PRICING_AS_OF est la date de la dernière revue des tables statiques ;
# tests/unit/test_pricing_sanity.py échoue quand elle dépasse 180 jours,
# pour forcer une relecture périodique (leçon du 2026-07-18 : t4g absent
# de la table EC2 pendant 6 mois, chaque t4g.micro coûté au défaut 54.35).
PRICING_SOURCE = "aws_on_demand_static"
PRICING_AS_OF = "2026-07-18"
PRICING_CURRENCY = "USD"

# Régions scannées par tout le produit : collecteur CloudWatch, détecteurs
# boto3 (garde-fou tests/unit/test_scan_perimeter.py) et l'UI, dont
# CLOUD_REGIONS (ui/state.py) importe cette liste. Surchargeable via
# AWS_REGIONS (liste séparée par des virgules). Source unique : ne jamais
# recréer de copie locale, c'est ce qui a fait diverger la couverture.
_default_regions = (
    "eu-west-1,eu-west-2,eu-west-3,eu-north-1,us-east-1,sa-east-1,ap-south-1,ap-southeast-2"
)
AWS_SCAN_REGIONS = [
    r.strip() for r in os.getenv("AWS_REGIONS", _default_regions).split(",") if r.strip()
]
