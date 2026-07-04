#!/usr/bin/env python3
"""
LLM integration for Wasteless — AI insights on recommendations.

Uses litellm so users pick any provider (Anthropic, OpenAI, Ollama, ...)
with a single model string. Everything degrades silently: wasteless must
work identically without litellm installed, without a model configured,
or when the provider errors — an insight is a bonus, never a dependency.

Configuration (.env):
    WASTELESS_LLM_MODEL=anthropic/claude-haiku-4-5-20251001   # any litellm model id
    ANTHROPIC_API_KEY=sk-ant-...       # or the key your provider expects

The insight explains the recommendation and its risk from the stored
detection metadata; it never decides anything — approval stays human and
behind the safeguards.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODEL_ENV_VAR = 'WASTELESS_LLM_MODEL'
MAX_TOKENS = 220
TIMEOUT_SECONDS = 20
# Cap per detector run: keeps cost/latency bounded on large accounts
MAX_INSIGHTS_PER_RUN = 25

PROMPT_TEMPLATE = """\
You are the FinOps assistant inside wasteless, an AWS cost-waste detector.
Explain the following recommendation to the human who must approve it.

Recommendation: {action}
Resource type: {resource_type}
Estimated savings: {savings} EUR/month
Detection confidence: {confidence}
Detection metadata (JSON): {metadata}

Write 2-3 short sentences, plain language, no markdown:
1. why this resource is considered waste, citing concrete numbers from the metadata;
2. the risk of applying the action and whether it is reversible;
3. one thing worth checking before approving, if any.
Never invent numbers that are not in the data above."""


def is_enabled() -> bool:
    """True when a model is configured AND litellm is importable."""
    if not os.getenv(MODEL_ENV_VAR):
        return False
    try:
        import litellm  # noqa: F401
        return True
    except ImportError:
        logger.debug("litellm not installed — AI insights disabled")
        return False


def build_prompt(action: str, resource_type: str, savings: Any,
                 confidence: Any, metadata: Dict[str, Any]) -> str:
    return PROMPT_TEMPLATE.format(
        action=action,
        resource_type=resource_type,
        savings=savings,
        confidence=confidence,
        metadata=json.dumps(metadata, default=str),
    )


def generate_insight(action: str, resource_type: str, savings: Any,
                     confidence: Any, metadata: Dict[str, Any]) -> Optional[str]:
    """One AI insight for a recommendation, or None (never raises)."""
    if not is_enabled():
        return None

    try:
        import litellm
        response = litellm.completion(
            model=os.getenv(MODEL_ENV_VAR),
            messages=[{'role': 'user', 'content': build_prompt(
                action, resource_type, savings, confidence, metadata)}],
            max_tokens=MAX_TOKENS,
            temperature=0.2,
            timeout=TIMEOUT_SECONDS,
        )
        insight = response.choices[0].message.content
        return insight.strip() if insight else None
    except Exception as e:
        logger.warning(f"AI insight generation failed (continuing without): {e}")
        return None


def enrich_recommendations(conn, limit: int = MAX_INSIGHTS_PER_RUN) -> int:
    """
    Fill ai_insight for pending recommendations that lack one.

    Called at the end of each detector run; a no-op (0) when insights are
    disabled. Each insight is committed individually so a crash mid-batch
    loses nothing.
    """
    if not is_enabled():
        return 0

    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT r.id, r.action_required, r.estimated_monthly_savings_eur,
                   w.resource_type, w.confidence_score, w.metadata
            FROM recommendations r
            JOIN waste_detected w ON w.id = r.waste_id
            WHERE r.status = 'pending' AND r.ai_insight IS NULL
            ORDER BY r.estimated_monthly_savings_eur DESC
            LIMIT %s;
        """, (limit,))
        rows = cursor.fetchall()

        generated = 0
        for rec_id, action, savings, resource_type, confidence, metadata in rows:
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            insight = generate_insight(action, resource_type, savings,
                                       confidence, metadata or {})
            if insight:
                cursor.execute(
                    "UPDATE recommendations SET ai_insight = %s WHERE id = %s;",
                    (insight, rec_id))
                conn.commit()
                generated += 1

        if generated:
            logger.info(f"Generated {generated} AI insight(s)")
        return generated

    except Exception as e:
        conn.rollback()
        logger.warning(f"AI insight enrichment failed (continuing without): {e}")
        return 0
    finally:
        cursor.close()
