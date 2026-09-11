"""Turn an ``ImpactReport`` into a short plain-English explanation via Bedrock.

Only numbers are sent, never record text: the dataset may contain personal posts. The prompt
lives in ``resources/explain_prompt.txt`` so it can be edited without touching code.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import ImpactReport

logger = get_logger(__name__)


def load_prompt() -> str:
    """Read the prompt template shipped with the package."""
    return resources.files("sentiment_prep.resources").joinpath("explain_prompt.txt").read_text()


class BedrockExplainer:
    """Uses the Converse API so any Bedrock text model works with the same code."""

    def __init__(self, client: Any, model_id: str) -> None:
        self._client = client
        self._model_id = model_id

    def explain(self, report: ImpactReport, applied_steps: list[str]) -> str:
        """Return 150-250 words covering what changed, why it matters, and one limitation."""
        facts = report.model_dump(exclude={"explanation", "warnings"})
        for step in facts["steps"]:
            step.pop("sample_diffs", None)  # contains raw text
        prompt = load_prompt().format(steps=", ".join(applied_steps), facts=json.dumps(facts))
        response = self._client.converse(
            modelId=self._model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 600, "temperature": 0.3},
        )
        text: str = response["output"]["message"]["content"][0]["text"].strip()
        logger.info("explanation_generated", chars=len(text))
        return text
