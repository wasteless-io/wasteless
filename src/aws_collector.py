#!/usr/bin/env python3
"""
AWS Cost Collector - Collecte automatique des coûts AWS via Cost Explorer API

Ce module permet de:
- Récupérer les coûts AWS des N derniers jours
- Sauvegarder les données dans PostgreSQL
- Exporter en CSV pour analyse
- Afficher des statistiques de collecte

Usage:
    python aws_collector.py

Pré-requis:
    - Variables d'environnement AWS configurées dans .env
    - Base de données PostgreSQL accessible
    - Accès Cost Explorer activé sur le compte AWS
"""

import pandas as pd
from psycopg2.extras import execute_values
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import sys

from core.aws_clients import get_client
from core.database import get_db_connection, release_connection


class AWSCostCollector:
    """
    Collecteur de coûts AWS via l'API Cost Explorer.

    Cette classe gère la récupération des données de coûts AWS,
    leur traitement et leur sauvegarde en base de données.

    Attributes:
        ce_client: Client boto3 pour Cost Explorer API
    """

    def __init__(self):
        """
        Initialise le collecteur AWS.

        Charge les variables d'environnement et configure le client AWS.
        Vérifie que toutes les variables requises sont présentes.

        Raises:
            SystemExit: Si des variables d'environnement sont manquantes
                       ou si la connexion AWS échoue
        """
        # Charger les variables d'environnement depuis .env
        load_dotenv()

        # Vérifier que toutes les variables AWS requises sont présentes
        # (les credentials viennent de la factory : AssumeRole ou chaîne par défaut)
        required_vars = ["AWS_REGION"]
        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            print(f"❌ Variables d'environnement manquantes: {', '.join(missing_vars)}")
            print("💡 Vérifiez votre fichier .env")
            sys.exit(1)

        # Initialiser le client Cost Explorer AWS
        try:
            self.ce_client = get_client("ce", region=os.getenv("AWS_REGION"))
            print("✅ Connexion AWS Cost Explorer établie")
        except Exception as e:
            print(f"❌ Erreur de connexion AWS: {e}")
            print("💡 Vérifiez vos credentials AWS et l'accès Cost Explorer")
            sys.exit(1)

    def get_costs_last_n_days(self, days=30):
        """
        Récupère les coûts AWS des N derniers jours.

        Utilise l'API Cost Explorer pour obtenir les coûts quotidiens
        groupés par service AWS.

        Args:
            days (int): Nombre de jours à récupérer (défaut: 30)

        Returns:
            pandas.DataFrame: DataFrame contenant les données de coûts avec colonnes:
                - date: Date de la consommation
                - service: Nom du service AWS
                - cost_usd: Coût en USD
                - usage: Quantité d'usage

        Note:
            Les coûts inférieurs à 0.01$ sont ignorés pour éviter le bruit
        """
        # Calculer les dates de début et fin
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        print(f"\n📊 Collecte des coûts: {start_date} → {end_date} ({days} jours)")

        try:
            # Traitement des résultats de l'API
            costs_data = []

            # Cost Explorer pagine les gros résultats via NextPageToken : un an
            # de granularité quotidienne × services dépasse une page, et sans
            # cette boucle le backfill serait tronqué en silence (seule la 1re
            # page insérée). On suit le token jusqu'à épuisement.
            next_token = None
            while True:
                params = {
                    "TimePeriod": {"Start": str(start_date), "End": str(end_date)},
                    "Granularity": "DAILY",  # Granularité quotidienne
                    "Metrics": ["UnblendedCost", "UsageQuantity"],  # Coût et usage
                    "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],  # par service
                }
                if next_token:
                    params["NextPageToken"] = next_token
                response = self.ce_client.get_cost_and_usage(**params)

                # Parcourir chaque jour dans la page courante
                for daily_result in response["ResultsByTime"]:
                    date = daily_result["TimePeriod"]["Start"]

                    # Parcourir chaque service pour ce jour
                    for group in daily_result["Groups"]:
                        service_name = group["Keys"][0]
                        cost_amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                        usage_quantity = float(group["Metrics"]["UsageQuantity"]["Amount"])

                        # Filtrer les coûts négligeables (< 1 centime)
                        if cost_amount > 0.01:
                            costs_data.append(
                                {
                                    "date": date,
                                    "service": service_name,
                                    "cost_usd": cost_amount,
                                    "usage": usage_quantity,
                                }
                            )

                next_token = response.get("NextPageToken")
                if not next_token:
                    break

            # Créer le DataFrame pandas
            df = pd.DataFrame(costs_data)

            # Vérifier si des données ont été collectées
            if len(df) == 0:
                print("⚠️  Aucune donnée de coût trouvée pour cette période")
                return df

            # Afficher les statistiques de collecte
            self._display_collection_stats(df)

            return df

        except Exception as e:
            print(f"❌ Erreur lors de la collecte des coûts: {e}")
            return pd.DataFrame()

    def _display_collection_stats(self, df):
        """
        Affiche les statistiques de la collecte de données.

        Args:
            df (pandas.DataFrame): DataFrame des coûts collectés
        """
        total_cost = df["cost_usd"].sum()
        unique_services = df["service"].nunique()

        print(f"✅ {len(df)} enregistrements collectés")
        print(f"💰 Coût total: ${total_cost:,.2f}")
        print(f"📦 Services uniques: {unique_services}")

        # Afficher le top 5 des services les plus coûteux
        print("\n📈 Top 5 des services les plus coûteux:")
        top_services = df.groupby("service")["cost_usd"].sum().sort_values(ascending=False).head(5)

        for service, cost in top_services.items():
            print(f"  {service:<30} ${cost:>10,.2f}")

    def save_to_postgres(self, df):
        """
        Sauvegarde les données de coûts dans PostgreSQL.

        Insère les données dans la table cloud_costs_raw avec gestion
        des conflits (ON CONFLICT DO NOTHING).

        Args:
            df (pandas.DataFrame): DataFrame contenant les données à sauvegarder

        Note:
            Utilise execute_values pour des insertions en lot optimisées
        """
        if len(df) == 0:
            print("⚠️  Aucune donnée à sauvegarder")
            return

        try:
            # Pool central de core.database (config et timeouts au meme
            # endroit) ; liberation via release_connection(), pas close().
            connection = get_db_connection()
            cursor = connection.cursor()

            # Récupérer les métadonnées AWS
            account_id = os.getenv("AWS_ACCOUNT_ID", "unknown")
            region = os.getenv("AWS_REGION", "unknown")

            # Préparer les données pour l'insertion
            insert_values = [
                (
                    "aws",  # provider
                    account_id,  # account_id
                    row["service"],  # service
                    None,  # resource_id (non applicable pour les coûts globaux)
                    row["date"],  # usage_date
                    row["cost_usd"],  # cost
                    "USD",  # currency
                    region,  # region
                    None,  # raw_data (peut être étendu plus tard)
                )
                for _, row in df.iterrows()
            ]

            # Insertion en lot avec gestion des doublons
            execute_values(
                cursor,
                """
                INSERT INTO cloud_costs_raw
                (provider, account_id, service, resource_id, usage_date,
                 cost, currency, region, raw_data)
                VALUES %s
                ON CONFLICT ON CONSTRAINT uq_cloud_costs
                DO UPDATE SET cost = EXCLUDED.cost, currency = EXCLUDED.currency
                """,
                insert_values,
            )

            # Valider la transaction
            connection.commit()
            rows_inserted = cursor.rowcount

            print(f"\n💾 {rows_inserted} enregistrements insérés dans PostgreSQL")

            # Rendre la connexion au pool
            cursor.close()
            release_connection(connection)

        except Exception as e:
            print(f"❌ Erreur lors de la sauvegarde PostgreSQL: {e}")
            print("💡 Vérifiez la configuration de la base de données")

    def export_to_csv(self, df):
        """
        Exporte les données vers un fichier CSV horodaté.

        Args:
            df (pandas.DataFrame): DataFrame à exporter

        Returns:
            str: Chemin du fichier CSV créé
        """
        if len(df) == 0:
            return None

        # Créer le répertoire data s'il n'existe pas
        os.makedirs("data", exist_ok=True)

        # Générer le nom de fichier avec timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = f"data/aws_costs_{timestamp}.csv"

        # Exporter vers CSV
        df.to_csv(csv_path, index=False)
        print(f"📁 Export CSV: {csv_path}")

        return csv_path

    def run(self, days=30, save_to_db=True, export_csv=True):
        """
        Lance le processus complet de collecte des coûts AWS.

        Cette méthode orchestre toutes les étapes:
        1. Collecte des données via Cost Explorer
        2. Sauvegarde en base de données (optionnel)
        3. Export CSV (optionnel)
        4. Affichage des résultats

        Args:
            days (int): Nombre de jours à collecter (défaut: 30)
            save_to_db (bool): Sauvegarder en base de données (défaut: True)
            export_csv (bool): Exporter en CSV (défaut: True)

        Returns:
            pandas.DataFrame: DataFrame des données collectées
        """
        print("=" * 60)
        print("🚀 Wasteless.io - AWS Cost Collector")
        print("=" * 60)

        # Étape 1: Collecte des données
        df = self.get_costs_last_n_days(days=days)

        if len(df) == 0:
            print("\n⚠️  Aucune donnée collectée - Arrêt du processus")
            return df

        # Étape 2: Sauvegarde en base de données
        if save_to_db:
            self.save_to_postgres(df)

        # Étape 3: Export CSV
        if export_csv:
            self.export_to_csv(df)

        # Résumé final
        print("\n✅ Collecte terminée avec succès!")
        print("=" * 60)

        return df


