import json
import logging

import structlog

from sentiment_prep.logging_config import bind_context, clear_context, configure_logging


def test_json_lines_carry_bound_context(capsys):
    configure_logging("INFO", json_output=True)
    clear_context()
    bind_context(request_id="abc123", dataset_id="d1")
    structlog.get_logger("t").info("thing_happened", reads=5)
    logging.getLogger("stdlib.lib").warning("plain %s", "message")
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    first, second = lines[-2], lines[-1]
    assert first["message"] == "thing_happened" and first["reads"] == 5
    assert first["request_id"] == "abc123" and first["dataset_id"] == "d1"
    assert second["message"] == "plain message" and second["request_id"] == "abc123"
    clear_context()
