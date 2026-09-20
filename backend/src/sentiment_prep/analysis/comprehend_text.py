"""One Unicode boundary for Comprehend requests, billing and prefix disclosure."""

from dataclasses import dataclass

MAX_BYTES = 5000
TRUNCATION_WARNING = (
    "Comprehend sentiment uses only the first 5,000 UTF-8 bytes of oversized documents; "
    "complete text is retained."
)


@dataclass(frozen=True)
class PreparedText:
    """Derived request metadata; the original document remains the persisted source."""

    text: str
    original_chars: int
    submitted_chars: int
    submitted_bytes: int
    truncated: bool


def prepare_text(text: str) -> PreparedText:
    """Keep complete UTF-8 code points within the request limit; bill submitted characters."""
    encoded = (text or " ").encode("utf-8")
    submitted = encoded[:MAX_BYTES].decode("utf-8", errors="ignore")
    return PreparedText(
        submitted,
        len(text),
        len(submitted),
        len(submitted.encode("utf-8")),
        len(encoded) > MAX_BYTES,
    )
