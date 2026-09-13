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

# Recommended order: normalise the text, split and prune it, then sweep up anything the earlier
# steps emptied. Handling missing data *last* matters because posts rarely arrive empty — they
# become empty, when a link-and-mention post loses its link and mention.
DEFAULT_ORDER: list[str] = [
    LowercaseStep.name,
    PunctuationStep.name,
    TokenizeStep.name,
    StopwordStep.name,
    LemmatizeStep.name,
    MissingDataStep.name,
]

__all__ = ["DEFAULT_ORDER", "STEP_REGISTRY", "Pipeline", "PreprocessStep"]
