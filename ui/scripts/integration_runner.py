#!/usr/bin/env python3
"""
Test script for RemediatorProxy integration
This tests the connection between UI and backend remediator
"""

import sys
import os
from dotenv import load_dotenv
import psycopg2

# Load environment
load_dotenv()

# Import our integration module
from utils.remediator import RemediatorProxy, check_backend_available, get_backend_path

def test_backend_availability():
    """Test 1: Check if backend is available"""
    print("\n" + "="*80)
    print("TEST 1: Backend Availability")
    print("="*80)

    backend_path = get_backend_path()
    print(f"Backend path: {backend_path}")
    print(f"Path exists: {os.path.exists(backend_path)}")

    try:
        sys.path.insert(0, backend_path)
        from src.remediators.ec2_remediator import EC2Remediator
        print("✅ EC2Remediator can be imported")
        return True
    except ImportError as e:
        print(f"❌ Cannot import EC2Remediator: {e}")
        return False


def test_database_connection():
    """Test 2: Check database connection"""
    print("\n" + "="*80)
    print("TEST 2: Database Connection")
    print("="*80)

    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432'),
            database=os.getenv('DB_NAME', 'wasteless'),
            user=os.getenv('DB_USER', 'wasteless'),
            password=os.getenv('DB_PASSWORD')
        )
        print("✅ Database connection successful")

        # Get a pending recommendation
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                r.id,
                r.recommendation_type,
                w.resource_id,
                r.estimated_monthly_savings_eur
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status = 'pending'
            ORDER BY r.estimated_monthly_savings_eur DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        if row:
            print(f"✅ Found pending recommendation: ID={row[0]}, Type={row[1]}, Instance={row[2]}, Savings=€{row[3]:.2f}")
            cursor.close()
            return conn, row[0]  # Return connection and rec_id for next test
        else:
            print("⚠️  No pending recommendations found")
            cursor.close()
            conn.close()
            return None, None

    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None, None


def test_remediator_proxy(conn, rec_id):
    """Test 3: Test RemediatorProxy execution (dry-run)"""
    print("\n" + "="*80)
    print("TEST 3: RemediatorProxy Execution (DRY-RUN)")
    print("="*80)

    if not conn or not rec_id:
        print("⚠️  Skipping - no recommendation to test")
        return False

    try:
        print(f"Testing recommendation ID: {rec_id}")
        print("Mode: DRY-RUN (safe, no actual AWS actions)")

        # Initialize proxy in dry-run mode
        remediator = RemediatorProxy(dry_run=True)
        print("✅ RemediatorProxy initialized")

        # Execute recommendation
        results = remediator.execute_recommendations(conn, [rec_id])
        print(f"✅ Execution completed, got {len(results)} result(s)")

        # Display result
        if results:
            result = results[0]
            print(f"\nResult details:")
            print(f"  Success: {result.get('success')}")
            print(f"  Instance ID: {result.get('instance_id')}")
            print(f"  Recommendation Type: {result.get('recommendation_type')}")
            print(f"  Message: {result.get('message', result.get('error', 'N/A'))}")

            if result.get('success'):
                print("\n✅ TEST PASSED - RemediatorProxy working correctly!")
                return True
            else:
                print(f"\n⚠️  Execution returned success=False: {result.get('error')}")
                return False
        else:
            print("❌ No results returned")
            return False

    except Exception as e:
        print(f"❌ RemediatorProxy execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all integration tests"""
    print("\n" + "="*80)
    print("🧪 WASTELESS UI - REMEDIATOR INTEGRATION TESTS")
    print("="*80)

    # Test 1: Backend availability
    backend_ok = test_backend_availability()

    if not backend_ok:
        print("\n❌ TESTS FAILED - Backend not available")
        return False

    # Test 2: Database connection
    conn, rec_id = test_database_connection()

    if not conn:
        print("\n❌ TESTS FAILED - Database connection failed")
        return False

    # Test 3: RemediatorProxy execution
    proxy_ok = test_remediator_proxy(conn, rec_id)

    # Cleanup
    if conn:
        conn.close()

    # Final result
    print("\n" + "="*80)
    if backend_ok and proxy_ok:
        print("✅ ALL TESTS PASSED - Integration is working!")
        print("="*80)
        print("\n🎉 You can now use the UI to approve/reject recommendations!")
        print("   Navigate to: http://localhost:8888")
        print("   Go to: 📋 Recommendations page")
        print("   Select a recommendation and click 'Approve (Dry-Run)'")
        return True
    else:
        print("❌ SOME TESTS FAILED - Check errors above")
        print("="*80)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
