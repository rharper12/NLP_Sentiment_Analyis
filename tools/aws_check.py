"""Report whether this project's AWS configuration actually works.

Answers the question `aws sts get-caller-identity` answers, but through the same code path the
application uses: the settings in ``backend/.env``, resolved into a boto3 session the same way
``api/deps.py`` resolves it. A working CLI and a working application are not the same thing —
the CLI reads ``AWS_PROFILE`` from the shell, while the app reads it from its own settings file.

Usage::

    make aws-check              # identity and configuration only, no billable calls
    make aws-check PROBE=1      # also send one short document to Comprehend (a fraction of a cent)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The app reads backend/.env relative to its working directory, so this tool adopts that directory
# before importing settings. Without it the check would pass against different configuration than
# the one the API actually runs with, which is the whole thing it exists to rule out.
BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND / "src"))
os.chdir(BACKEND)

from sentiment_prep.api import deps  # noqa: E402
from sentiment_prep.config import get_settings  # noqa: E402

PROBE_TEXT = "This is a short sentence used only to confirm access."


def main() -> int:
    """Print the resolved identity and settings. Returns a process exit code."""
    settings = get_settings()
    source = (
        "static keys"
        if settings.has_static_keys
        else settings.aws_profile or "default credential chain"
    )
    print(f"credentials : {source}")
    print(f"region      : {settings.aws_region}")
    print(f"comprehend  : {'enabled' if settings.comprehend_enabled else 'disabled'}")

    try:
        session = deps.boto_session()
        identity = session.client("sts", region_name=settings.aws_region).get_caller_identity()
    except Exception as error:  # noqa: BLE001 - this tool exists to report the failure plainly
        print(f"\nSTS failed: {type(error).__name__}: {error}")
        print("For an SSO profile run: aws sso login --profile <name>")
        return 1

    print(f"\naccount     : {identity['Account']}")
    print(f"arn         : {identity['Arn']}")

    if os.environ.get("PROBE") and settings.comprehend_enabled:
        client = deps.get_comprehend_client()
        if client is None:
            print("\ncomprehend  : disabled in settings, nothing probed")
            return 0
        try:
            result = client.batch_detect_sentiment(TextList=[PROBE_TEXT], LanguageCode="en")
        except Exception as error:  # noqa: BLE001 - same reason
            print(f"\nComprehend failed: {type(error).__name__}: {error}")
            return 1
        print(f"comprehend  : reachable, replied {result['ResultList'][0]['Sentiment'].lower()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
