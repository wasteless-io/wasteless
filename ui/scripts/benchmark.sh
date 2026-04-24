#!/bin/bash
# Wasteless UI - Performance Benchmark
# =====================================
# Measures import times and startup performance

cd "$(dirname "$0")"
source venv/bin/activate

echo "⚡ WASTELESS UI - PERFORMANCE BENCHMARK"
echo "======================================="
echo ""

# 1. Project Stats
echo "📊 PROJECT STATISTICS"
echo "---------------------"
project_size=$(du -sh . 2>/dev/null | cut -f1)
venv_size=$(du -sh venv/ 2>/dev/null | cut -f1)
pyc_count=$(find . -name "*.pyc" | wc -l | tr -d ' ')
pycache_count=$(find . -type d -name "__pycache__" | wc -l | tr -d ' ')

echo "   Project size:      $project_size"
echo "   Venv size:         $venv_size"
echo "   .pyc files:        $pyc_count"
echo "   __pycache__ dirs:  $pycache_count"
echo ""

# 2. Module Import Times
echo "⏱️  MODULE IMPORT TIMES"
echo "---------------------"

test_import() {
    local module=$1
    local name=$2
    local result=$(python3 -c "
import time
start = time.time()
import $module
duration_ms = (time.time() - start) * 1000
if duration_ms < 100:
    status = '✅'
elif duration_ms < 500:
    status = '⚠️ '
else:
    status = '🔴'
print(f'{status} {name:25s}: {duration_ms:6.1f}ms')
" 2>&1)
    echo "   $result"
}

test_import "streamlit" "streamlit"
test_import "pandas" "pandas"
test_import "plotly.express" "plotly.express"
test_import "plotly.graph_objects" "plotly.graph_objects"
test_import "psycopg2" "psycopg2"
test_import "boto3" "boto3"
test_import "numpy" "numpy"
test_import "yaml" "yaml"
test_import "dotenv" "dotenv"

echo ""

# 3. Custom Modules
echo "🔧 CUSTOM MODULES"
echo "----------------"

python3 -c "
import sys
import time
sys.path.insert(0, '.')

modules = [
    ('utils.logger', 'logger'),
    ('utils.design_system', 'design_system'),
    ('utils.pagination', 'pagination'),
    ('utils.config_manager', 'config_manager'),
]

for module_name, short_name in modules:
    start = time.time()
    try:
        __import__(module_name)
        duration_ms = (time.time() - start) * 1000
        status = '✅' if duration_ms < 50 else '⚠️ ' if duration_ms < 200 else '🔴'
        print(f'   {status} {short_name:25s}: {duration_ms:6.1f}ms')
    except Exception as e:
        print(f'   ❌ {short_name:25s}: FAILED')
"

echo ""

# 4. Backend Check
echo "🔌 BACKEND INTEGRATION"
echo "---------------------"

python3 -c "
import sys
import time
sys.path.insert(0, '.')

start = time.time()
from utils.remediator import check_backend_available, get_backend_path
duration_ms = (time.time() - start) * 1000
print(f'   Import remediator: {duration_ms:.1f}ms')

backend_path = get_backend_path()
import os
exists = os.path.exists(backend_path)
print(f'   Backend path:      {backend_path}')
print(f'   Exists:            {'✅' if exists else '❌'}')

start = time.time()
available = check_backend_available()
duration_ms = (time.time() - start) * 1000
status = '✅' if available else '❌'
print(f'   {status} Backend check:   {duration_ms:.1f}ms')
"

echo ""

# 5. Database Check
echo "🗄️  DATABASE CONNECTION"
echo "----------------------"

python3 -c "
import os
import time
from dotenv import load_dotenv
load_dotenv()

try:
    import psycopg2
    start = time.time()
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        database=os.getenv('DB_NAME', 'wasteless'),
        user=os.getenv('DB_USER', 'wasteless'),
        password=os.getenv('DB_PASSWORD', 'wasteless_dev_2025'),
        connect_timeout=3
    )
    duration_ms = (time.time() - start) * 1000
    print(f'   ✅ Connection:     {duration_ms:.1f}ms')

    start = time.time()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM recommendations')
    result = cursor.fetchone()
    query_ms = (time.time() - start) * 1000
    print(f'   ✅ Sample query:   {query_ms:.1f}ms ({result[0]} recs)')

    cursor.close()
    conn.close()
except Exception as e:
    print(f'   ❌ Failed: {str(e)[:50]}')
"

echo ""
echo "======================================="
echo "✅ Benchmark complete!"
echo ""
echo "💡 To improve performance:"
echo "   Run: ./cleanup_performance.sh"
echo ""
