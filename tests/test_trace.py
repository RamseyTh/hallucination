import json

from airs_hv.trace import TraceLogger, _json_default


class ResponseUsageLike:
    def __init__(self):
        self.prompt_tokens = 11
        self.completion_tokens = 7
        self.total_tokens = 18


def test_json_default_serializes_response_usage_like_object():
    usage = ResponseUsageLike()

    assert _json_default(usage) == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


def test_trace_logger_writes_single_line_jsonl(tmp_path):
    logger = TraceLogger(tmp_path / "trace.jsonl", "run-123")
    logger.log(
        "generation_completed",
        prompt_id="p1",
        model="gpt-5",
        usage=ResponseUsageLike(),
        raw_output="print('hello')\nprint('world')\n",
    )

    lines = (tmp_path / "trace.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["usage"]["total_tokens"] == 18
    assert record["raw_output"] == "print('hello')\nprint('world')\n"
