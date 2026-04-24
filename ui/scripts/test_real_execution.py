#!/usr/bin/env python3
"""
Test Real Execution
===================
Test the complete flow: UI → Backend → AWS (dry-run=False)
"""

import sys
import os
from dotenv import load_dotenv
import psycopg2

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

from utils.remediator import RemediatorProxy
from utils.config_manager import ConfigManager

def main():
    print("\n" + "="*80)
    print("🧪 TESTING REAL EXECUTION (Production Mode)")
    print("="*80)

    # Check configuration
    print("\n1️⃣  Checking configuration...")
    config_manager = ConfigManager()
    config = config_manager.load_config()

    auto_enabled = config.get('auto_remediation', {}).get('enabled', False)
    min_age = config.get('protection', {}).get('min_instance_age_days', 30)
    schedule_days = config.get('schedule', {}).get('allowed_days', [])

    print(f"   Auto-remediation: {'✅ ENABLED' if auto_enabled else '❌ DISABLED'}")
    print(f"   Min instance age: {min_age} days")
    print(f"   Schedule restrictions: {'✅ ACTIVE' if schedule_days else '❌ DISABLED (allows all)'}")

    if not auto_enabled:
        print("\n❌ Auto-remediation is DISABLED. Cannot proceed.")
        print("   Enable it via Settings or toggle in Recommendations page.")
        return False

    # Connect to database
    print("\n2️⃣  Connecting to database...")
    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432'),
            database=os.getenv('DB_NAME', 'wasteless'),
            user=os.getenv('DB_USER', 'wasteless'),
            password=os.getenv('DB_PASSWORD')
        )
        print("   ✅ Connected")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False

    # Get recommendation #13
    print("\n3️⃣  Loading recommendation #13...")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.id, w.resource_id, r.recommendation_type, w.confidence_score
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.id = 13
    """)
    row = cursor.fetchone()

    if not row:
        print("   ❌ Recommendation #13 not found")
        cursor.close()
        conn.close()
        return False

    rec_id, instance_id, rec_type, confidence = row
    print(f"   ✅ Found: {instance_id}")
    print(f"      Type: {rec_type}")
    print(f"      Confidence: {confidence:.0%}")

    # Execute in PRODUCTION mode (dry_run=False)
    print("\n4️⃣  Executing in PRODUCTION mode (dry_run=False)...")
    print("   ⚠️  This will attempt REAL AWS actions!")
    print("   ⚠️  Press Ctrl+C within 3 seconds to abort...")

    import time
    for i in range(3, 0, -1):
        print(f"   {i}...")
        time.sleep(1)

    print("\n   ⚡ EXECUTING NOW...")

    try:
        remediator = RemediatorProxy(dry_run=False)
        results = remediator.execute_recommendations(conn, [rec_id])

        # Display result
        if results:
            result = results[0]
            print("\n" + "="*80)
            print("📋 EXECUTION RESULT")
            print("="*80)

            if result.get('success'):
                print("✅ SUCCESS!")
                print(f"   Instance: {result.get('instance_id')}")
                print(f"   Action: {result.get('action')}")
                print(f"   Action Log ID: {result.get('action_log_id')}")
                print(f"   Message: {result.get('message', 'N/A')}")

                print("\n🎉 The action was EXECUTED on AWS!")
                print(f"   Check your AWS console for instance {instance_id}")

            else:
                print("❌ FAILED")
                print(f"   Error: {result.get('error')}")
                print(f"   Instance: {result.get('instance_id')}")

            print("\n📊 Full result:")
            import json
            print(json.dumps(result, indent=2))

        else:
            print("❌ No results returned")

    except Exception as e:
        print(f"\n❌ Exception during execution: {e}")
        import traceback
        traceback.print_exc()
        return False

    cursor.close()
    conn.close()

    print("\n" + "="*80)
    print("✅ TEST COMPLETED")
    print("="*80)
    return True


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Aborted by user")
        sys.exit(1)
