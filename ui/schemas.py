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
    action: Literal["approve", "reject", "dismiss", "cancel", "execute", "restore"]
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


class AwsSetupRequest(BaseModel):
    """AWS connection submitted from the /setup onboarding page.

    Everything optional except the region: the route validates the two
    accepted combinations (role ARNs, or direct access keys) and their
    formats — Pydantic only bounds the sizes so a stray paste can't stuff
    megabytes into the process environment.
    """

    region: str = Field(default="eu-west-1", max_length=32)
    role_arn: str = Field(default="", max_length=2048)
    write_role_arn: str = Field(default="", max_length=2048)
    external_id: str = Field(default="", max_length=1224)
    access_key_id: str = Field(default="", max_length=128)
    secret_access_key: str = Field(default="", max_length=128)
