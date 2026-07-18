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
PRICING_SOURCE = "aws_on_demand_static"
PRICING_AS_OF = "2026-01-11"
PRICING_CURRENCY = "USD"

# Régions scannées par le pipeline (collecteur CloudWatch, détecteurs qui
# interrogent AWS directement). Surchargeables via AWS_REGIONS (liste
# séparée par des virgules) ; le défaut reprend les régions déjà couvertes
# par ec2_stopped et la page Cloud Resources (CLOUD_REGIONS dans ui/state.py)
# pour que tout le produit regarde le même périmètre.
_default_regions = "eu-west-1,eu-west-2,eu-west-3,us-east-1"
AWS_SCAN_REGIONS = [
    r.strip() for r in os.getenv("AWS_REGIONS", _default_regions).split(",") if r.strip()
]
