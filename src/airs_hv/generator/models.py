from __future__ import annotations

MODEL_ALIASES = {
    "gpt-5": {
        "display_name": "GPT-5",
        "gateway_model": "openai/gpt-5",
        "verified": True,
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "default_reasoning_effort": "low",
        "generation_max_completion_tokens": 8192,
        "smoke_max_completion_tokens": 256,
    },
    "gemini-pro": {
        "display_name": "Gemini 2.5 Pro",
        "gateway_model": "google-ai-studio/gemini-2.5-pro",
        "verified": True,
        "supports_temperature": True,
        "supports_reasoning_effort": False,
        "generation_max_completion_tokens": 4096,
        "smoke_max_completion_tokens": 256,
    },
    "gemini-flash": {
        "display_name": "Gemini 2.5 Flash",
        "gateway_model": "google-ai-studio/gemini-2.5-flash",
        "verified": True,
        "supports_temperature": True,
        "supports_reasoning_effort": False,
        "generation_max_completion_tokens": 4096,
        "smoke_max_completion_tokens": 256,
    },
    "claude-sonnet": {
        "display_name": "Claude 4 Sonnet 20250522",
        "gateway_model": "anthropic/claude-4-sonnet-20250522",
        "verified": False,
        "supports_temperature": True,
        "supports_reasoning_effort": False,
        "generation_max_completion_tokens": 4096,
        "smoke_max_completion_tokens": 256,
    },
    "claude-haiku": {
        "display_name": "Claude 4.5 Haiku 20251001",
        "gateway_model": "anthropic/claude-4.5-haiku-20251001",
        "verified": False,
        "supports_temperature": True,
        "supports_reasoning_effort": False,
        "generation_max_completion_tokens": 4096,
        "smoke_max_completion_tokens": 256,
    },
    "gpt-4o-realtime": {
        "display_name": "GPT-4o Realtime",
        "gateway_model": "openai/gpt-4o-realtime",
        "verified": False,
        "supports_temperature": True,
        "supports_reasoning_effort": False,
        "generation_max_completion_tokens": 4096,
        "smoke_max_completion_tokens": 256,
    },
}

ALL_MODEL_ALIASES = tuple(MODEL_ALIASES.keys())

CANDIDATE_GATEWAY_MODELS = {
    "gpt-5": [
        "openai/gpt-5",
    ],
    "gemini-pro": [
        "google-ai-studio/gemini-2.5-pro",
    ],
    "gemini-flash": [
        "google-ai-studio/gemini-2.5-flash",
    ],
    "claude-sonnet": [
        "claude-4-sonnet-20250522",
        "anthropic/claude-4-sonnet-20250522",
        "anthropic/claude-sonnet-4-20250522",
        "bedrock/claude-4-sonnet-20250522",
    ],
    "claude-haiku": [
        "claude-4.5-haiku-20251001",
        "anthropic/claude-4.5-haiku-20251001",
        "anthropic/claude-haiku-4.5-20251001",
        "bedrock/claude-4.5-haiku-20251001",
    ],
    "gpt-4o-realtime": [
        "gpt-4o-realtime",
        "openai/gpt-4o-realtime",
        "openai/gpt-4o",
        "gpt-4o",
    ],
}

DEFAULT_RESOLVED_MODEL_MAP_FILENAME = "gateway_models.resolved.json"

# Backward-compatible export for tests/callers that only need resolved values.
MODEL_MAP = {
    alias: entry["gateway_model"]
    for alias, entry in MODEL_ALIASES.items()
}


def set_gateway_model(alias: str, gateway_model: str, *, verified: bool = True) -> None:
    MODEL_ALIASES[alias]["gateway_model"] = gateway_model
    MODEL_ALIASES[alias]["verified"] = verified
    MODEL_MAP[alias] = gateway_model
