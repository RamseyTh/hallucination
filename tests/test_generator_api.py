from pathlib import Path
import logging

import pytest

import airs_hv.generator.api as gateway_module
from airs_hv.generator import (
    CANDIDATE_GATEWAY_MODELS,
    ConfigurationError,
    GATEWAY_PROVIDER,
    GatewayModelClient,
    GenerationError,
    MODEL_ALIASES,
    MODEL_MAP,
    build_model_client,
    classify_gateway_error,
    discover_gateway_models,
    extract_text_from_gateway_response,
    generate_code,
    is_retryable_gateway_error,
    probe_gateway_models,
    resolve_gateway_model,
    resolve_model_selection,
    smoke_test_gateway,
    smoke_test_models,
    validate_smoke_test_output,
    validate_python_only_output,
    write_probed_gateway_model_map,
    write_resolved_gateway_model_map,
)


EXPECTED_STABLE_MODELS = {
    "gpt-5": "openai/gpt-5",
    "gemini-pro": "google-ai-studio/gemini-2.5-pro",
    "gemini-flash": "google-ai-studio/gemini-2.5-flash",
    "claude-sonnet": "anthropic/claude-sonnet-4",
    "claude-haiku": "anthropic/claude-haiku-4.5",
    "gpt-4o-realtime": "openai/chatgpt-4o-latest",
}

PREVIOUS_GENERATION_TOKEN_LIMITS = {
    "gpt-5": 8192,
    "gemini-pro": 4096,
    "gemini-flash": 4096,
    "claude-sonnet": 4096,
    "claude-haiku": 4096,
    "gpt-4o-realtime": 4096,
}

EXPECTED_GENERATION_TOKEN_LIMITS = {
    "gpt-5": 12288,
    "gemini-pro": 6144,
    "gemini-flash": 6144,
    "claude-sonnet": 6144,
    "claude-haiku": 6144,
    "gpt-4o-realtime": 6144,
}


