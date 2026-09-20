"""Consumed remote-source fields, validated before creating persisted records."""

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

NonemptyText = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]


class XPost(BaseModel):
    """Preserve string provider IDs and actual text without coercion."""

    id: NonemptyText
    text: NonemptyText
    lang: Annotated[str, Field(strict=True)] | None = None
    created_at: AwareDatetime | None = None


class XMeta(BaseModel):
    """Only the continuation token is consumed."""

    next_token: NonemptyText | None = None


class XPage(BaseModel):
    """Missing data is valid for an empty search; null or malformed data is not."""

    model_config = ConfigDict(strict=True)
    data: list[XPost] = Field(default_factory=list)
    meta: XMeta


class HFRow(BaseModel):
    """Column names are configurable; their consumed values are checked by the adapter."""

    model_config = ConfigDict(strict=True)
    row_idx: Annotated[int, Field(ge=0)]
    row: dict[str, object]


class HFPage(BaseModel):
    """Rows must be a real list of structured dataset rows."""

    model_config = ConfigDict(strict=True)
    rows: list[HFRow]
