"""
Decision logic of scripts/windows_diagnostics.ps1, fed with simulated machine
states: these failures can't be reproduced on a CI runner. Runs on Windows only,
in the Windows PowerShell 5.1 the scripts target.
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell only")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows_diagnostics.ps1"

HEALTHY = {
    "FirmwareEnabled": "$true",
    "HypervisorPresent": "$true",
    "Vmp": "1",
    "Wsl": "1",
    "HyperV": "2",
    "VmCompute": "$true",
    "RebootPending": "$false",
}


def issue_code(**changes):
    """The issue Code for HEALTHY overridden with `changes`, "" when none."""
    state = "; ".join(f"{k} = {v}" for k, v in {**HEALTHY, **changes}.items())
    command = (
        f". '{SCRIPT}'; $i = Get-VirtualizationIssue @{{ {state} }}; "
        "if ($i) { $i.Code } else { '' }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, ""),
        # The firmware flag can read false while the hypervisor is running
        ({"FirmwareEnabled": "$false"}, ""),
        ({"HypervisorPresent": "$false", "FirmwareEnabled": "$false"}, "firmware"),
        ({"HypervisorPresent": "$false", "Vmp": "2"}, "features"),
        ({"HypervisorPresent": "$false", "Wsl": "2"}, "features"),
        # Docker Desktop's Hyper-V backend does not need the WSL components
        ({"Vmp": "2", "Wsl": "2", "HyperV": "1"}, ""),
        # Reported enabled while still queued: seen on a real machine
        ({"HypervisorPresent": "$false", "VmCompute": "$false"}, "pending"),
        ({"HypervisorPresent": "$false", "RebootPending": "$true"}, "reboot"),
        ({"HypervisorPresent": "$false"}, "hypervisor"),
        # What could not be read is never a problem
        ({"Vmp": "$null", "Wsl": "$null", "HyperV": "$null"}, ""),
        ({"HypervisorPresent": "$null", "FirmwareEnabled": "$null"}, ""),
    ],
)
def test_issue_detection(changes, expected):
    assert issue_code(**changes) == expected
