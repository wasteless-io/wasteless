"""Pydantic request models shared across ui/routes/*.py."""

from typing import List, Literal
from pydantic import BaseModel, Field


class ActionRequest(BaseModel):
    """Request to execute actions on recommendations."""

    # Bounded: an unbounded list would let a single request queue thousands
    # of AWS actions. 500 is far above any legitimate bulk approval.
    recommendation_ids: List[int] = Field(min_length=1, max_length=500)
    # Closed set — the route dispatches on these exact values; anything else
    # must be rejected at validation time (422), not silently fall through.
    action: Literal["approve", "reject", "dismiss", "cancel", "execute"]
    dry_run: bool = True


class ConfigUpdate(BaseModel):
    """Configuration update request."""

    key: str
    value: str | int | float | bool


class AskQuestionRequest(BaseModel):
    """One-shot question about a specific recommendation."""

    question: str


class PolicyImport(BaseModel):
    """Policy-as-code import request (YAML text)."""

    yaml_text: str