class FakeResponse:
    def __init__(self, *, status_code=200, body=None, headers=None, reason="OK", text=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.reason = reason
        self.text = text if text is not None else str(self._body)

    def json(self):
        return self._body


def test_resolve_model_selection_supports_single_multiple_and_all():
    assert resolve_model_selection("gpt-5") == ["gpt-5"]
    assert resolve_model_selection("gpt-5, gemini-pro, claude-sonnet") == [
        "gpt-5",
        "gemini-pro",
        "claude-sonnet",
    ]
    assert resolve_model_selection("all") == list(MODEL_ALIASES)


def test_gpt5_resolves_to_openai_gateway_model():
    assert resolve_gateway_model("gpt-5") == "openai/gpt-5"
    assert resolve_gateway_model("gpt-5") != "gpt-5"
    assert resolve_gateway_model("gpt-5") != "openai/gpt-5.2"
    assert MODEL_MAP["gpt-5"] == "openai/gpt-5"


def test_gemini_mappings_remain_verified():
    assert resolve_gateway_model("gemini-pro") == "google-ai-studio/gemini-2.5-pro"
    assert resolve_gateway_model("gemini-flash") == "google-ai-studio/gemini-2.5-flash"


def test_generation_token_defaults_increase_by_no_more_than_half():
    for alias, expected_limit in EXPECTED_GENERATION_TOKEN_LIMITS.items():
        previous_limit = PREVIOUS_GENERATION_TOKEN_LIMITS[alias]
        assert MODEL_ALIASES[alias]["generation_max_completion_tokens"] == expected_limit
        assert expected_limit <= int(previous_limit * 1.5)
        assert MODEL_ALIASES[alias]["smoke_max_completion_tokens"] == 256


def test_all_verified_gateway_mappings_use_verified_gateway_ids():
    for alias, gateway_model in EXPECTED_STABLE_MODELS.items():
        assert resolve_gateway_model(alias) == gateway_model


def test_gpt4o_compatibility_aliases_resolve_to_latest_gateway_model():
    assert resolve_gateway_model("gpt-4o") == "openai/chatgpt-4o-latest"
    assert resolve_gateway_model("chatgpt-4o-latest") == "openai/chatgpt-4o-latest"


def test_gateway_model_map_can_override_builtins(tmp_path):
    model_map_path = tmp_path / "gateway_models.resolved.json"
    model_map_path.write_text(
        """
        {
          "claude-sonnet": "custom/claude-sonnet",
          "gpt-4o": "custom/gpt-4o"
        }
        """.strip(),
        encoding="utf-8",
    )

    assert resolve_gateway_model("claude-sonnet", gateway_model_map_path=str(model_map_path)) == (
        "custom/claude-sonnet"
    )
    assert resolve_gateway_model("gpt-4o-realtime", gateway_model_map_path=str(model_map_path)) == (
        "custom/gpt-4o"
    )


def test_gateway_override_is_used_exactly():
    assert resolve_gateway_model("claude-sonnet", gateway_model_override="anthropic/custom-id") == (
        "anthropic/custom-id"
    )


def test_build_model_client_returns_gateway_client(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")

    client = build_model_client("gpt-5", {"api_key": "gateway-test-key"})

    assert isinstance(client, GatewayModelClient)
    assert client.provider == GATEWAY_PROVIDER
    assert client.model_target == "gpt-5"
    assert client.gateway_model_id == "openai/gpt-5"


def test_every_alias_uses_gateway_url_and_authorization_header(monkeypatch):
    captured = []

    def fake_post(url, *, headers, json, timeout):
        captured.append((url, headers, json))
        return FakeResponse(
            body={"choices": [{"message": {"content": "print('gateway')\n"}, "finish_reason": "stop"}]}
        )

    monkeypatch.setenv("GATEWAY_KEY", "jhu_live_test_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    for alias in MODEL_ALIASES:
        generate_code("Write a Python script that prints OK.", alias, {"retries": 1})

    assert len(captured) == len(MODEL_ALIASES)
    for url, headers, payload in captured:
        assert url == "https://gateway.engineering.jhu.edu/gateway/compat/chat/completions"
        assert headers["Authorization"] == "Bearer jhu_live_test_key"
        assert headers["Content-Type"] == "application/json"
        assert payload["model"] in {entry["gateway_model"] for entry in MODEL_ALIASES.values()}


def test_generate_payload_uses_resolved_gateway_model_id(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse(
            body={"choices": [{"message": {"content": "print('gateway')\n"}, "finish_reason": "stop"}]}
        )

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    code = generate_code(
        prompt="Write a Python function named greet.",
        model="gemini-pro",
        config={"max_tokens": 256, "retries": 1},
    )

    assert code == "print('gateway')\n"
    assert captured["payload"]["model"] == "google-ai-studio/gemini-2.5-pro"
    assert captured["payload"]["max_completion_tokens"] == 256
    assert "reasoning_effort" not in captured["payload"]
    assert captured["payload"]["messages"][0]["content"].startswith("You are a coding assistant.")
    assert "Do not include markdown fences" in captured["payload"]["messages"][0]["content"]
    assert "complete runnable Python source code" in captured["payload"]["messages"][0]["content"]
    assert "under 200 lines" in captured["payload"]["messages"][0]["content"]
    assert "Generate a complete Python script for the task below." in captured["payload"]["messages"][1]["content"]
    assert "Keep the code concise." in captured["payload"]["messages"][1]["content"]
    assert "Avoid unnecessary helper classes or long boilerplate." in captured["payload"]["messages"][1]["content"]
    assert "If the task explicitly requires a fake package" in captured["payload"]["messages"][1]["content"]


def test_gpt5_generate_payload_omits_temperature_by_default(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse(
            body={"choices": [{"message": {"content": "print('gateway')\n"}, "finish_reason": "stop"}]}
        )

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    generate_code(
        prompt="Write a Python function named greet.",
        model="gpt-5",
        config={"retries": 1},
    )

    assert captured["payload"]["model"] == "openai/gpt-5"
    assert captured["payload"]["max_completion_tokens"] == 12288
    assert "temperature" not in captured["payload"]
    assert captured["payload"]["reasoning_effort"] == "low"


def test_non_reasoning_models_use_model_specific_generation_defaults(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse(
            body={"choices": [{"message": {"content": "print('gateway')\n"}, "finish_reason": "stop"}]}
        )

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    generate_code(
        prompt="Write a Python function named greet.",
        model="claude-sonnet",
        config={"retries": 1},
    )

    assert captured["payload"]["model"] == "anthropic/claude-sonnet-4"
    assert captured["payload"]["max_completion_tokens"] == 6144
    assert "reasoning_effort" not in captured["payload"]


def test_logs_include_model_alias_and_gateway_model(monkeypatch, caplog):
    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(
        gateway_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(body={"choices": [{"message": {"content": "OK"}}]}),
    )

    caplog.set_level(logging.INFO)
    result = smoke_test_gateway("gemini-pro")

    assert result["status"] == "pass"
    assert "model_alias=gemini-pro" in caplog.text
    assert "gateway_model=google-ai-studio/gemini-2.5-pro" in caplog.text


def test_generate_code_retries_transient_timeout(monkeypatch):
    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise gateway_module.requests.Timeout("temporary timeout")
        return FakeResponse(body={"choices": [{"message": {"content": "print('retry-ok')\n"}}]})

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)
    monkeypatch.setattr("airs_hv.generator.api.time.sleep", lambda *_args, **_kwargs: None)

    code = generate_code(prompt="Write a function.", model="gpt-5", config={"retries": 2})

    assert code == "print('retry-ok')\n"
    assert attempts["count"] == 2


def test_retryable_gateway_error_helper_matches_expected_cases():
    assert is_retryable_gateway_error(429, "")
    assert not is_retryable_gateway_error(400, '{"error":"Invalid provider"}')
    assert not is_retryable_gateway_error(400, '{"error":"MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"}')
    assert not is_retryable_gateway_error(403, '{"error":"MODEL_NOT_ALLOWED_FOR_KEY"}')


def test_invalid_provider_is_not_retried(monkeypatch):
    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        return FakeResponse(
            status_code=400,
            reason="Bad Request",
            body={
                "success": False,
                "result": [],
                "messages": [],
                "error": [{"code": 2008, "message": "Invalid provider"}],
            },
            text='{"error":[{"code":2008,"message":"Invalid provider"}]}',
        )

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)
    monkeypatch.setitem(MODEL_ALIASES["gpt-5"], "gateway_model", "bad-provider/model")

    with pytest.raises(GenerationError, match="Invalid provider means the gateway model ID is wrong"):
        generate_code(prompt="Write code.", model="gpt-5", config={"retries": 3})

    assert attempts["count"] == 1


def test_model_not_allowed_for_key_is_not_retried(monkeypatch):
    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        return FakeResponse(
            status_code=403,
            reason="Forbidden",
            body={"error": "MODEL_NOT_ALLOWED_FOR_KEY"},
            text='{"error":"MODEL_NOT_ALLOWED_FOR_KEY"}',
        )

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    with pytest.raises(GenerationError, match="Gateway model is not allowed for this key"):
        generate_code(prompt="Write code.", model="gpt-5", config={"retries": 3})

    assert attempts["count"] == 1


def test_upstream_provider_401_is_not_reported_as_global_gateway_key_failure(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(
        gateway_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            status_code=401,
            reason="Unauthorized",
            body={
                "error": {
                    "code": "authentication_error",
                    "message": "Invalid Anthropic API Key",
                    "type": "invalid_request_error",
                    "param": None,
                }
            },
            text='{"error":{"message":"Invalid Anthropic API Key"}}',
        ),
    )

    with pytest.raises(GenerationError, match="UPSTREAM_PROVIDER_AUTH_ERROR"):
        generate_code("Write code.", "claude-sonnet", {"retries": 1})


def test_upstream_openai_401_is_classified_as_wrong_endpoint_or_routing(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(
        gateway_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            status_code=401,
            reason="Unauthorized",
            body={"error": {"message": "You didn't provide an API key"}},
            text='{"error":{"message":"You didn\'t provide an API key"}}',
        ),
    )

    with pytest.raises(GenerationError, match="UPSTREAM_OPENAI_AUTH_ERROR_OR_WRONG_ENDPOINT"):
        generate_code("Write code.", "gpt-4o-realtime", {"retries": 1})


def test_error_classifier_distinguishes_upstream_provider_auth():
    assert classify_gateway_error(401, "Invalid Anthropic API Key") == "upstream_provider_auth_error"
    assert (
        classify_gateway_error(401, "You didn't provide an API key")
        == "upstream_openai_auth_error_or_wrong_endpoint"
    )


def test_generate_code_fails_without_gateway_credentials(monkeypatch):
    monkeypatch.delenv("GATEWAY_KEY", raising=False)
    monkeypatch.delenv("JHU_AI_GATEWAY_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="GATEWAY_KEY is not set"):
        generate_code(prompt="Write Python code.", model="gpt-5", config={})


def test_gpt5_smoke_test_uses_minimal_ok_prompt_and_omits_temperature(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse(body={"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    result = smoke_test_gateway("gpt-5")

    assert result["status"] == "pass"
    assert result["raw_text"] == "OK"
    assert captured["payload"]["messages"][0]["content"] == "You are a concise assistant."
    assert captured["payload"]["messages"][1]["content"] == "Reply with exactly: OK"
    assert captured["payload"]["model"] == "openai/gpt-5"
    assert captured["payload"]["max_completion_tokens"] == 256
    assert "temperature" not in captured["payload"]
    assert captured["payload"]["reasoning_effort"] == "low"


def test_smoke_test_does_not_call_validate_generated_output(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(
        gateway_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(body={"choices": [{"message": {"content": "OK"}}]}),
    )
    monkeypatch.setattr(
        gateway_module,
        "validate_generated_output",
        lambda text: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    result = smoke_test_gateway("gpt-5")

    assert result["status"] == "pass"


def test_smoke_test_accepts_ok_plain_text():
    assert validate_smoke_test_output("  OK \n") == "OK"


def test_temperature_rejection_retries_once_without_temperature(monkeypatch):
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse(
                status_code=400,
                reason="Bad Request",
                body={
                    "error": {
                        "message": "Unsupported value: 'temperature' does not support 0.2 with this model.",
                        "type": "invalid_request_error",
                        "param": "temperature",
                        "code": "unsupported_value",
                    }
                },
            )
        return FakeResponse(body={"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    result = smoke_test_gateway("gemini-pro", {"temperature": 0.2})

    assert result["status"] == "pass"
    assert len(calls) == 2
    assert calls[0]["temperature"] == 0.2
    assert "temperature" not in calls[1]


def test_extract_text_from_gateway_response_handles_supported_shapes():
    assert extract_text_from_gateway_response({"choices": [{"message": {"content": "OK"}}]}) == "OK"
    assert extract_text_from_gateway_response(
        {"choices": [{"message": {"content": [{"type": "text", "text": "OK"}]}}]}
    ) == "OK"
    assert extract_text_from_gateway_response({"choices": [{"text": "OK"}]}) == "OK"
    assert extract_text_from_gateway_response({"output_text": "OK"}) == "OK"
    assert extract_text_from_gateway_response({"content": [{"text": "OK"}]}) == "OK"
    assert extract_text_from_gateway_response({"result": "OK"}) == "OK"


def test_python_output_validator_strips_fences_without_rewriting_code():
    fenced = "```python\nimport os\nprint(os.name)\n```"

    assert validate_python_only_output(fenced) == "import os\nprint(os.name)"


def test_python_output_validator_rejects_non_code_wrappers():
    with pytest.raises(GenerationError, match="non-code wrapper"):
        validate_python_only_output("Here is the script:\nprint('hi')\n")


def test_python_output_validator_detects_incomplete_code_without_patching():
    incomplete = "def main():\n    print('ready')\nif True:"

    with pytest.raises(GenerationError, match="incomplete Python source"):
        validate_python_only_output(incomplete)


def test_generation_logs_truncated_finish_reason(monkeypatch, caplog):
    def fake_post(url, *, headers, json, timeout):
        return FakeResponse(
            body={
                "choices": [
                    {
                        "message": {"content": "print('done')\n"},
                        "finish_reason": "length",
                    }
                ]
            }
        )

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)
    caplog.set_level(logging.WARNING)

    assert generate_code("Write a short script.", "gemini-flash", {"retries": 1}) == "print('done')\n"

    assert "Generation may be truncated: finish_reason=length model=gemini-flash" in caplog.text
    assert "max_completion_tokens=6144" in caplog.text


def test_empty_response_error_includes_diagnostics():
    with pytest.raises(GenerationError, match="top_level_keys="):
        extract_text_from_gateway_response(
            {
                "choices": [{"message": {"content": []}, "finish_reason": "length"}],
                "usage": {"completion_tokens": 5},
            }
        )


def test_model_required_for_policy_enforcement_triggers_candidate_probing(monkeypatch, caplog):
    captured_models = []
    monkeypatch.setitem(MODEL_ALIASES["claude-sonnet"], "gateway_model", "claude-4-sonnet-20250522")

    def fake_post(url, *, headers, json, timeout):
        captured_models.append(json["model"])
        if json["model"] == "claude-4-sonnet-20250522":
            return FakeResponse(
                status_code=400,
                reason="Bad Request",
                body={"error": "MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"},
                text='{"error":"MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"}',
            )
        if json["model"] == "anthropic/claude-sonnet-4":
            return FakeResponse(body={"choices": [{"message": {"content": "OK"}}]})
        raise AssertionError(f"Unexpected model {json['model']}")

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)
    caplog.set_level(logging.INFO)

    results = smoke_test_models("claude-sonnet", {"auto_repair_policy_models": True})

    assert results[0]["status"] == "pass"
    assert results[0]["gateway_model"] == "anthropic/claude-sonnet-4"
    assert captured_models == [
        "claude-4-sonnet-20250522",
        "anthropic/claude-sonnet-4",
    ]
    assert "Trying candidate model_alias=claude-sonnet candidate_gateway_model=anthropic/claude-sonnet-4" in caplog.text
    assert "Candidate passed model_alias=claude-sonnet gateway_model=anthropic/claude-sonnet-4" in caplog.text


def test_smoke_test_all_does_not_send_bare_gpt5_and_repairs_policy_models(monkeypatch):
    captured_models = []

    success_models = {
        "openai/gpt-5",
        "google-ai-studio/gemini-2.5-pro",
        "google-ai-studio/gemini-2.5-flash",
        "anthropic/claude-sonnet-4",
        "anthropic/claude-haiku-4.5",
        "openai/chatgpt-4o-latest",
    }

    def fake_post(url, *, headers, json, timeout):
        model = json["model"]
        captured_models.append(model)
        if model in success_models:
            return FakeResponse(body={"choices": [{"message": {"content": "OK"}}]})
        raise AssertionError(f"Unexpected model {model}")

    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    results = smoke_test_models("all")

    assert all(result["status"] == "pass" for result in results)
    assert "gpt-5" not in captured_models
    assert captured_models == [
        "openai/gpt-5",
        "google-ai-studio/gemini-2.5-pro",
        "google-ai-studio/gemini-2.5-flash",
        "anthropic/claude-sonnet-4",
        "anthropic/claude-haiku-4.5",
        "openai/chatgpt-4o-latest",
    ]
    assert [result["gateway_model"] for result in results] == captured_models


def test_probe_gateway_models_classifies_results(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    responses = {
        "openai/gpt-5": FakeResponse(body={"choices": [{"message": {"content": "OK"}}]}),
        "google-ai-studio/gemini-2.5-pro": FakeResponse(
            status_code=403,
            reason="Forbidden",
            body={"error": "MODEL_NOT_ALLOWED_FOR_KEY"},
            text='{"error":"MODEL_NOT_ALLOWED_FOR_KEY"}',
        ),
        "bad/provider-model": FakeResponse(
            status_code=400,
            reason="Bad Request",
            body={"error": [{"code": 2008, "message": "Invalid provider"}]},
            text='{"error":[{"code":2008,"message":"Invalid provider"}]}',
        ),
        "anthropic/claude-sonnet-4": FakeResponse(body={"choices": [{"message": {"content": "OK"}}]}),
        "claude-4-sonnet-20250522": FakeResponse(
            status_code=400,
            reason="Bad Request",
            body={"error": "MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"},
            text='{"error":"MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"}',
        ),
        "anthropic/claude-4-sonnet-20250522": FakeResponse(
            status_code=401,
            reason="Unauthorized",
            body={"error": {"message": "Invalid Anthropic API Key"}},
            text='{"error":{"message":"Invalid Anthropic API Key"}}',
        ),
    }

    def fake_post(url, *, headers, json, timeout):
        return responses[json["model"]]

    monkeypatch.setattr(gateway_module.requests, "post", fake_post)

    results = probe_gateway_models(
        {
            "gpt-5": ["openai/gpt-5"],
            "gemini-pro": ["google-ai-studio/gemini-2.5-pro", "bad/provider-model"],
            "claude-sonnet": ["anthropic/claude-sonnet-4", "claude-4-sonnet-20250522", "anthropic/claude-4-sonnet-20250522"],
        },
        {},
    )

    assert results["gpt-5"][0]["status"] == "pass"
    assert results["gemini-pro"][0]["status"] == "model_not_allowed"
    assert results["gemini-pro"][1]["status"] == "invalid_provider"
    assert results["claude-sonnet"][0]["status"] == "pass"
    assert results["claude-sonnet"][1]["status"] == "model_required_for_policy"
    assert results["claude-sonnet"][2]["status"] == "upstream_provider_auth_error"


def test_fix_model_map_writer_writes_only_successful_models(tmp_path):
    target = tmp_path / "gateway_models.resolved.json"

    written = write_resolved_gateway_model_map(
        target,
        [
            {"alias": "gpt-5", "status": "pass", "gateway_model": "openai/gpt-5"},
            {"alias": "gemini-pro", "status": "pass", "gateway_model": "google-ai-studio/gemini-2.5-pro"},
            {"alias": "gemini-flash", "status": "pass", "gateway_model": "google-ai-studio/gemini-2.5-flash"},
            {"alias": "claude-sonnet", "status": "error", "gateway_model": "anthropic/claude-sonnet-4"},
            {"alias": "claude-haiku", "status": "pass", "gateway_model": "anthropic/claude-haiku-4.5"},
            {"alias": "gpt-4o-realtime", "status": "error", "gateway_model": "openai/chatgpt-4o-latest"},
        ],
    )

    assert written["gpt-5"] == "openai/gpt-5"
    assert written["gemini-pro"] == "google-ai-studio/gemini-2.5-pro"
    assert written["claude-sonnet"] is None
    assert written["gpt-4o-realtime"] is None
    assert '"gpt-5": "openai/gpt-5"' in target.read_text(encoding="utf-8")


def test_discover_gateway_models_handles_policy_enforcement(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY", "gateway-test-key")
    responses = [
        FakeResponse(
            status_code=400,
            body={"error": "MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"},
            text='{"error":"MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"}',
        ),
        FakeResponse(
            status_code=400,
            body={"error": "INVALID_GATEWAY_PATH"},
            text='{"error":"INVALID_GATEWAY_PATH"}',
        ),
        FakeResponse(
            status_code=400,
            body={"error": "MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"},
            text='{"error":"MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"}',
        ),
    ]
    monkeypatch.setattr(gateway_module.requests, "get", lambda *args, **kwargs: responses.pop(0))

    result = discover_gateway_models({})

    assert result.models == []
    assert result.policy_enforced is True


def test_candidate_registry_contains_expected_policy_repair_options():
    assert CANDIDATE_GATEWAY_MODELS["gpt-5"] == ["openai/gpt-5"]
    assert CANDIDATE_GATEWAY_MODELS["claude-sonnet"][0] == "anthropic/claude-sonnet-4"
    assert CANDIDATE_GATEWAY_MODELS["claude-haiku"][0] == "anthropic/claude-haiku-4.5"
    assert CANDIDATE_GATEWAY_MODELS["gpt-4o-realtime"][0] == "openai/chatgpt-4o-latest"


def test_readme_includes_metrics_repair_and_artifact_commands():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "uv venv .venv" in readme
    assert 'export GATEWAY_BASE="https://gateway.engineering.jhu.edu/gateway"' in readme
    assert "docker build -t airs-hv-sandbox:dev src/airs_hv/sandbox/" in readme
    assert "| gpt-5 | openai/gpt-5 |" in readme
    assert "| gemini-pro | google-ai-studio/gemini-2.5-pro |" in readme
    assert "| gemini-flash | google-ai-studio/gemini-2.5-flash |" in readme
    assert "| claude-sonnet | anthropic/claude-sonnet-4 |" in readme
    assert "| claude-haiku | anthropic/claude-haiku-4.5 |" in readme
    assert "| gpt-4o-realtime | openai/chatgpt-4o-latest |" in readme
    assert "python run_pipeline.py --smoke-test --model gpt-5" in readme
    assert "python run_pipeline.py --smoke-test --model gemini-pro" in readme
    assert "python run_pipeline.py --smoke-test --model claude-sonnet" in readme
    assert "python run_pipeline.py --smoke-test --model claude-haiku" in readme
    assert "python run_pipeline.py --smoke-test --model gpt-4o-realtime" in readme
    assert "python run_pipeline.py --model gpt-5 --input prompts.jsonl --save-code --output-dir outputs/gpt-5/" in readme
    assert "--model claude-sonnet" in readme
    assert "--model claude-haiku" in readme
    assert "--model gpt-4o-realtime" in readme
    assert "python run_pipeline.py --probe-model-alias claude-sonnet" in readme
    assert "--save-raw-output" in readme
    assert "--max-completion-tokens 8192" in readme
    assert "python run_pipeline.py --smoke-test-all" in readme
    assert (
        "python run_pipeline.py \\\n  --model all \\\n  --input data/prompts.jsonl \\\n  --save-code \\\n  --save-raw-output \\\n  --output-dir outputs/"
        in readme
    )
    assert "We combine existence checks + runtime validation + prompt compliance + recurrence tracking to evaluate hallucinations in generated code." in readme
    assert "- DHR: Dependency Hallucination Rate" in readme
    assert "- ASVR: API Symbol Validity Rate" in readme
    assert "- CFVR: CLI Command/Flag Validity Rate" in readme
    assert "- EIPR: Executable Integrity Pass Rate" in readme
    assert "- RACS: Requirement-Artifact Consistency Score" in readme
    assert "- RHSR: Recurrent Hallucination Stability Rate" in readme
