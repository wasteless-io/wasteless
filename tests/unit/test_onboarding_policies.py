"""
Guard tests for the onboarding artifacts (onboarding/).

The JSON policies are the source of truth. The Terraform module reads
them via file(), so it cannot drift; the CloudFormation template inlines
them (required for Launch Stack), so these tests fail on any divergence.
"""

import json
import os
import re

import pytest
import yaml

ONBOARDING = os.path.join(os.path.dirname(__file__), '..', '..', 'onboarding')
READONLY_JSON = os.path.join(ONBOARDING, 'policies', 'readonly.json')
REMEDIATION_JSON = os.path.join(ONBOARDING, 'policies', 'remediation.json')
CFN_TEMPLATE = os.path.join(ONBOARDING, 'cloudformation',
                            'wasteless-onboarding.yaml')
TF_MAIN = os.path.join(ONBOARDING, 'terraform', 'main.tf')


def load_json(path):
    with open(path) as f:
        return json.load(f)


class CfnLoader(yaml.SafeLoader):
    """YAML loader tolerant to CloudFormation short-form tags (!Ref, !If...)."""


def _cfn_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CfnLoader.add_multi_constructor('!', _cfn_tag)


def load_cfn():
    with open(CFN_TEMPLATE) as f:
        return yaml.load(f, Loader=CfnLoader)


def statements_by_sid(policy_doc):
    return {s['Sid']: s for s in policy_doc['Statement']}


class TestPolicyJsons:
    def test_both_policies_parse(self):
        assert load_json(READONLY_JSON)['Version'] == '2012-10-17'
        assert load_json(REMEDIATION_JSON)['Version'] == '2012-10-17'

    def test_readonly_covers_every_detector_read_call(self):
        """One entry per AWS read call a detector actually makes.

        Found the hard way: vpc_unused's Steampipe query calls
        ec2:DescribeNetworkInterfaces, which was missing from this policy —
        a real onboarded client would have had this detector fail on every
        run. No other test caught it because the existing guards only check
        internal consistency (CFN matches JSON), never detector-to-policy
        coverage. Extend this set whenever a detector starts using a new
        read-only AWS call.
        """
        required_actions = {
            'ec2:DescribeAddresses',    # eip_orphan
            'ec2:DescribeImages',       # snapshot_orphan (AMI-backed exclusion)
            'ec2:DescribeInstances',    # ec2_idle, ec2_stopped
            'ec2:DescribeNatGateways',  # nat_gateway_unused
            'ec2:DescribeNetworkInterfaces',  # vpc_unused
            'ec2:DescribeSnapshots',    # snapshot_orphan
            'ec2:DescribeVolumes',      # ebs_orphan, ebs_gp2_migration
            'ec2:DescribeVpcs',         # vpc_unused
            'elasticloadbalancing:DescribeLoadBalancers',  # elb_unused
            'elasticloadbalancing:DescribeTargetGroups',   # elb_unused
            'elasticloadbalancing:DescribeTargetHealth',   # elb_unused
        }
        granted = {a for s in load_json(READONLY_JSON)['Statement']
                   for a in s['Action']}
        missing = required_actions - granted
        assert not missing, f"Detector(s) need actions missing from readonly.json: {missing}"

    def test_readonly_actions_are_read_only(self):
        readonly = load_json(READONLY_JSON)
        pattern = re.compile(
            r'^(ce|cloudwatch|ec2|elasticloadbalancing|s3):(Describe|Get|List)'
        )
        for statement in readonly['Statement']:
            for action in statement['Action']:
                assert pattern.match(action), (
                    f"Non read-only action in readonly.json: {action}"
                )

    def test_remediation_has_no_iam_or_wildcard_actions(self):
        remediation = load_json(REMEDIATION_JSON)
        for statement in remediation['Statement']:
            for action in statement['Action']:
                assert '*' not in action, f"Wildcard action: {action}"
                assert not action.startswith('iam:'), f"IAM action: {action}"

    def test_rollback_snapshot_tagging_is_scoped(self):
        statements = statements_by_sid(load_json(REMEDIATION_JSON))
        tag = statements['TagRollbackSnapshots']
        assert tag['Resource'] == 'arn:aws:ec2:*::snapshot/*'
        assert tag['Condition'] == {
            'StringEquals': {'ec2:CreateAction': 'CreateSnapshot'}
        }


class TestCloudFormationDriftGuard:
    """The inline CFN policies must match the JSON files exactly."""

    @staticmethod
    def _inline_policies(role):
        return {p['PolicyName']: p['PolicyDocument']
                for p in role['Properties']['Policies']}

    def test_readonly_role_matches_json(self):
        template = load_cfn()
        role = template['Resources']['WastelessReadOnlyRole']
        inline = self._inline_policies(role)['wasteless-readonly']
        assert inline['Statement'] == load_json(READONLY_JSON)['Statement']

    def test_remediation_role_matches_both_jsons(self):
        template = load_cfn()
        role = template['Resources']['WastelessRemediationRole']
        inline = self._inline_policies(role)
        assert (inline['wasteless-readonly']['Statement']
                == load_json(READONLY_JSON)['Statement'])
        assert (inline['wasteless-remediation']['Statement']
                == load_json(REMEDIATION_JSON)['Statement'])

    def test_roles_have_bounded_session_duration(self):
        template = load_cfn()
        for name in ('WastelessReadOnlyRole', 'WastelessRemediationRole'):
            role = template['Resources'][name]
            assert role['Properties']['MaxSessionDuration'] == 3600


class TestTerraformModule:
    def test_file_references_exist(self):
        with open(TF_MAIN) as f:
            content = f.read()
        refs = re.findall(r'file\("\$\{path\.module\}/(.+?)"\)', content)
        assert refs, "No file() reference found in main.tf"
        module_dir = os.path.join(ONBOARDING, 'terraform')
        for ref in refs:
            path = os.path.normpath(os.path.join(module_dir, ref))
            assert os.path.isfile(path), f"file() target missing: {ref}"
