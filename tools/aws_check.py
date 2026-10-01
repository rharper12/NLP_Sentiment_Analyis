"""Report whether this project's AWS configuration actually works.

Answers the question `aws sts get-caller-identity` answers, but through the same code path the
application uses: the settings in ``backend/.env``, resolved into a boto3 session the same way
``api/deps.py`` resolves it. A working CLI and a working application are not the same thing —
the CLI reads ``AWS_PROFILE`` from the shell, while the app reads it from its own settings file.

Usage::

    make aws-check              # identity and configuration only, no billable calls
    make aws-check PROBE=1      # also send one short document to Comprehend (a fraction of a cent)
    make aws-login              # renew SSO for the app's configured AWS_PROFILE
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Resolve the application package from any caller; main adopts the API's working directory
# before loading settings so both commands use the same backend/.env and credential precedence.
BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND / "src"))

from sentiment_prep.api import deps  # noqa: E402
from sentiment_prep.config import Settings, get_settings  # noqa: E402

PROBE_TEXT = "This is a short sentence used only to confirm access."


def login(settings: Settings) -> int:
    """Run the interactive AWS CLI login for the profile the application actually uses."""
    if settings.has_static_keys:
        print(
            "The app uses static access keys. Remove that key pair from backend/.env or the "
            "environment before using AWS_PROFILE for SSO."
        )
        return 1
    if not settings.aws_profile or not settings.aws_profile.strip():
        print("Set AWS_PROFILE to your SSO profile in backend/.env, then run make aws-login.")
        return 1
    executable = shutil.which("aws")
    if executable is None:
        print("Install AWS CLI v2, configure an SSO profile with aws configure sso, then retry.")
        return 1
    print(
        f"Signing in with AWS_PROFILE={settings.aws_profile}. Complete login in your browser.",
        flush=True,
    )
    try:
        result = subprocess.run(
            [executable, "sso", "login", "--profile", settings.aws_profile], check=False
        )
    except OSError:
        print("Could not start AWS CLI. Check its installation and permissions.")
        return 1
    if result.returncode == 0:
        print(
            "SSO login complete. Run make aws-check, then resume the pending operation in the app."
        )
    return result.returncode


def main() -> int:
    """Check the app's AWS identity or renew its SSO session; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="Renew the configured SSO session")
    args = parser.parse_args()
    os.chdir(BACKEND)
    settings = get_settings()
    if args.login:
        return login(settings)
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
        print("For the app's SSO profile run: make aws-login")
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
