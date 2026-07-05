"""
Unit tests for the Terraform mapper (AWS ID -> Terraform address -> HCL block).
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from remediators.terraform_mapper import (
    TerraformMapper,
    TerraformMappingError,
    locate_block,
)


# Shape of `terraform show -json` for the wasteless-tf-fixtures repo.
STATE = {
    "format_version": "1.0",
    "values": {
        "root_module": {
            "resources": [
                {
                    "address": "aws_eip.orphan",
                    "mode": "managed",
                    "type": "aws_eip",
                    "name": "orphan",
                    "values": {
                        "id": "eipalloc-0aaa111",
                        "allocation_id": "eipalloc-0aaa111",
                        "public_ip": "52.1.2.3",
                    },
                },
                {
                    "address": "aws_nat_gateway.unused",
                    "mode": "managed",
                    "type": "aws_nat_gateway",
                    "name": "unused",
                    "values": {
                        "id": "nat-0bbb222",
                    },
                },
                {
                    "address": "data.aws_ami.ubuntu",
                    "mode": "data",
                    "type": "aws_ami",
                    "name": "ubuntu",
                    "values": {"id": "ami-0ccc333"},
                },
            ],
            "child_modules": [
                {
                    "address": "module.network",
                    "resources": [
                        {
                            "address": "module.network.aws_eip.nested",
                            "mode": "managed",
                            "type": "aws_eip",
                            "name": "nested",
                            "values": {"id": "eipalloc-0ddd444"},
                        }
                    ],
                }
            ],
        }
    },
}

WASTE_TF = '''resource "aws_eip" "orphan" {
  domain = "vpc"

  tags = {
    Name = "wasteless-fixture-eip-orphan"
  }
}

resource "aws_nat_gateway" "unused" {
  allocation_id = aws_eip.orphan.id
  subnet_id     = "subnet-123"
}
'''


@pytest.fixture
def tf_dir(tmp_path):
    (tmp_path / "waste.tf").write_text(WASTE_TF)
    return str(tmp_path)


class TestTerraformMapperIndex:
    """Tests for state indexing and ID lookup."""

    def test_find_by_primary_id(self):
        mapper = TerraformMapper(STATE)
        resource = mapper.find_by_id("eipalloc-0aaa111")
        assert resource is not None
        assert resource.address == "aws_eip.orphan"
        assert resource.resource_type == "aws_eip"
        assert resource.name == "orphan"

    def test_find_by_secondary_id_public_ip(self):
        mapper = TerraformMapper(STATE)
        resource = mapper.find_by_id("52.1.2.3")
        assert resource is not None
        assert resource.address == "aws_eip.orphan"

    def test_find_nat_gateway(self):
        mapper = TerraformMapper(STATE)
        resource = mapper.find_by_id("nat-0bbb222")
        assert resource is not None
        assert resource.address == "aws_nat_gateway.unused"

    def test_unmanaged_resource_returns_none(self):
        mapper = TerraformMapper(STATE)
        assert mapper.find_by_id("i-not-in-state") is None

    def test_data_sources_are_ignored(self):
        mapper = TerraformMapper(STATE)
        assert mapper.find_by_id("ami-0ccc333") is None

    def test_child_module_resource_is_indexed(self):
        mapper = TerraformMapper(STATE)
        resource = mapper.find_by_id("eipalloc-0ddd444")
        assert resource is not None
        assert resource.module_path == "module.network"
        assert not resource.in_root_module

    def test_empty_state(self):
        mapper = TerraformMapper({})
        assert mapper.find_by_id("eipalloc-0aaa111") is None


class TestLocateBlock:
    """Tests for HCL block location (file + line range)."""

    def test_locates_first_block(self, tf_dir):
        location = locate_block(tf_dir, "aws_eip", "orphan")
        assert location == ("waste.tf", 1, 7)

    def test_locates_second_block(self, tf_dir):
        location = locate_block(tf_dir, "aws_nat_gateway", "unused")
        assert location == ("waste.tf", 9, 12)

    def test_missing_block_returns_none(self, tf_dir):
        assert locate_block(tf_dir, "aws_eip", "does_not_exist") is None

    def test_missing_directory_raises(self):
        with pytest.raises(TerraformMappingError):
            locate_block("/nonexistent/dir", "aws_eip", "orphan")

    def test_same_name_different_type_not_confused(self, tf_dir):
        location = locate_block(tf_dir, "aws_nat_gateway", "orphan")
        assert location is None


class TestLocate:
    """Tests for the end-to-end locate (state + files)."""

    def test_locate_resolves_file_and_lines(self, tf_dir):
        mapper = TerraformMapper(STATE)
        resource = mapper.locate("eipalloc-0aaa111", tf_dir)
        assert resource.located
        assert resource.file == "waste.tf"
        assert (resource.start_line, resource.end_line) == (1, 7)

    def test_locate_unmanaged_returns_none(self, tf_dir):
        mapper = TerraformMapper(STATE)
        assert mapper.locate("i-unknown", tf_dir) is None

    def test_locate_child_module_returns_resource_without_file(self, tf_dir):
        mapper = TerraformMapper(STATE)
        resource = mapper.locate("eipalloc-0ddd444", tf_dir)
        assert resource is not None
        assert not resource.located

    def test_locate_state_only_resource_has_no_file(self, tmp_path):
        # In the state but block absent from the .tf files (e.g. moved/renamed)
        (tmp_path / "empty.tf").write_text("# nothing here\n")
        mapper = TerraformMapper(STATE)
        resource = mapper.locate("nat-0bbb222", str(tmp_path))
        assert resource is not None
        assert not resource.located
