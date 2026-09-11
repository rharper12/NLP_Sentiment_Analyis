"""Toggleable preprocessing steps and the pipeline that runs them.

``STEP_REGISTRY`` maps the names the UI and API use to step classes. Adding a step means
adding a module here and a rationale entry in ``resources/rationale.yaml``.
"""

from sentiment_prep.preprocessing.base import PreprocessStep
from sentiment_prep.preprocessing.lemmatize import LemmatizeStep
from sentiment_prep.preprocessing.lowercase import LowercaseStep
from sentiment_prep.preprocessing.missing_data import MissingDataStep
from sentiment_prep.preprocessing.pipeline import Pipeline
from sentiment_prep.preprocessing.punctuation import PunctuationStep
from sentiment_prep.preprocessing.stopwords import StopwordStep
from sentiment_prep.preprocessing.tokenize import TokenizeStep

STEP_REGISTRY: dict[str, type[PreprocessStep]] = {
    MissingDataStep.name: MissingDataStep,
    LowercaseStep.name: LowercaseStep,
    PunctuationStep.name: PunctuationStep,
    TokenizeStep.name: TokenizeStep,
    StopwordStep.name: StopwordStep,
    LemmatizeStep.name: LemmatizeStep,
}

# Sensible order when the user has not reordered: fix data, normalise, split, prune, reduce.
DEFAULT_ORDER: list[str] = list(STEP_REGISTRY)

__all__ = ["DEFAULT_ORDER", "STEP_REGISTRY", "Pipeline", "PreprocessStep"]
