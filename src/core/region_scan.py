"""Tell "nothing found" apart from "nothing could be scanned".

The per-region fetchers of the boto3 detectors return None for a region whose
scan failed. One failed region is tolerated: it may not be enabled on the
account, and must not cost the others their results. When EVERY region fails,
AWS is unreachable (missing credentials, broken role trust): the detector must
fail so the step lands in collection_runs.failed_steps, instead of reporting
zero waste. Same rule as src/collectors/aws_cloudwatch.py.
"""

import logging
from typing import Any, List, Optional, Sequence

logger = logging.getLogger(__name__)


def scanned_results(results: Sequence[Optional[Any]], regions: Sequence[str], what: str) -> List:
    """The results of the regions that were scanned, in order.

    Raises RuntimeError when every region failed. `results` and `regions` are
    parallel; `what` names the resource in the messages.
    """
    failed = [region for region, result in zip(regions, results) if result is None]
    if failed and len(failed) == len(regions):
        raise RuntimeError(f"Could not scan {what} in any region: AWS unreachable or access denied")
    if failed:
        logger.warning(
            f"{what}: {len(failed)}/{len(regions)} region(s) not scanned: {', '.join(failed)}"
        )
    return [result for result in results if result is not None]
