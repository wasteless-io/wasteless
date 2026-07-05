#!/usr/bin/env python3
"""
Terraform Editor for Wasteless

Generates the code change proposed in a remediation PR, from a block
location resolved by terraform_mapper:
  - remove a whole resource block (delete_volume, delete_nat_gateway...)
  - edit one attribute in place (migrate_gp2_to_gp3)

Edits are applied to a working copy of the Terraform repo (the PR flow
clones into a temp dir); each edit returns a unified diff for the PR body.
Before opening a PR, callers must check `find_references` (a removed
resource still referenced elsewhere would break the plan) and run
`validate_directory`.

Author: Wasteless
"""

import difflib
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class TerraformEditError(Exception):
    """Raised when an edit cannot be applied safely."""
    pass


@dataclass
class BlockEdit:
    """An applied edit and its unified diff (for the PR body)."""
    file: str
    unified_diff: str


def remove_block(terraform_dir: str, file: str, start_line: int,
                 end_line: int) -> BlockEdit:
    """
    Remove lines [start_line, end_line] (1-based, inclusive) from a .tf file,
    along with any blank lines immediately following, and write it back.
    """
    path = _resolve(terraform_dir, file)
    old_lines = path.read_text().splitlines(keepends=True)
    _check_range(old_lines, start_line, end_line, path)

    end = end_line
    while end < len(old_lines) and old_lines[end].strip() == '':
        end += 1

    new_lines = old_lines[:start_line - 1] + old_lines[end:]
    path.write_text(''.join(new_lines))
    logger.info(f"Removed lines {start_line}-{end_line} from {path}")
    return BlockEdit(file=file, unified_diff=_diff(old_lines, new_lines, file))


def set_block_attribute(terraform_dir: str, file: str, start_line: int,
                        end_line: int, attribute: str,
                        new_value: str) -> BlockEdit:
    """
    Replace the value of a top-level attribute inside a block, e.g.
    type = "gp2" -> type = "gp3". `new_value` is inserted as-is, so quote
    string literals ('"gp3"').
    """
    path = _resolve(terraform_dir, file)
    old_lines = path.read_text().splitlines(keepends=True)
    _check_range(old_lines, start_line, end_line, path)

    attr_re = re.compile(
        rf'^(?P<prefix>\s*{re.escape(attribute)}\s*=\s*)(?P<value>.+?)(?P<suffix>\s*)$'
    )
    new_lines = list(old_lines)
    replaced = False
    for i in range(start_line - 1, end_line):
        match = attr_re.match(old_lines[i].rstrip('\n'))
        if match:
            new_lines[i] = f"{match.group('prefix')}{new_value}\n"
            replaced = True
            break

    if not replaced:
        raise TerraformEditError(
            f"Attribute '{attribute}' not found in {file}:{start_line}-{end_line}"
        )

    path.write_text(''.join(new_lines))
    logger.info(f"Set {attribute} = {new_value} in {file}:{start_line}-{end_line}")
    return BlockEdit(file=file, unified_diff=_diff(old_lines, new_lines, file))


def find_references(terraform_dir: str, resource_type: str, name: str,
                    exclude_file: Optional[str] = None,
                    exclude_range: Optional[Tuple[int, int]] = None) -> List[Tuple[str, int]]:
    """
    Find HCL expressions referencing `<resource_type>.<name>` in the .tf files
    (e.g. aws_eip.nat.id in another block). Returns (file, line) pairs,
    excluding the resource's own block. A non-empty result means removing the
    block would leave dangling references — the PR flow must abort.
    """
    ref_re = re.compile(rf'\b{re.escape(resource_type)}\.{re.escape(name)}\b')
    references = []

    for tf_file in sorted(Path(terraform_dir).glob('*.tf')):
        for i, line in enumerate(tf_file.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//'):
                continue
            if not ref_re.search(line):
                continue
            if (tf_file.name == exclude_file and exclude_range
                    and exclude_range[0] <= i <= exclude_range[1]):
                continue
            # The block's own header (resource "type" "name") is not a reference.
            if re.match(rf'\s*resource\s+"{re.escape(resource_type)}"\s+"{re.escape(name)}"', line):
                continue
            references.append((tf_file.name, i))

    return references


def validate_directory(terraform_dir: str) -> Tuple[bool, str]:
    """
    Run `terraform validate` on the edited working copy. Returns (ok, message).
    Assumes `terraform init` has already been run in the directory (the PR
    flow inits once after cloning).
    """
    try:
        result = subprocess.run(
            ['terraform', 'validate', '-no-color'],
            cwd=terraform_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"terraform validate could not run: {e}"

    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def _resolve(terraform_dir: str, file: str) -> Path:
    path = Path(terraform_dir) / file
    if not path.is_file():
        raise TerraformEditError(f"No such file: {path}")
    return path


def _check_range(lines: List[str], start_line: int, end_line: int, path: Path) -> None:
    if not (1 <= start_line <= end_line <= len(lines)):
        raise TerraformEditError(
            f"Invalid line range {start_line}-{end_line} for {path} "
            f"({len(lines)} lines)"
        )


def _diff(old_lines: List[str], new_lines: List[str], file: str) -> str:
    return ''.join(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{file}", tofile=f"b/{file}"
    ))
