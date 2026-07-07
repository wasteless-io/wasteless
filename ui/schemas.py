"""Pydantic request models shared across ui/routes/*.py."""

from typing import List
from pydantic import BaseModel


class ActionRequest(BaseModel):
    """Request to execute actions on recommendations."""

    recommendation_ids: List[int]
    action: str  # 'approve', 'reject', 'dismiss', 'execute'
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
