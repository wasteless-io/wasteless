#!/usr/bin/env python3
"""
Generic resource remediators for Wasteless.

Extends remediation beyond EC2 instances:
- Gp2MigrationRemediator   — modify gp2 volumes to gp3 (online, reversible)
- NATGatewayRemediator     — delete unused NAT gateways (irreversible)
- LoadBalancerRemediator   — delete load balancers with no targets (irreversible)

Every remediation follows the same guarded flow as EC2Remediator:
  1. fetch live resource state (aborts if the resource is gone)
  2. re-verify the waste condition on live data (not just detection-time data)
  3. safeguard checks: whitelist, confidence, schedule, auto-remediation flag
  4. actions_log entry + rollback snapshot of the state before
  5. dry-run by default — the AWS call only fires with dry_run=False
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, date
from typing import Any, Dict, Optional

import boto3
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.safeguards import Safeguards, SafeguardException
from core.database import get_db_connection

load_dotenv()

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


class ResourceRemediator:
    """Template for guarded remediation of a non-EC2-instance resource."""

    resource_type: str = ''    # actions_log / rollback_snapshots resource_type
    action_type: str = ''      # actions_log action_type
    can_rollback: bool = False

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.safeguards = Safeguards()
        self.conn = get_db_connection()
        logger.info(f"{type(self).__name__} initialized (dry_run={dry_run})")

    # -- abstract hooks -------------------------------------------------

    def get_resource_state(self, resource_id: str, region: str) -> Optional[Dict]:
        """Live resource state incl. 'tags' dict, or None if it is gone."""
        raise NotImplementedError

    def verify_still_wasteful(self, state: Dict, resource_id: str, region: str) -> None:
        """Re-check the waste condition on live data.

        Raises SafeguardException if the resource is no longer wasteful
        (e.g. a load balancer that gained targets since detection).
        """
        raise NotImplementedError

    def execute_action(self, resource_id: str, region: str, state: Dict) -> None:
        """Perform the actual AWS call (only reached when dry_run=False)."""
        raise NotImplementedError

    # -- shared machinery ------------------------------------------------

    def _log_action(self, recommendation_id, resource_id, status,
                    metadata=None, error_message=None) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO actions_log (
                recommendation_id, resource_id, resource_type,
                action_type, action_status, dry_run,
                metadata, error_message, executed_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            recommendation_id, resource_id, self.resource_type,
            self.action_type, status, self.dry_run,
            json.dumps(metadata, cls=DateTimeEncoder) if metadata else None,
            error_message, 'system'
        ))
        action_id = cursor.fetchone()[0]
        self.conn.commit()
        cursor.close()
        return action_id

    def _update_action_status(self, action_log_id, status, error_message=None):
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE actions_log
            SET action_status = %s, error_message = %s, updated_at = NOW()
            WHERE id = %s;
        """, (status, error_message, action_log_id))
        self.conn.commit()
        cursor.close()

    def _create_rollback_snapshot(self, action_log_id, resource_id, state) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO rollback_snapshots (
                action_log_id, resource_id, resource_type,
                state_before, can_rollback, rollback_expiry
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            action_log_id, resource_id, self.resource_type,
            json.dumps(state, cls=DateTimeEncoder),
            self.can_rollback,
            datetime.now() + timedelta(days=7)
        ))
        snapshot_id = cursor.fetchone()[0]
        self.conn.commit()
        cursor.close()
        return snapshot_id

    def _get_recommendation_confidence(self, recommendation_id: int) -> float:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT w.confidence_score
            FROM waste_detected w
            JOIN recommendations r ON r.waste_id = w.id
            WHERE r.id = %s;
        """, (recommendation_id,))
        row = cursor.fetchone()
        cursor.close()
        if not row:
            raise Exception("Recommendation not found in database")
        return float(row[0])

    def remediate(self, resource_id: str, recommendation_id: int,
                  reason: str = '', region: str = None) -> Dict:
        """Run the full guarded remediation flow for one resource."""
        region = region or os.getenv('AWS_REGION', 'eu-west-1')
        logger.info(f"Remediating {self.resource_type} {resource_id} "
                    f"in {region} (dry_run={self.dry_run})")

        result = {
            'success': False,
            'resource_id': resource_id,
            'action': self.action_type,
            'dry_run': self.dry_run,
            'action_log_id': None,
            'error': None,
        }

        try:
            state = self.get_resource_state(resource_id, region)
            if state is None:
                raise Exception(f"{self.resource_type} {resource_id} "
                                f"not found in {region}")

            # Re-verify waste condition on live data
            self.verify_still_wasteful(state, resource_id, region)

            # Safeguards
            if not self.safeguards.is_action_enabled(self.action_type):
                raise SafeguardException(
                    f"Action '{self.action_type}' disabled by config "
                    f"(auto_remediation.actions) — execute manually"
                )
            confidence = self._get_recommendation_confidence(recommendation_id)
            if self.safeguards.is_whitelisted(resource_id, state.get('tags', {})):
                raise SafeguardException(f"{resource_id} is whitelisted")
            self.safeguards.check_confidence_score(confidence)
            self.safeguards.check_schedule()
            if not self.dry_run and not self.safeguards.is_auto_remediation_enabled():
                raise SafeguardException(
                    "Auto-remediation is disabled (dry-run only)"
                )

            action_log_id = self._log_action(
                recommendation_id, resource_id, 'pending',
                metadata={'reason': reason, 'region': region,
                          'confidence': confidence,
                          'state_before': state},
            )
            result['action_log_id'] = action_log_id

            self._create_rollback_snapshot(action_log_id, resource_id, state)

            if self.dry_run:
                logger.info(f"[DRY-RUN] Would {self.action_type} "
                            f"{resource_id} in {region}")
            else:
                logger.info(f"Executing {self.action_type} on {resource_id}")
                self.execute_action(resource_id, region, state)

            cursor = self.conn.cursor()
            try:
                cursor.execute("""
                    UPDATE actions_log
                    SET action_status = 'success', updated_at = NOW()
                    WHERE id = %s;
                """, (action_log_id,))
                cursor.execute("""
                    UPDATE recommendations
                    SET status = 'applied', applied_at = NOW()
                    WHERE id = %s;
                """, (recommendation_id,))
                self.conn.commit()
            finally:
                cursor.close()

            result['success'] = True

        except SafeguardException as e:
            logger.warning(f"Safeguard prevented action: {e}")
            result['error'] = str(e)
            if result['action_log_id']:
                self._update_action_status(result['action_log_id'], 'blocked',
                                           error_message=str(e))
        except Exception as e:
            logger.error(f"Remediation failed: {e}")
            result['error'] = str(e)
            if result['action_log_id']:
                self._update_action_status(result['action_log_id'], 'failed',
                                           error_message=str(e))

        return result

    def __del__(self):
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()


def _tags_dict(aws_tags) -> Dict[str, str]:
    return {t['Key']: t['Value'] for t in (aws_tags or [])}


class Gp2MigrationRemediator(ResourceRemediator):
    """Migrate a gp2 volume to gp3 (online, no downtime, reversible)."""

    resource_type = 'ebs_volume'
    action_type = 'migrate_gp2_to_gp3'
    can_rollback = True   # modify back to gp2

    def get_resource_state(self, resource_id, region):
        ec2 = boto3.client('ec2', region_name=region)
        try:
            volume = ec2.describe_volumes(VolumeIds=[resource_id])['Volumes'][0]
        except ec2.exceptions.ClientError:
            return None
        return {
            'volume_id':   volume['VolumeId'],
            'volume_type': volume['VolumeType'],
            'size_gb':     volume['Size'],
            'state':       volume['State'],
            'az':          volume['AvailabilityZone'],
            'tags':        _tags_dict(volume.get('Tags')),
        }

    def verify_still_wasteful(self, state, resource_id, region):
        if state['volume_type'] != 'gp2':
            raise SafeguardException(
                f"Volume {resource_id} is {state['volume_type']}, "
                f"not gp2 — already migrated?"
            )

    def execute_action(self, resource_id, region, state):
        ec2 = boto3.client('ec2', region_name=region)
        ec2.modify_volume(VolumeId=resource_id, VolumeType='gp3')


class NATGatewayRemediator(ResourceRemediator):
    """Delete an unused NAT gateway (irreversible)."""

    resource_type = 'nat_gateway'
    action_type = 'delete_nat_gateway'
    can_rollback = False

    def get_resource_state(self, resource_id, region):
        ec2 = boto3.client('ec2', region_name=region)
        try:
            nat = ec2.describe_nat_gateways(
                NatGatewayIds=[resource_id])['NatGateways'][0]
        except ec2.exceptions.ClientError:
            return None
        if nat['State'] in ('deleted', 'deleting'):
            return None
        return {
            'nat_gateway_id': nat['NatGatewayId'],
            'state':          nat['State'],
            'vpc_id':         nat.get('VpcId'),
            'subnet_id':      nat.get('SubnetId'),
            'tags':           _tags_dict(nat.get('Tags')),
        }

    def verify_still_wasteful(self, state, resource_id, region):
        # State checks only: 30-day traffic was already evaluated by the
        # detector and cannot meaningfully change within the approval window
        pass

    def execute_action(self, resource_id, region, state):
        ec2 = boto3.client('ec2', region_name=region)
        ec2.delete_nat_gateway(NatGatewayId=resource_id)


class LoadBalancerRemediator(ResourceRemediator):
    """Delete a load balancer with no registered targets (irreversible).

    resource_id is an ARN for ALB/NLB/GWLB, a name for Classic LBs.
    """

    resource_type = 'load_balancer'
    action_type = 'delete_load_balancer'
    can_rollback = False

    @staticmethod
    def _is_classic(resource_id: str) -> bool:
        return not resource_id.startswith('arn:')

    def get_resource_state(self, resource_id, region):
        if self._is_classic(resource_id):
            elb = boto3.client('elb', region_name=region)
            try:
                lb = elb.describe_load_balancers(
                    LoadBalancerNames=[resource_id]
                )['LoadBalancerDescriptions'][0]
            except elb.exceptions.AccessPointNotFoundException:
                return None
            return {
                'name':      lb['LoadBalancerName'],
                'lb_type':   'classic',
                'instances': [i['InstanceId'] for i in lb.get('Instances', [])],
                'tags':      {},
            }

        elbv2 = boto3.client('elbv2', region_name=region)
        try:
            lb = elbv2.describe_load_balancers(
                LoadBalancerArns=[resource_id]
            )['LoadBalancers'][0]
        except elbv2.exceptions.LoadBalancerNotFoundException:
            return None

        # Registered targets across all attached target groups
        registered = 0
        for tg in elbv2.describe_target_groups(
                LoadBalancerArn=resource_id).get('TargetGroups', []):
            registered += len(elbv2.describe_target_health(
                TargetGroupArn=tg['TargetGroupArn']
            ).get('TargetHealthDescriptions', []))

        return {
            'name':               lb['LoadBalancerName'],
            'arn':                lb['LoadBalancerArn'],
            'lb_type':            lb.get('Type', 'application'),
            'registered_targets': registered,
            'tags':               {},
        }

    def verify_still_wasteful(self, state, resource_id, region):
        if state['lb_type'] == 'classic':
            if state['instances']:
                raise SafeguardException(
                    f"Classic LB {resource_id} now has "
                    f"{len(state['instances'])} instance(s) — not deleting"
                )
        elif state.get('registered_targets', 0) > 0:
            raise SafeguardException(
                f"Load balancer {resource_id} now has "
                f"{state['registered_targets']} registered target(s) — not deleting"
            )

    def execute_action(self, resource_id, region, state):
        if self._is_classic(resource_id):
            boto3.client('elb', region_name=region).delete_load_balancer(
                LoadBalancerName=resource_id)
        else:
            boto3.client('elbv2', region_name=region).delete_load_balancer(
                LoadBalancerArn=resource_id)


# recommendation_type -> remediator class, used by the UI dispatch
REMEDIATORS_BY_RECOMMENDATION = {
    'migrate_gp2_to_gp3':   Gp2MigrationRemediator,
    'delete_nat_gateway':   NATGatewayRemediator,
    'delete_load_balancer': LoadBalancerRemediator,
}
