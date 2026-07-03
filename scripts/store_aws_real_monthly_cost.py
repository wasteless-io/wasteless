#!/usr/bin/python3
"""
Retrieve AWS monthly billing data via Cost Explorer and store it in cloud_costs_raw.
Supports multi-account via AWS Organizations; falls back to current account.

Usage:
    python3 scripts/store_aws_real_monthly_cost.py

Env vars:
    FINOPS_MONTHS        Number of past months to retrieve (default: 3)
    SKIP_INSERT_INTO_DB  Set to 'True' to dry-run without writing to DB
"""
import os
import sys
import boto3
import datetime
import psycopg2
from dateutil import relativedelta
from dotenv import load_dotenv

load_dotenv()

TABLE = 'cloud_costs_raw'


def get_accounts():
    """Return {account_id: account_name} for all accessible accounts."""
    try:
        org = boto3.client('organizations')
        paginator = org.get_paginator('list_accounts')
        accounts = {}
        for page in paginator.paginate():
            for acct in page['Accounts']:
                if acct['Status'] == 'ACTIVE':
                    accounts[acct['Id']] = acct['Name']
        if accounts:
            return accounts
    except Exception:
        pass
    # fallback: current account only
    sts = boto3.client('sts')
    identity = sts.get_caller_identity()
    account_id = identity['Account']
    return {account_id: os.environ.get('AWS_ACCOUNT_NAME', account_id)}


def fetch_monthly_costs(account_id, start_date, end_date):
    """Return list of (month, cost_usd) for a single account."""
    ce = boto3.client('ce')
    results = []
    kwargs = {
        'TimePeriod': {'Start': start_date, 'End': end_date},
        'Granularity': 'MONTHLY',
        'Metrics': ['UnblendedCost'],
        'Filter': {
            'And': [
                {'Not': {'Dimensions': {'Key': 'RECORD_TYPE', 'Values': ['Tax', 'Credit'], 'MatchOptions': ['EQUALS']}}},
                {'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': [account_id], 'MatchOptions': ['EQUALS']}},
            ]
        },
    }
    while True:
        resp = ce.get_cost_and_usage(**kwargs)
        for entry in resp['ResultsByTime']:
            month = entry['TimePeriod']['Start']
            cost = float(entry['Total']['UnblendedCost']['Amount'])
            results.append((month, cost))
        if 'NextPageToken' not in resp:
            break
        kwargs['NextPageToken'] = resp['NextPageToken']
    return results


def collect(months):
    """Fetch billing data for all accounts over the last N months."""
    end_date = datetime.date.today()
    start_date = end_date - relativedelta.relativedelta(months=months, day=1)
    start_str = start_date.strftime('%Y-%m-01')
    end_str = end_date.strftime('%Y-%m-%d')

    print(f"Fetching AWS costs from {start_str} to {end_str}")

    accounts = get_accounts()
    rows = []
    for account_id, account_name in accounts.items():
        print(f"  Account: {account_name} ({account_id})")
        for month, cost in fetch_monthly_costs(account_id, start_str, end_str):
            if cost < 0.01:
                continue
            rows.append({
                'provider': 'aws',
                'account_id': account_id,
                'service': 'ALL',
                'usage_date': month,
                'cost': round(cost, 4),
                'currency': 'USD',
                'raw_data': f'{{"account_name": "{account_name}"}}',
            })
            print(f"    {month}: ${cost:.2f}")
    return rows


def store(rows):
    conn = psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', 5432),
        database=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
    )
    sql = f"""
        INSERT INTO {TABLE} (provider, account_id, service, usage_date, cost, currency, raw_data)
        VALUES (%(provider)s, %(account_id)s, %(service)s, %(usage_date)s, %(cost)s, %(currency)s, %(raw_data)s::jsonb)
        ON CONFLICT DO NOTHING
    """
    with conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    print(f"Stored {len(rows)} records in {TABLE}")


def main():
    months = int(os.environ.get('FINOPS_MONTHS', 3))
    skip_db = os.getenv('SKIP_INSERT_INTO_DB', 'False') == 'True'

    try:
        rows = collect(months)
        if not rows:
            print("No billing data retrieved.")
            return

        if skip_db:
            print(f"\nDry-run — {len(rows)} records would be inserted:")
            for r in rows[:5]:
                print(f"  {r['usage_date']} {r['account_id']}: ${r['cost']}")
            if len(rows) > 5:
                print(f"  ... and {len(rows) - 5} more")
        else:
            store(rows)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
