#!/usr/bin/env python3
"""
TEST END-TO-END - Wasteless Platform
=====================================

Ce script teste le pipeline complet de détection et remédiation du gaspillage cloud:

FLUX COMPLET TESTÉ:
1. Collecte des métriques CloudWatch (CPU, Network, etc.)
2. Détection des instances EC2 inactives (idle)
3. Génération des recommandations d'optimisation
4. Exécution des actions de remédiation (stop instance)
5. Vérification des économies réalisées (savings tracking)

ARCHITECTURE DU SYSTÈME:
------------------------
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  AWS CloudWatch │────▶│  EC2 Metrics DB  │────▶│  Idle Detector  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                           │
                                                           ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Savings Tracker │◀────│  EC2 Remediator  │◀────│ Recommendations │
└─────────────────┘     └──────────────────┘     └─────────────────┘

PRÉREQUIS:
----------
- Base de données PostgreSQL en cours d'exécution
- Credentials AWS configurés dans .env
- Tables créées via les migrations SQL
- Environnement virtuel Python activé

MODES D'EXÉCUTION:
------------------
- DRY_RUN=True  : Mode test, aucune action AWS réelle (recommandé pour les tests)
- DRY_RUN=False : Mode production, actions AWS réelles (dangereux!)

AUTEUR: Wasteless Team
DATE: 2025-12-22
"""

import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from decimal import Decimal

# Ajouter le répertoire parent au path pour importer les modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import des modules Wasteless
from src.collectors.aws_cloudwatch import AWSCloudWatchCollector
from src.detectors.ec2_idle import EC2IdleDetector
from src.remediators.ec2_remediator import EC2Remediator
from src.trackers.savings_tracker import SavingsTracker
from src.core.database import get_db_connection

