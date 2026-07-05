"""
Unit tests for the Terraform editor (block removal, attribute edit,
reference detection, validation).
"""

import shutil

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from remediators.terraform_editor import (
    TerraformEditError,
    find_references,
    remove_block,
    set_block_attribute,
    validate_directory,
)

WASTE_TF = '''resource "aws_eip" "orphan" {
  domain = "vpc"

  tags = {
    Name = "wasteless-fixture-eip-orphan"
  }
}

resource "aws_ebs_volume" "slow" {
  availability_zone = "eu-west-1a"
  size              = 100
  type              = "gp2"
}
'''

NAT_TF = '''resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "unused" {
  allocation_id = aws_eip.nat.id
  subnet_id     = "subnet-123"
}
'''


@pytest.fixture
def tf_dir(tmp_path):
    (tmp_path / "waste.tf").write_text(WASTE_TF)
    (tmp_path / "nat.tf").write_text(NAT_TF)
    return str(tmp_path)


class TestRemoveBlock:

    def test_removes_block_and_trailing_blank(self, tf_dir):
        edit = remove_block(tf_dir, "waste.tf", 1, 7)
        content = (open(f"{tf_dir}/waste.tf").read())
        assert content.startswith('resource "aws_ebs_volume" "slow"')
        assert "aws_eip" not in content

    def test_unified_diff_shows_removed_lines(self, tf_dir):
        edit = remove_block(tf_dir, "waste.tf", 1, 7)
        assert edit.unified_diff.startswith("--- a/waste.tf")
        assert '-resource "aws_eip" "orphan" {' in edit.unified_diff
        assert '+resource' not in edit.unified_diff

    def test_remove_last_block_keeps_others(self, tf_dir):
        remove_block(tf_dir, "waste.tf", 9, 13)
        content = open(f"{tf_dir}/waste.tf").read()
        assert 'resource "aws_eip" "orphan"' in content
        assert "aws_ebs_volume" not in content

    def test_invalid_range_raises(self, tf_dir):
        with pytest.raises(TerraformEditError):
            remove_block(tf_dir, "waste.tf", 10, 99)

    def test_missing_file_raises(self, tf_dir):
        with pytest.raises(TerraformEditError):
            remove_block(tf_dir, "nope.tf", 1, 2)


class TestSetBlockAttribute:

    def test_gp2_to_gp3(self, tf_dir):
        edit = set_block_attribute(tf_dir, "waste.tf", 9, 13, "type", '"gp3"')
        content = open(f"{tf_dir}/waste.tf").read()
        assert 'type              = "gp3"' in content
        assert '"gp2"' not in content
        assert '-  type              = "gp2"' in edit.unified_diff
        assert '+  type              = "gp3"' in edit.unified_diff

    def test_only_edits_inside_range(self, tf_dir):
        # 'domain' exists in the EIP block (lines 1-7), not the volume block
        with pytest.raises(TerraformEditError):
            set_block_attribute(tf_dir, "waste.tf", 9, 13, "domain", '"vpc"')

    def test_missing_attribute_raises(self, tf_dir):
        with pytest.raises(TerraformEditError):
            set_block_attribute(tf_dir, "waste.tf", 1, 7, "iops", "3000")


class TestFindReferences:

    def test_finds_cross_block_reference(self, tf_dir):
        refs = find_references(tf_dir, "aws_eip", "nat",
                               exclude_file="nat.tf", exclude_range=(1, 3))
        assert refs == [("nat.tf", 6)]

    def test_no_references_for_orphan(self, tf_dir):
        refs = find_references(tf_dir, "aws_eip", "orphan",
                               exclude_file="waste.tf", exclude_range=(1, 7))
        assert refs == []

    def test_own_block_header_is_not_a_reference(self, tf_dir):
        refs = find_references(tf_dir, "aws_nat_gateway", "unused",
                               exclude_file="nat.tf", exclude_range=(5, 8))
        assert refs == []

    def test_comments_are_ignored(self, tmp_path):
        (tmp_path / "main.tf").write_text(
            '# aws_eip.legacy was removed\nresource "aws_eip" "other" {}\n'
        )
        assert find_references(str(tmp_path), "aws_eip", "legacy") == []


@pytest.mark.skipif(shutil.which("terraform") is None,
                    reason="terraform CLI not installed")
class TestValidateDirectory:

    def test_valid_config(self, tmp_path):
        (tmp_path / "main.tf").write_text('variable "x" {\n  default = 1\n}\n')
        ok, message = validate_directory(str(tmp_path))
        assert ok, message

    def test_dangling_reference_fails(self, tmp_path):
        # What validate must catch: block removed but still referenced
        (tmp_path / "main.tf").write_text(
            'output "ip" {\n  value = aws_eip.gone.public_ip\n}\n'
        )
        ok, message = validate_directory(str(tmp_path))
        assert not ok
        assert "aws_eip" in message
