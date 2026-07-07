#!/usr/bin/python3
import os

NULL_VALUE = "NULL"

# Taux de conversion unique pour tout le pipeline (détecteurs, tracker,
# briefing). L'UI lit la même variable d'environnement (ui/main.py) :
# changer USD_TO_EUR garde waste et savings dans la même monnaie partout.
USD_TO_EUR = float(os.getenv("USD_TO_EUR", "0.92"))

# Provenance des tarifs codés en dur dans les détecteurs : prix on-demand
# AWS convertis via USD_TO_EUR. Estampillée dans le metadata de chaque
# détection (steampipe_base.save, ec2_idle) pour que chaque chiffre EUR
# soit traçable jusqu'à sa source et sa date de relevé.
PRICING_SOURCE = "aws_on_demand_static"
PRICING_AS_OF = "2026-01-11"
