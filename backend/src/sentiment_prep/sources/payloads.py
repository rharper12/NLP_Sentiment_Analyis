"""Consumed remote-source fields, validated before creating persisted records."""

from typing import Annotated

from pydantic import AliasChoices, AwareDatetime, BaseModel, ConfigDict, Field

from sentiment_prep.models import PostReference, PostUrl

NonemptyText = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]


class XEntities(BaseModel):
    """Consumed URL metadata only; no author enrichment or linked-page downloads."""

    urls: list[PostUrl] = Field(default_factory=list)


class XNote(BaseModel):
    """Complete long-post text when supplied by the search response."""

    text: NonemptyText
    entities: XEntities = Field(default_factory=XEntities)


class XPost(BaseModel):
    """Preserve string provider IDs and actual text without coercion."""

    id: NonemptyText
    text: NonemptyText
    lang: Annotated[str, Field(strict=True)] | None = None
    created_at: AwareDatetime | None = None
    author_id: NonemptyText | None = None
    conversation_id: NonemptyText | None = None
    in_reply_to_user_id: NonemptyText | None = None
    referenced_tweets: list[PostReference] = Field(
        default_factory=list, validation_alias=AliasChoices("referenced_tweets", "referenced_posts")
    )
    entities: XEntities = Field(default_factory=XEntities)
    note_tweet: XNote | None = Field(
        default=None, validation_alias=AliasChoices("note_tweet", "note_post")
    )


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
