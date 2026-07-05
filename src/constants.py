#!/usr/bin/python3
import os

NULL_VALUE = 'NULL'

# Taux de conversion unique pour tout le pipeline (détecteurs, tracker,
# briefing). L'UI lit la même variable d'environnement (ui/main.py) :
# changer USD_TO_EUR garde waste et savings dans la même monnaie partout.
USD_TO_EUR = float(os.getenv('USD_TO_EUR', '0.92'))
