"""Validate only the external ML response fields consumed by this application."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Score = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class Scores(BaseModel):
    """All four Comprehend confidence values are required and finite."""

    positive: Score = Field(alias="Positive")
    negative: Score = Field(alias="Negative")
    neutral: Score = Field(alias="Neutral")
    mixed: Score = Field(alias="Mixed")


class SentimentResult(BaseModel):
    """One result tied to an integer request index."""

    index: Annotated[int, Field(strict=True, ge=0)] = Field(alias="Index")
    sentiment: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"] = Field(alias="Sentiment")
    scores: Scores = Field(alias="SentimentScore")


class SentimentError(BaseModel):
    """A provider error is never a sentiment label."""

    index: Annotated[int, Field(strict=True, ge=0)] = Field(alias="Index")
    code: Annotated[str, Field(strict=True, min_length=1)] = Field(alias="ErrorCode")


class SentimentResponse(BaseModel):
    """Validate the entire batch before associating any result with a document."""

    model_config = ConfigDict(strict=True)
    results: list[SentimentResult] = Field(alias="ResultList")
    errors: list[SentimentError] = Field(alias="ErrorList")

    def check_indices(self, size: int) -> None:
        """Reject ambiguous indices, including success/error conflicts, before mutation."""
        indices = [item.index for item in self.results] + [item.index for item in self.errors]
        if any(index >= size for index in indices) or len(indices) != len(set(indices)):
            raise ValueError("Invalid or duplicate Comprehend indices")


EMBEDDING_DIMENSIONS = 1024


class TitanResponse(BaseModel):
    """Float embeddings matching the dimensions sent in the Titan v2 request."""

    model_config = ConfigDict(strict=True)
    embedding: Annotated[
        list[Annotated[float, Field(strict=True, allow_inf_nan=False)]],
        Field(min_length=EMBEDDING_DIMENSIONS, max_length=EMBEDDING_DIMENSIONS),
    ]


class ConverseMessage(BaseModel):
    """Content may include non-text blocks; text, when present, must be a string."""

    model_config = ConfigDict(strict=True)
    content: list[dict[str, object]]


class ConverseOutput(BaseModel):
    """The output message returned by Converse."""

    message: ConverseMessage


class ConverseResponse(BaseModel):
    """The consumed Converse hierarchy."""

    output: ConverseOutput

    def text(self) -> str:
        """Collect supported text blocks regardless of their position."""
        texts = []
        for block in self.output.message.content:
            if "text" in block:
                value = block["text"]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Invalid Converse text block")
                texts.append(value.strip())
        if not texts:
            raise ValueError("Converse returned no text")
        return "\n".join(texts)
