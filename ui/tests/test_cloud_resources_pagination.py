#!/usr/bin/env python3
"""
Regression test for GET /cloud-resources (ui/main.py) pagination.

Before this fix, _fetch_ec2/_fetch_volumes/_fetch_snapshots called
describe_instances/describe_volumes/describe_snapshots directly with no
paginator. AWS returns at most one page (commonly capped around 1000
items) without NextToken handling, so an account with more resources
than that in a single region would silently lose the rest from this
inventory page — no error, just missing rows. Locks in that every page
returned by get_paginator(...).paginate() is now consumed.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient
    TESTCLIENT_AVAILABLE = True
except ImportError:
    TESTCLIENT_AVAILABLE = False


def _paginator_for(op_name, pages_by_op):
    paginator = MagicMock()
    paginator.paginate.return_value = pages_by_op[op_name]
    return paginator


def _fake_ec2_client(pages_by_op):
    client = MagicMock()
    client.get_paginator.side_effect = lambda op: _paginator_for(op, pages_by_op)
    client.describe_addresses.return_value = {'Addresses': []}
    client.describe_vpcs.return_value = {'Vpcs': []}
    return client


@unittest.skipUnless(TESTCLIENT_AVAILABLE, "fastapi.testclient not installed")
class TestCloudResourcesPagination(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from main import app
        cls.client = TestClient(app)

    def test_ec2_instances_from_every_page_are_included(self):
        pages = {
            'describe_instances': [
                {'Reservations': [{'Instances': [
                    {'InstanceId': 'i-page1', 'State': {'Name': 'running'},
                     'InstanceType': 't3.micro'}
                ]}]},
                {'Reservations': [{'Instances': [
                    {'InstanceId': 'i-page2', 'State': {'Name': 'running'},
                     'InstanceType': 't3.micro'}
                ]}]},
            ],
            'describe_volumes': [{'Volumes': []}],
            'describe_snapshots': [{'Snapshots': []}],
        }
        fake_client = _fake_ec2_client(pages)
        s3_client = MagicMock()
        s3_client.list_buckets.return_value = {'Buckets': []}

        def _get_client(service, region=None):
            return s3_client if service == 's3' else fake_client

        with patch('utils.aws_clients.get_client', side_effect=_get_client):
            resp = self.client.get("/cloud-resources", params={"tab": "ec2"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn('i-page1', resp.text)
        self.assertIn('i-page2', resp.text)

    def test_ebs_volumes_from_every_page_are_included(self):
        pages = {
            'describe_instances': [{'Reservations': []}],
            'describe_volumes': [
                {'Volumes': [{'VolumeId': 'vol-page1', 'Size': 10,
                              'State': 'available', 'VolumeType': 'gp3',
                              'AvailabilityZone': 'eu-west-1a'}]},
                {'Volumes': [{'VolumeId': 'vol-page2', 'Size': 20,
                              'State': 'available', 'VolumeType': 'gp3',
                              'AvailabilityZone': 'eu-west-1a'}]},
            ],
            'describe_snapshots': [{'Snapshots': []}],
        }
        fake_client = _fake_ec2_client(pages)
        s3_client = MagicMock()
        s3_client.list_buckets.return_value = {'Buckets': []}

        def _get_client(service, region=None):
            return s3_client if service == 's3' else fake_client

        with patch('utils.aws_clients.get_client', side_effect=_get_client):
            resp = self.client.get("/cloud-resources", params={"tab": "ebs"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn('vol-page1', resp.text)
        self.assertIn('vol-page2', resp.text)

    def test_snapshots_from_every_page_are_included(self):
        pages = {
            'describe_instances': [{'Reservations': []}],
            'describe_volumes': [{'Volumes': []}],
            'describe_snapshots': [
                {'Snapshots': [{'SnapshotId': 'snap-page1', 'State': 'completed',
                               'VolumeSize': 8}]},
                {'Snapshots': [{'SnapshotId': 'snap-page2', 'State': 'completed',
                               'VolumeSize': 8}]},
            ],
        }
        fake_client = _fake_ec2_client(pages)
        s3_client = MagicMock()
        s3_client.list_buckets.return_value = {'Buckets': []}

        def _get_client(service, region=None):
            return s3_client if service == 's3' else fake_client

        with patch('utils.aws_clients.get_client', side_effect=_get_client):
            resp = self.client.get("/cloud-resources", params={"tab": "snapshots"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn('snap-page1', resp.text)
        self.assertIn('snap-page2', resp.text)


if __name__ == '__main__':
    unittest.main()