def already_collected_today():
    """True si cloud_costs_raw contient déjà des lignes collectées aujourd'hui.

    Le pipeline `collect` (toutes les 5 min) appelle ce collecteur avec
    --daily à chaque tick, mais les données Cost Explorer ne se rafraîchissent
    qu'~1×/jour et chaque appel GetCostAndUsage est facturé : on saute donc
    dès qu'on a les lignes du jour. Un run en échec n'insère rien (created_at
    reste dans le passé), donc le tick suivant réessaie jusqu'à ce qu'une
    collecte réussisse. On teste created_at (l'instant de collecte) et non
    usage_date : Cost Explorer accuse un délai de facturation d'un ou deux
    jours, la dernière usage_date n'est jamais aujourd'hui."""
    connection = get_db_connection()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 FROM cloud_costs_raw WHERE provider = 'aws' AND created_at >= CURRENT_DATE LIMIT 1"
        )
        return cursor.fetchone() is not None
    finally:
        if cursor is not None:
            cursor.close()
        release_connection(connection)


def cost_history_days():
    """Profondeur d'historique déjà présente dans cloud_costs_raw, en jours
    (0 si vide).

    Pilote la décision de backfill : la première collecte remonte jusqu'à
    WASTELESS_COST_BACKFILL_DAYS (défaut 365, pour que le sélecteur de plage
    1 an ait des données) ; une fois cette profondeur atteinte, chaque run
    suivant ne rafraîchit que la queue récente (WASTELESS_COST_REFRESH_DAYS),
    ce qui garde l'appel Cost Explorer (facturé) petit. Les anciennes lignes
    ne sont jamais supprimées : la fenêtre collectée ne fait que grandir."""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COALESCE(CURRENT_DATE - MIN(usage_date), 0) AS depth FROM cloud_costs_raw"
        )
        row = cursor.fetchone()
        try:
            return int(row["depth"])
        except (TypeError, KeyError, IndexError):
            return int(row[0])
    finally:
        release_connection(connection)


