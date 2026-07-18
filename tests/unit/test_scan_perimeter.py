#!/usr/bin/env python3
"""Every boto3 detector must scan the same perimeter as the rest of the
pipeline. Three detectors used to keep a private hardcoded region list,
which silently shrank their coverage when constants.AWS_SCAN_REGIONS
grew (found when EBS volumes in newly added regions went undetected
while the same-day us-east-1 ones were flagged)."""

import constants
from detectors import ebs_orphan, ec2_stopped, eip_orphan, snapshot_orphan


def test_boto3_detectors_share_the_pipeline_scan_perimeter():
    for module in (ebs_orphan, eip_orphan, snapshot_orphan, ec2_stopped):
        assert module.REGIONS is constants.AWS_SCAN_REGIONS, module.__name__
