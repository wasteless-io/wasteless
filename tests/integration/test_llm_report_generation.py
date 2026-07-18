"""
Golden-snapshot tests for the AWS FinOps audit report.

These tests exercise `reports.audit_report.generate_audit_report()`, a pure
Python function — NOT an LLM call. Golden-snapshot testing needs byte-stable
output, and an LLM cannot give that run to run; the deterministic report is
what an LLM-produced narrative should eventually be diffed against (see
`src/reports/prompts/audit_report_system_prompt.md`), not what performs the
comparison itself. The file is named `test_llm_report_generation.py` to match
the reporting feature it covers, not because it invokes a model.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.finops_invariants import FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS  # noqa: E402
from reports.audit_report import generate_audit_report  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "..", "snapshots")
GOLDEN_DATASET_PATH = os.path.join(FIXTURES_DIR, "golden_aws_audit_dataset.json")
GOLDEN_SNAPSHOT_PATH = os.path.join(SNAPSHOTS_DIR, "golden_aws_audit_report.md")

REQUIRED_SECTIONS = [
    "# AWS FinOps Audit Report — Wasteless",
    "## 1. Executive Summary",
    "## 2. Audit Scope",
    "## 3. Financial Overview",
    "## 4. Cost Breakdown by Service",
    "## 5. Top Recommendations",
    "## 6. Detailed Findings",
    "## 7. Risk Summary",
    "## 8. Tagging & Ownership",
    "## 9. Methodology",
    "## 10. Assumptions & Limitations",
    "## 11. Data Quality & Consistency Checks",
    "## 12. CTO-safe Summary",
]

# "confirmed savings" is deliberately excluded from this list: it is one of
# the 5 validated CTO-safe categories (see feedback-cto-safe-formulation)
# and is expected to appear as a legitimate section/row label whenever the
# report shows a Cost Explorer-verified figure. Banning the phrase outright
# would fail against our own correctly-labeled report. It is checked more
# precisely in test_confirmed_savings_only_claimed_when_verified below.
BANNED_UNCONDITIONALLY = tuple(
    w for w in FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS if w != "confirmed savings"
)


@pytest.fixture(scope="module")
def golden_dataset():
    with open(GOLDEN_DATASET_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def golden_report(golden_dataset):
    return generate_audit_report(golden_dataset)


def test_generated_audit_report_matches_golden_snapshot(golden_report):
    for section in REQUIRED_SECTIONS:
        assert section in golden_report, f"Missing section: {section}"

    with open(GOLDEN_SNAPSHOT_PATH) as f:
        expected = f.read()

    assert golden_report == expected, (
        "Report output drifted from the golden snapshot. If the change is "
        "intentional, regenerate tests/snapshots/golden_aws_audit_report.md "
        "and review the diff carefully before committing it."
    )


def test_audit_report_does_not_use_forbidden_wording(golden_report):
    normalized = golden_report.lower()
    for term in BANNED_UNCONDITIONALLY:
        assert term not in normalized, f"Forbidden term found: {term!r}"


def test_confirmed_savings_only_claimed_when_verified(golden_dataset, golden_report):
    normalized = golden_report.lower()
    assert "confirmed savings" in normalized  # legitimate category label

    # Every occurrence of the phrase must be qualified by its verification
    # source, never stated as a bare, certain claim.
    for line in normalized.splitlines():
        if "confirmed savings" in line and "cost explorer" not in line:
            # section 12's narrative sentence states the source on the
            # following clause rather than the same line; only fail on
            # table/list rows, which are self-contained.
            if line.strip().startswith(("-", "|")):
                assert "cost explorer" in line, (
                    f"'Confirmed savings' claimed without its verification " f"source: {line!r}"
                )

    if golden_dataset["confirmed_savings_monthly"] == 0:
        assert (
            "confirmed savings (verified via cost explorer): $0.00" in normalized
            or "confirmed savings (verified via cost explorer) | $0.00" in normalized
        )


def test_potential_savings_not_presented_as_realized(golden_dataset, golden_report):
    normalized = golden_report.lower()

    if golden_dataset["realized_savings_monthly"] == 0:
        assert "realized savings: $0.00" in normalized or "realized savings | $0.00" in normalized
        assert "saved $" not in normalized
        assert "you saved" not in normalized
        assert "we saved" not in normalized


def test_amounts_are_internally_consistent(golden_dataset, golden_report):
    """Cross-check the rendered figures against the source dataset rather
    than trusting the prose — a report that silently drops a decimal is
    worse than one that fails a test."""
    spend = golden_dataset["cloud_spend_monthly"]
    waste = golden_dataset["detected_waste_monthly"]
    potential = golden_dataset["potential_savings_monthly"]
    yearly = golden_dataset["potential_savings_yearly"]

    assert abs(yearly - potential * 12) < 0.01
    assert potential <= waste
    assert waste <= spend
    assert f"${waste:.2f}" in golden_report
    assert f"${potential:.2f}" in golden_report
    assert f"${yearly:.2f}" in golden_report


def test_high_and_critical_risk_recommendations_are_flagged(golden_dataset, golden_report):
    high_or_critical = [
        r for r in golden_dataset["recommendations"] if r["risk"] in ("high", "critical")
    ]
    assert high_or_critical, "Fixture must include at least one high/critical recommendation"

    assert "### Operational Red Flags" in golden_report
    for rec in high_or_critical:
        assert rec["resource_id"] in golden_report

    assert (
        "No production destructive action should be executed without explicit approval."
        in golden_report
    )


def test_assumptions_and_limitations_present(golden_report):
    section = golden_report.split("## 10. Assumptions & Limitations")[1].split("## 11.")[0]
    assert "Not provided" not in section, (
        "Golden fixture should exercise the fully-populated case; "
        "'Not provided' placeholders belong in a separate incomplete-data fixture"
    )
    for label in ("Pricing source", "Currency", "Period", "Forecast method"):
        assert label in section


def test_report_is_exportable_as_markdown(golden_report):
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "report.md")
        with open(path, "w") as f:
            f.write(golden_report)
        with open(path) as f:
            assert f.read() == golden_report


@pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason=(
        "No Markdown->PDF converter available in this environment. "
        "Wasteless has no server-side Markdown-to-PDF pipeline today — "
        "the UI's 'Print / PDF' button renders the HTML report and calls "
        "the browser's print-to-PDF (window.print()), it does not convert "
        "this Markdown document. This test only proves the Markdown is "
        "well-formed enough for a converter to accept; it does not exercise "
        "production code."
    ),
)
def test_markdown_can_be_converted_to_pdf(golden_report):
    with tempfile.TemporaryDirectory() as tmpdir:
        md_path = os.path.join(tmpdir, "report.md")
        pdf_path = os.path.join(tmpdir, "report.pdf")
        with open(md_path, "w") as f:
            f.write(golden_report)

        result = subprocess.run(
            ["pandoc", md_path, "-o", pdf_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert os.path.exists(pdf_path)
        assert os.path.getsize(pdf_path) > 0