def main():
    """
    Point d'entrée principal du script.

    Crée une instance du collecteur et lance la collecte. Le flag --daily
    (utilisé par le pipeline `collect`) saute la collecte si elle a déjà
    réussi aujourd'hui et n'écrit pas de CSV pour ne pas accumuler de fichiers
    à chaque tick. La profondeur est auto-adaptative : backfill d'un an au
    premier passage, puis simple rafraîchissement de la queue récente.
    """
    daily = "--daily" in sys.argv[1:]
    backfill_days = int(os.getenv("WASTELESS_COST_BACKFILL_DAYS", "365"))
    refresh_days = int(os.getenv("WASTELESS_COST_REFRESH_DAYS", "35"))
    try:
        if daily and already_collected_today():
            print("✅ Coûts déjà collectés aujourd'hui — collecte quotidienne ignorée")
            return
        # Backfill tant qu'on n'a pas la profondeur cible ; sinon on ne
        # rafraîchit que la queue récente pour garder l'appel API petit.
        days = refresh_days if cost_history_days() >= backfill_days else backfill_days
        collector = AWSCostCollector()
        collector.run(days=days, save_to_db=True, export_csv=not daily)
    except KeyboardInterrupt:
        print("\n⚠️  Collecte interrompue par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur inattendue: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
