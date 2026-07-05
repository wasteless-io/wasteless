#!/usr/bin/env python3
"""
Terraform Mapper for Wasteless

Maps a live AWS resource ID (i-xxx, vol-xxx, eipalloc-xxx, nat-xxx...)
back to the Terraform resource that declares it:
  1. Parse `terraform show -json` output (state) to find the resource address.
  2. Locate the corresponding HCL block (file + line range) in the .tf files.

This is the foundation of the Terraform-PR remediation flow: once a block
is located, a diff removing or modifying it can be generated and proposed
as a GitHub pull request.

Author: Wasteless
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# State attributes that can identify a resource besides its primary `id`.
SECONDARY_ID_ATTRIBUTES = ('arn', 'allocation_id', 'public_ip', 'association_id')

RESOURCE_HEADER_RE = re.compile(
    r'^\s*resource\s+"(?P<type>[\w-]+)"\s+"(?P<name>[\w-]+)"\s*\{'
)


class TerraformMappingError(Exception):
    """Raised when the state or the HCL files cannot be read or parsed."""
    pass


@dataclass
class TerraformResource:
    """A Terraform-managed resource matched from the state."""
    address: str            # e.g. module.network.aws_eip.orphan[0]
    resource_type: str      # e.g. aws_eip
    name: str               # e.g. orphan
    resource_id: str        # live AWS ID, e.g. eipalloc-0abc...
    module_path: str = ''   # e.g. module.network ('' = root module)
    file: Optional[str] = None      # .tf file declaring the block (root module only)
    start_line: Optional[int] = None
    end_line: Optional[int] = None

    @property
    def in_root_module(self) -> bool:
        return self.module_path == ''

    @property
    def located(self) -> bool:
        return self.file is not None


class TerraformMapper:
    """Index a Terraform state and locate resources in HCL source files."""

    def __init__(self, state: Dict):
        self._by_id: Dict[str, TerraformResource] = {}
        root = (state.get('values') or {}).get('root_module') or {}
        self._index_module(root, module_path='')

    @classmethod
    def from_state_file(cls, path: str) -> 'TerraformMapper':
        """Build a mapper from a saved `terraform show -json` output."""
        try:
            with open(path, 'r') as f:
                return cls(json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            raise TerraformMappingError(f"Cannot read state file {path}: {e}")

    @classmethod
    def from_terraform_dir(cls, terraform_dir: str) -> 'TerraformMapper':
        """Run `terraform show -json` in a directory and index its state."""
        try:
            result = subprocess.run(
                ['terraform', 'show', '-json'],
                cwd=terraform_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise TerraformMappingError(f"terraform show failed in {terraform_dir}: {e}")

        if result.returncode != 0:
            raise TerraformMappingError(
                f"terraform show failed in {terraform_dir}: {result.stderr.strip()}"
            )

        try:
            return cls(json.loads(result.stdout))
        except json.JSONDecodeError as e:
            raise TerraformMappingError(f"Invalid terraform show output: {e}")

    def _index_module(self, module: Dict, module_path: str) -> None:
        for res in module.get('resources') or []:
            # Data sources and non-AWS providers are not remediation targets.
            if res.get('mode') != 'managed':
                continue
            values = res.get('values') or {}
            resource = TerraformResource(
                address=res.get('address', ''),
                resource_type=res.get('type', ''),
                name=res.get('name', ''),
                resource_id=str(values.get('id', '')),
                module_path=module_path,
            )
            for key in ('id',) + SECONDARY_ID_ATTRIBUTES:
                value = values.get(key)
                if value and isinstance(value, str):
                    self._by_id.setdefault(value, resource)

        for child in module.get('child_modules') or []:
            self._index_module(child, module_path=child.get('address', ''))

    def find_by_id(self, resource_id: str) -> Optional[TerraformResource]:
        """Return the Terraform resource declaring this AWS ID, if managed."""
        return self._by_id.get(resource_id)

    def locate(self, resource_id: str, terraform_dir: str) -> Optional[TerraformResource]:
        """
        Find a resource by AWS ID and resolve its HCL block (file + lines).

        Resources declared inside child modules are returned without file
        location (v1 only edits the root module; callers must fall back to
        the API remediation path when `located` is False).
        """
        resource = self.find_by_id(resource_id)
        if resource is None:
            logger.info(f"Resource {resource_id} is not managed by this Terraform state")
            return None

        if not resource.in_root_module:
            logger.info(
                f"Resource {resource_id} ({resource.address}) lives in a child "
                f"module — file location not supported yet"
            )
            return resource

        location = locate_block(terraform_dir, resource.resource_type, resource.name)
        if location:
            resource.file, resource.start_line, resource.end_line = location
        else:
            logger.warning(
                f"Resource {resource.address} is in the state but its block was "
                f"not found in {terraform_dir}/*.tf"
            )
        return resource


def locate_block(terraform_dir: str, resource_type: str,
                 name: str) -> Optional[tuple]:
    """
    Locate a `resource "<type>" "<name>"` block in the .tf files of a directory.

    Returns (relative_file_path, start_line, end_line) with 1-based inclusive
    lines, or None if not found. Lines are tracked by brace counting, which is
    sufficient for standard HCL (heredocs containing unbalanced braces are the
    known limitation).
    """
    directory = Path(terraform_dir)
    if not directory.is_dir():
        raise TerraformMappingError(f"Not a directory: {terraform_dir}")

    for tf_file in sorted(directory.glob('*.tf')):
        try:
            lines = tf_file.read_text().splitlines()
        except OSError as e:
            logger.warning(f"Cannot read {tf_file}: {e}")
            continue

        for i, line in enumerate(lines):
            match = RESOURCE_HEADER_RE.match(line)
            if not match:
                continue
            if match.group('type') != resource_type or match.group('name') != name:
                continue

            depth = 0
            for j in range(i, len(lines)):
                depth += lines[j].count('{') - lines[j].count('}')
                if depth == 0:
                    return (tf_file.name, i + 1, j + 1)
            raise TerraformMappingError(
                f"Unbalanced braces in {tf_file} at line {i + 1}"
            )

    return None