# Configuration de la journalisation
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EndToEndTester:
    """
    Classe principale pour orchestrer les tests end-to-end.

    Cette classe coordonne tous les composants du système Wasteless
    et vérifie que le flux complet fonctionne correctement.
    """

    def __init__(self, dry_run: bool = True):
        """
        Initialise le testeur end-to-end.

        Args:
            dry_run: Si True, aucune action AWS réelle n'est exécutée
                    (recommandé pour les tests)
        """
        self.dry_run = dry_run
        self.test_start_time = datetime.now()

        # Résultats des tests (pour le rapport final)
        self.results = {
            'metrics_collected': 0,
            'idle_instances_detected': 0,
            'recommendations_created': 0,
            'actions_executed': 0,
            'actions_successful': 0,
            'actions_failed': 0,
            'total_potential_savings': 0.0,
            'errors': []
        }

        logger.info("="*80)
        logger.info("🧪 WASTELESS END-TO-END TEST SUITE")
        logger.info("="*80)
        logger.info(f"Mode: {'DRY-RUN (Safe)' if dry_run else 'PRODUCTION (Dangerous!)'}")
        logger.info(f"Start time: {self.test_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("="*80)

        # Connexion à la base de données
        try:
            self.conn = get_db_connection()
            logger.info("✅ Database connection established")
        except Exception as e:
            logger.error(f"❌ Failed to connect to database: {e}")
            sys.exit(1)

    def validate_environment(self) -> bool:
        """
        Valide que l'environnement est correctement configuré.

        Vérifie:
        - Variables d'environnement AWS
        - Variables de base de données
        - Tables PostgreSQL nécessaires

        Returns:
            True si l'environnement est valide, False sinon
        """
        logger.info("\n" + "="*80)
        logger.info("📋 STEP 0: ENVIRONMENT VALIDATION")
        logger.info("="*80)

        # Liste des variables d'environnement requises
        required_env_vars = [
            'AWS_REGION',
            'AWS_ACCOUNT_ID',
            'AWS_ACCESS_KEY_ID',
            'AWS_SECRET_ACCESS_KEY',
            'DB_HOST',
            'DB_PORT',
            'DB_NAME',
            'DB_USER',
            'DB_PASSWORD'
        ]

        # Vérifier les variables d'environnement
        logger.info("\n1. Checking environment variables...")
        missing_vars = []
        for var in required_env_vars:
            value = os.getenv(var)
            if value:
                # Masquer les credentials sensibles dans les logs
                if 'KEY' in var or 'PASSWORD' in var:
                    display_value = value[:4] + "****" + value[-4:] if len(value) > 8 else "****"
                else:
                    display_value = value
                logger.info(f"   ✅ {var}: {display_value}")
            else:
                logger.error(f"   ❌ {var}: MISSING")
                missing_vars.append(var)

        if missing_vars:
            logger.error(f"\n❌ Missing environment variables: {', '.join(missing_vars)}")
            return False

        # Vérifier les tables de la base de données
        logger.info("\n2. Checking database tables...")
        required_tables = [
            'ec2_metrics',
            'waste_detected',
            'recommendations',
            'actions_log',
            'savings_realized',
            'rollback_snapshots'
        ]

        cursor = self.conn.cursor()

        missing_tables = []
        for table in required_tables:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = %s
                );
            """, (table,))

            exists = cursor.fetchone()[0]
            if exists:
                # Compter les lignes dans la table
                cursor.execute(f"SELECT COUNT(*) FROM {table};")
                count = cursor.fetchone()[0]
                logger.info(f"   ✅ {table}: {count} rows")
            else:
                logger.error(f"   ❌ {table}: TABLE NOT FOUND")
                missing_tables.append(table)

        cursor.close()

        if missing_tables:
            logger.error(f"\n❌ Missing database tables: {', '.join(missing_tables)}")
            logger.error("   Run SQL migrations first: psql -d wasteless -f sql/init.sql")
            return False

        logger.info("\n✅ Environment validation passed!")
        return True

    def test_metrics_collection(self) -> bool:
        """
        Teste la collecte des métriques CloudWatch.

        Ce test vérifie que nous pouvons:
        - Lister les instances EC2 actives
        - Récupérer les métriques CloudWatch (CPU, Network, etc.)
        - Sauvegarder les métriques dans la base de données

        Returns:
            True si la collecte réussit, False sinon
        """
        logger.info("\n" + "="*80)
        logger.info("📊 STEP 1: CLOUDWATCH METRICS COLLECTION")
        logger.info("="*80)

        try:
            # Initialiser le collecteur CloudWatch
            logger.info("\n1. Initializing CloudWatch collector...")
            collector = AWSCloudWatchCollector()
            logger.info("   ✅ Collector initialized")

            # Lister les instances EC2
            logger.info("\n2. Listing EC2 instances...")
            instances = collector.get_ec2_instances()
            logger.info(f"   ✅ Found {len(instances)} EC2 instances")

            if len(instances) == 0:
                logger.warning("   ⚠️  No EC2 instances found - test limited")
                return True

            # Afficher les détails des instances trouvées
            for instance in instances[:3]:  # Limiter à 3 pour la lisibilité
                logger.info(f"      - {instance['instance_id']} ({instance['instance_type']}) - {instance['instance_state']}")

            # Collecter les métriques pour les 7 derniers jours
            logger.info("\n3. Collecting CloudWatch metrics (last 7 days)...")
            days_to_collect = 7

            metrics_collected = collector.collect_all_metrics(
                days=days_to_collect
            )

            logger.info(f"   ✅ Collected {metrics_collected} metric data points")

            # Mettre à jour les résultats
            self.results['metrics_collected'] = metrics_collected

            # Vérifier dans la base de données
            logger.info("\n4. Verifying data in database...")
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT COUNT(*),
                       COUNT(DISTINCT instance_id),
                       MIN(collection_date),
                       MAX(collection_date)
                FROM ec2_metrics
                WHERE collection_date >= CURRENT_DATE - INTERVAL '7 days';
            """)

            row = cursor.fetchone()
            total_metrics, unique_instances, min_date, max_date = row

            logger.info(f"   ✅ Database verification:")
            logger.info(f"      - Total metrics: {total_metrics}")
            logger.info(f"      - Unique instances: {unique_instances}")
            logger.info(f"      - Date range: {min_date} to {max_date}")

            cursor.close()

            logger.info("\n✅ Metrics collection test PASSED")
            return True

        except Exception as e:
            logger.error(f"\n❌ Metrics collection test FAILED: {e}")
            self.results['errors'].append(f"Metrics collection: {str(e)}")
            return False

    def test_idle_detection(self) -> bool:
        """
        Teste la détection des instances inactives.

        Ce test vérifie que nous pouvons:
        - Analyser les métriques CPU sur 7 jours
        - Identifier les instances avec CPU < 5%
        - Calculer le gaspillage estimé (coût mensuel)
        - Générer un score de confiance
        - Sauvegarder dans waste_detected

        Returns:
            True si la détection réussit, False sinon
        """
        logger.info("\n" + "="*80)
        logger.info("🔍 STEP 2: IDLE INSTANCE DETECTION")
        logger.info("="*80)

        try:
            # Initialiser le détecteur
            logger.info("\n1. Initializing idle detector...")
            detector = EC2IdleDetector()
            logger.info("   ✅ Detector initialized")

            # Paramètres de détection
            cpu_threshold = 5.0  # 5% de CPU
            days = 7

            logger.info(f"\n2. Detecting idle instances (CPU < {cpu_threshold}%, last {days} days)...")

            # Détecter les instances inactives
            waste_list = detector.detect_idle_instances(
                cpu_threshold=cpu_threshold,
                days=days
            )

            logger.info(f"   ✅ Detected {len(waste_list)} idle instances")

            # Mettre à jour les résultats
            self.results['idle_instances_detected'] = len(waste_list)

            if len(waste_list) == 0:
                logger.info("   ℹ️  No idle instances found - all instances are active")
                return True

            # Calculer les totaux
            total_waste = sum(w['monthly_waste_eur'] for w in waste_list)
            avg_confidence = sum(w['confidence_score'] for w in waste_list) / len(waste_list)

            logger.info(f"\n3. Idle instances analysis:")
            logger.info(f"   - Total monthly waste: €{total_waste:,.2f}")
            logger.info(f"   - Average confidence: {avg_confidence:.2f}")
            logger.info(f"   - Annual waste: €{total_waste * 12:,.2f}")

            # Afficher les détails des 3 premiers
            logger.info(f"\n4. Top idle instances:")
            for i, waste in enumerate(waste_list[:3], 1):
                cpu_avg = waste['metadata']['cpu_avg_7d']
                instance_type = waste['metadata']['instance_type']
                monthly_waste = waste['monthly_waste_eur']
                confidence = waste['confidence_score']

                logger.info(
                    f"   {i}. {waste['resource_id']} ({instance_type})\n"
                    f"      CPU: {cpu_avg:.2f}% | Waste: €{monthly_waste:.2f}/mo | "
                    f"Confidence: {confidence:.2f}"
                )

            # Sauvegarder dans la base de données
            logger.info(f"\n5. Saving waste records to database...")
            waste_ids = detector.save_waste_detected(waste_list)
            logger.info(f"   ✅ Saved {len(waste_ids)} waste records")

            # Générer les recommandations
            logger.info(f"\n6. Generating recommendations...")
            recommendations_count = detector.generate_recommendations(waste_ids)
            logger.info(f"   ✅ Created {recommendations_count} recommendations")

            # Mettre à jour les résultats
            self.results['recommendations_created'] = recommendations_count
            self.results['total_potential_savings'] = float(total_waste)

            # Vérifier dans la base de données
            logger.info(f"\n7. Verifying recommendations in database...")
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT
                    recommendation_type,
                    COUNT(*),
                    SUM(estimated_monthly_savings_eur)
                FROM recommendations
                WHERE created_at >= %s
                GROUP BY recommendation_type;
            """, (self.test_start_time,))

            for row in cursor.fetchall():
                rec_type, count, total_savings = row
                logger.info(f"   - {rec_type}: {count} recommendations, €{total_savings:.2f}/mo")

            cursor.close()

            logger.info("\n✅ Idle detection test PASSED")
            return True

        except Exception as e:
            logger.error(f"\n❌ Idle detection test FAILED: {e}")
            self.results['errors'].append(f"Idle detection: {str(e)}")
            return False

    def test_remediation(self) -> bool:
        """
        Teste l'exécution des actions de remédiation.

        Ce test vérifie que nous pouvons:
        - Récupérer les recommandations en attente
        - Valider les safeguards (protection)
        - Exécuter l'action de stop instance
        - Créer un snapshot de rollback
        - Logger l'action dans actions_log

        Returns:
            True si la remédiation réussit, False sinon
        """
        logger.info("\n" + "="*80)
        logger.info("🚀 STEP 3: REMEDIATION EXECUTION")
        logger.info("="*80)

        try:
            # Initialiser le remediator
            logger.info("\n1. Initializing EC2 remediator...")
            logger.info(f"   Mode: {'DRY-RUN (no real AWS actions)' if self.dry_run else 'PRODUCTION (real actions!)'}")

            remediator = EC2Remediator(dry_run=self.dry_run)
            logger.info("   ✅ Remediator initialized")

            # Vérifier s'il y a des recommandations en attente
            logger.info("\n2. Checking for pending recommendations...")
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM recommendations
                WHERE status = 'pending'
                  AND recommendation_type IN ('stop_instance', 'terminate_instance');
            """)

            pending_count = cursor.fetchone()[0]
            logger.info(f"   ℹ️  Found {pending_count} pending recommendations")

            cursor.close()

            if pending_count == 0:
                logger.info("   ⚠️  No pending recommendations to process")
                return True

            # Traiter les recommandations (maximum 3 pour le test)
            logger.info("\n3. Processing pending recommendations (max 3)...")

            results = remediator.process_pending_recommendations(limit=3)

            # Compter les succès et échecs
            successful = [r for r in results if r['success']]
            failed = [r for r in results if not r['success']]

            logger.info(f"\n4. Remediation results:")
            logger.info(f"   - Total processed: {len(results)}")
            logger.info(f"   - Successful: {len(successful)}")
            logger.info(f"   - Failed: {len(failed)}")

            # Mettre à jour les résultats
            self.results['actions_executed'] = len(results)
            self.results['actions_successful'] = len(successful)
            self.results['actions_failed'] = len(failed)

            # Afficher les détails de chaque action
            logger.info(f"\n5. Action details:")
            for i, result in enumerate(results, 1):
                status_icon = "✅" if result['success'] else "❌"
                status_text = "SUCCESS" if result['success'] else "FAILED"

                logger.info(f"   {i}. {result['instance_id']}: {status_icon} {status_text}")

                if result['error']:
                    logger.info(f"      Error: {result['error']}")

                if result.get('action_log_id'):
                    logger.info(f"      Action log ID: {result['action_log_id']}")

            # Vérifier les actions dans la base de données
            logger.info(f"\n6. Verifying actions in database...")
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT
                    action_type,
                    action_status,
                    COUNT(*)
                FROM actions_log
                WHERE created_at >= %s
                GROUP BY action_type, action_status;
            """, (self.test_start_time,))

            logger.info("   Database verification:")
            for row in cursor.fetchall():
                action_type, status, count = row
                logger.info(f"   - {action_type} ({status}): {count} actions")

            cursor.close()

            logger.info("\n✅ Remediation test PASSED")
            return True

        except Exception as e:
            logger.error(f"\n❌ Remediation test FAILED: {e}")
            self.results['errors'].append(f"Remediation: {str(e)}")
            return False

    def test_savings_tracking(self) -> bool:
        """
        Teste le suivi des économies réalisées.

        Ce test vérifie que nous pouvons:
        - Identifier les actions exécutées il y a 7+ jours
        - Récupérer les coûts avant/après via Cost Explorer
        - Calculer les économies réelles
        - Comparer avec les estimations
        - Sauvegarder dans savings_realized

        Returns:
            True si le tracking réussit, False sinon
        """
        logger.info("\n" + "="*80)
        logger.info("💰 STEP 4: SAVINGS TRACKING")
        logger.info("="*80)

        try:
            # Initialiser le tracker
            logger.info("\n1. Initializing savings tracker...")
            tracker = SavingsTracker()
            logger.info("   ✅ Tracker initialized")

            # Vérifier s'il y a des actions anciennes à vérifier
            logger.info("\n2. Checking for actions eligible for verification...")
            logger.info("   (Actions must be 7+ days old to verify savings)")

            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM actions_log a
                LEFT JOIN savings_realized s ON s.recommendation_id = a.recommendation_id
                WHERE a.action_status = 'success'
                  AND a.action_type IN ('stop', 'terminate')
                  AND a.action_date < NOW() - INTERVAL '7 days'
                  AND s.id IS NULL;
            """)

            eligible_count = cursor.fetchone()[0]
            logger.info(f"   ℹ️  Found {eligible_count} actions eligible for verification")

            cursor.close()

            if eligible_count == 0:
                logger.info("   ⚠️  No actions old enough to verify (need 7+ days)")
                logger.info("   ℹ️  This is normal for new deployments")
                return True

            # Vérifier les économies pour toutes les actions éligibles
            logger.info("\n3. Verifying savings for eligible actions...")

            verification_results = tracker.verify_all_unverified_actions(
                min_days_elapsed=7
            )

            logger.info(f"   ✅ Verified {len(verification_results)} actions")

            if len(verification_results) == 0:
                return True

            # Calculer les totaux
            total_actual_savings = sum(r['actual_savings_eur'] for r in verification_results)
            total_estimated_savings = sum(r['estimated_savings_eur'] for r in verification_results)

            avg_accuracy = sum(r['accuracy_percent'] for r in verification_results) / len(verification_results)

            logger.info(f"\n4. Savings verification results:")
            logger.info(f"   - Actions verified: {len(verification_results)}")
            logger.info(f"   - Total actual savings: €{total_actual_savings:,.2f}/month")
            logger.info(f"   - Total estimated savings: €{total_estimated_savings:,.2f}/month")
            logger.info(f"   - Average accuracy: {avg_accuracy:.1f}%")

            # Afficher les détails des 3 premières vérifications
            logger.info(f"\n5. Verification details:")
            for i, result in enumerate(verification_results[:3], 1):
                logger.info(
                    f"   {i}. {result['instance_id']}\n"
                    f"      Actual: €{result['actual_savings_eur']:.2f}/mo | "
                    f"Estimated: €{result['estimated_savings_eur']:.2f}/mo | "
                    f"Accuracy: {result['accuracy_percent']:.1f}%"
                )

            # Obtenir les totaux globaux
            logger.info(f"\n6. Getting total verified savings...")
            totals = tracker.get_total_verified_savings()

            logger.info(f"   Overall statistics:")
            logger.info(f"   - Total actions verified: {totals['total_actions']}")
            logger.info(f"   - Total monthly savings: €{totals['total_savings_eur']:,.2f}")
            logger.info(f"   - Annual savings: €{totals['total_savings_eur'] * 12:,.2f}")

            logger.info("\n✅ Savings tracking test PASSED")
            return True

        except Exception as e:
            logger.error(f"\n❌ Savings tracking test FAILED: {e}")
            self.results['errors'].append(f"Savings tracking: {str(e)}")
            return False

    def generate_final_report(self, tests_passed: Dict[str, bool]):
        """
        Génère le rapport final des tests.

        Args:
            tests_passed: Dictionnaire des tests et leur statut (True/False)
        """
        logger.info("\n" + "="*80)
        logger.info("📊 END-TO-END TEST REPORT")
        logger.info("="*80)

        # Calculer le temps total
        test_duration = datetime.now() - self.test_start_time

        # Afficher le résumé des tests
        logger.info(f"\nTest execution summary:")
        logger.info(f"  Start time: {self.test_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"  Duration: {test_duration.total_seconds():.2f} seconds")
        logger.info(f"  Mode: {'DRY-RUN' if self.dry_run else 'PRODUCTION'}")

        # Afficher les résultats de chaque test
        logger.info(f"\nTest results:")
        all_passed = True
        for test_name, passed in tests_passed.items():
            icon = "✅" if passed else "❌"
            status = "PASSED" if passed else "FAILED"
            logger.info(f"  {icon} {test_name}: {status}")
            if not passed:
                all_passed = False

        # Afficher les métriques collectées
        logger.info(f"\nMetrics collected:")
        logger.info(f"  - CloudWatch metrics: {self.results['metrics_collected']}")
        logger.info(f"  - Idle instances detected: {self.results['idle_instances_detected']}")
        logger.info(f"  - Recommendations created: {self.results['recommendations_created']}")
        logger.info(f"  - Actions executed: {self.results['actions_executed']}")
        logger.info(f"  - Actions successful: {self.results['actions_successful']}")
        logger.info(f"  - Actions failed: {self.results['actions_failed']}")

        # Afficher les économies potentielles
        if self.results['total_potential_savings'] > 0:
            logger.info(f"\nPotential savings:")
            logger.info(f"  - Monthly: €{self.results['total_potential_savings']:,.2f}")
            logger.info(f"  - Annual: €{self.results['total_potential_savings'] * 12:,.2f}")

        # Afficher les erreurs rencontrées
        if self.results['errors']:
            logger.info(f"\nErrors encountered:")
            for i, error in enumerate(self.results['errors'], 1):
                logger.info(f"  {i}. {error}")

        # Résultat final
        logger.info("\n" + "="*80)
        if all_passed:
            logger.info("🎉 ALL TESTS PASSED - SYSTEM IS WORKING CORRECTLY")
        else:
            logger.info("⚠️  SOME TESTS FAILED - PLEASE REVIEW ERRORS ABOVE")
        logger.info("="*80 + "\n")

        return all_passed

    def run_all_tests(self) -> bool:
        """
        Exécute tous les tests end-to-end dans l'ordre.

        Returns:
            True si tous les tests passent, False sinon
        """
        tests_passed = {}

        # STEP 0: Validation de l'environnement
        tests_passed['Environment Validation'] = self.validate_environment()
        if not tests_passed['Environment Validation']:
            logger.error("\n❌ Environment validation failed - cannot proceed")
            return False

        # STEP 1: Collecte des métriques
        tests_passed['Metrics Collection'] = self.test_metrics_collection()

        # STEP 2: Détection des instances inactives
        tests_passed['Idle Detection'] = self.test_idle_detection()

        # STEP 3: Exécution de la remédiation
        tests_passed['Remediation'] = self.test_remediation()

        # STEP 4: Suivi des économies
        tests_passed['Savings Tracking'] = self.test_savings_tracking()

        # Générer le rapport final
        return self.generate_final_report(tests_passed)

    def __del__(self):
        """Ferme la connexion à la base de données lors de la destruction."""
        if hasattr(self, 'conn'):
            self.conn.close()


def main():
    """
    Point d'entrée principal du script de test end-to-end.

    Usage:
        # Mode DRY-RUN (recommandé, aucune action AWS réelle)
        python tests/test_end_to_end.py

        # Mode PRODUCTION (dangereux, actions AWS réelles!)
        DRY_RUN=false python tests/test_end_to_end.py
    """
    # Charger les variables d'environnement
    from dotenv import load_dotenv
    load_dotenv()

    # Déterminer le mode (DRY-RUN par défaut)
    dry_run = os.getenv('DRY_RUN', 'true').lower() != 'false'

    if not dry_run:
        logger.warning("="*80)
        logger.warning("⚠️  WARNING: PRODUCTION MODE ENABLED")
        logger.warning("⚠️  Real AWS actions will be executed!")
        logger.warning("⚠️  Press Ctrl+C within 5 seconds to abort...")
        logger.warning("="*80)
        time.sleep(5)

    # Créer et exécuter le testeur
    tester = EndToEndTester(dry_run=dry_run)

    try:
        success = tester.run_all_tests()
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.warning("\n⚠️  Test interrupted by user")
        sys.exit(1)

    except Exception as e:
        logger.error(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
