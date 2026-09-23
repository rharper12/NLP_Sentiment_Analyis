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
