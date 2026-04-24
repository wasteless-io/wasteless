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

import boto3
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import sys


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
        required_vars = ['AWS_REGION', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        if missing_vars:
            print(f"❌ Variables d'environnement manquantes: {', '.join(missing_vars)}")
            print("💡 Vérifiez votre fichier .env")
            sys.exit(1)
        
        # Initialiser le client Cost Explorer AWS
        try:
            self.ce_client = boto3.client(
                'ce',  # Cost Explorer service
                region_name=os.getenv('AWS_REGION'),
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
            )
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
            # Appel à l'API Cost Explorer
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    'Start': str(start_date),
                    'End': str(end_date)
                },
                Granularity='DAILY',  # Granularité quotidienne
                Metrics=['UnblendedCost', 'UsageQuantity'],  # Coût et usage
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]  # Grouper par service
            )
            
            # Traitement des résultats de l'API
            costs_data = []
            
            # Parcourir chaque jour dans la réponse
            for daily_result in response['ResultsByTime']:
                date = daily_result['TimePeriod']['Start']
                
                # Parcourir chaque service pour ce jour
                for group in daily_result['Groups']:
                    service_name = group['Keys'][0]
                    cost_amount = float(group['Metrics']['UnblendedCost']['Amount'])
                    usage_quantity = float(group['Metrics']['UsageQuantity']['Amount'])
                    
                    # Filtrer les coûts négligeables (< 1 centime)
                    if cost_amount > 0.01:
                        costs_data.append({
                            'date': date,
                            'service': service_name,
                            'cost_usd': cost_amount,
                            'usage': usage_quantity
                        })
            
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
        total_cost = df['cost_usd'].sum()
        unique_services = df['service'].nunique()
        
        print(f"✅ {len(df)} enregistrements collectés")
        print(f"💰 Coût total: ${total_cost:,.2f}")
        print(f"📦 Services uniques: {unique_services}")
        
        # Afficher le top 5 des services les plus coûteux
        print(f"\n📈 Top 5 des services les plus coûteux:")
        top_services = df.groupby('service')['cost_usd'].sum().sort_values(ascending=False).head(5)
        
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
            # Établir la connexion PostgreSQL
            connection = psycopg2.connect(
                host=os.getenv('DB_HOST', 'localhost'),
                port=int(os.getenv('DB_PORT', 5432)),
                database=os.getenv('DB_NAME', 'wasteless'),
                user=os.getenv('DB_USER', 'wasteless'),
                password=os.getenv('DB_PASSWORD')
            )
            cursor = connection.cursor()
            
            # Récupérer les métadonnées AWS
            account_id = os.getenv('AWS_ACCOUNT_ID', 'unknown')
            region = os.getenv('AWS_REGION', 'unknown')
            
            # Préparer les données pour l'insertion
            insert_values = [
                (
                    'aws',                    # provider
                    account_id,               # account_id
                    row['service'],           # service
                    None,                     # resource_id (non applicable pour les coûts globaux)
                    row['date'],              # usage_date
                    row['cost_usd'],          # cost
                    'USD',                    # currency
                    region,                   # region
                    None                      # raw_data (peut être étendu plus tard)
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
                ON CONFLICT DO NOTHING
                """,
                insert_values
            )
            
            # Valider la transaction
            connection.commit()
            rows_inserted = cursor.rowcount
            
            print(f"\n💾 {rows_inserted} enregistrements insérés dans PostgreSQL")
            
            # Fermer les connexions
            cursor.close()
            connection.close()
            
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
        os.makedirs('data', exist_ok=True)
        
        # Générer le nom de fichier avec timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
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


def main():
    """
    Point d'entrée principal du script.
    
    Crée une instance du collecteur et lance la collecte
    avec les paramètres par défaut.
    """
    try:
        collector = AWSCostCollector()
        collector.run(days=30, save_to_db=True, export_csv=True)
    except KeyboardInterrupt:
        print("\n⚠️  Collecte interrompue par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur inattendue: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()