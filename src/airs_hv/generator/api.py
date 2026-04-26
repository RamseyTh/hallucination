from __future__ import annotations

import hashlib
import ast
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from uuid import uuid4

import requests

from .base import GenerationTrace, ModelClient
from .models import (
    ALL_MODEL_ALIASES,
    CANDIDATE_GATEWAY_MODELS,
    DEFAULT_RESOLVED_MODEL_MAP_FILENAME,
    MODEL_ALIAS_REDIRECTS,
    MODEL_ALIASES,
    set_gateway_model,
)

logger = logging.getLogger(__name__)

GATEWAY_PROVIDER = "jhu-ai-gateway"
DEFAULT_GATEWAY_BASE_URL = "https://gateway.engineering.jhu.edu/gateway"
COMPAT_CHAT_COMPLETIONS_PATH = "/compat/chat/completions"
MODEL_DISCOVERY_PATHS = (
    "/compat/models",
    "/models",
    "/compat/v1/models",
)
DEFAULT_CANDIDATE_MODELS_FILE = "gateway_model_candidates.json"
DEFAULT_SYSTEM_PROMPT = (
    "You are a coding assistant. Return only complete runnable Python source code. "
    "Do not include markdown fences, explanations, headings, comments, or analysis. "
    "Keep the implementation concise and under 200 lines unless the task explicitly "
    "requires more. Include all required imports and executable code."
)
STRICT_VISIBLE_CODE_SYSTEM_PROMPT = "Return visible Python code immediately. Output only code."
SMOKE_TEST_SYSTEM_PROMPT = "You are a concise assistant."
SMOKE_TEST_PROMPT = "Reply with exactly: OK"
STATIC_OUTPUT_PATTERNS = (
    "# Mock code for prompt:",
    "Hello, from a mock OpenAI response!",
)
INVALID_PROVIDER_CODE = 2008
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
NON_RETRYABLE_STATUS_CODES = {400, 401, 403}
POLICY_ENFORCEMENT_ERROR = "MODEL_REQUIRED_FOR_POLICY_ENFORCEMENT"
UPSTREAM_PROVIDER_AUTH_ERROR = "UPSTREAM_PROVIDER_AUTH_ERROR"
UPSTREAM_OPENAI_AUTH_ERROR_OR_WRONG_ENDPOINT = "UPSTREAM_OPENAI_AUTH_ERROR_OR_WRONG_ENDPOINT"
PYTHON_CODE_PROMPT_TEMPLATE = """Generate a complete Python script for the task below.

Rules:
- Output only Python code.
- Do not use markdown.
- Do not wrap the code in triple backticks.
- Do not include explanations.
- Do not include comments.
- Do not include headings.
- Do not include natural-language text outside the code.
- Keep the code concise.
- Avoid unnecessary helper classes or long boilerplate.
- Include enough code for the artifact to be executable.
- If the task explicitly requires a fake package, fake API, or invalid CLI flag, preserve it exactly because this is an evaluation test.

Task:
{prompt}
"""
MODEL_MAP_PLACEHOLDERS = {
    "gpt-5": "openai/gpt-5",
    "gemini-pro": "google-ai-studio/gemini-2.5-pro",
    "gemini-flash": "google-ai-studio/gemini-2.5-flash",
    "claude-sonnet": "anthropic/claude-sonnet-4",
    "claude-haiku": "anthropic/claude-haiku-4.5",
    "gpt-4o-realtime": "openai/chatgpt-4o-latest",
}


class GenerationError(RuntimeError):
    """Base error for model generation failures."""


class ConfigurationError(GenerationError):
    """Raised when gateway configuration is missing or invalid."""


class ProviderResponseError(GenerationError):
    """Raised when the gateway returns an unusable or deterministic failure response."""


@dataclass(frozen=True)
class ModelSettings:
    """Normalized gateway settings for a selected model alias."""

    provider: str
    model_alias: str
    gateway_model: str
    display_name: str
    supports_temperature: bool | None
    supports_reasoning_effort: bool
    reasoning_effort: str | None
    generation_max_completion_tokens: int
    smoke_max_completion_tokens: int
    temperature: float | None = None
    max_completion_tokens: int = 1024
    retries: int = 3
    request_timeout: float = 60.0
    retry_backoff_seconds: float = 1.0
    api_key: str | None = None
    api_base: str | None = None
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @classmethod
    def from_inputs(
        cls,
        model: str,
        config: Mapping[str, Any] | None = None,
    ) -> "ModelSettings":
        settings = dict(config or {})
        alias = normalize_model_alias(model)
        registry_entry = MODEL_ALIASES[alias]
        gateway_model = resolve_gateway_model(
            alias,
            gateway_model_override=_optional_string(settings.get("gateway_model_override")),
            gateway_model_map=settings.get("gateway_model_map"),
            gateway_model_map_path=_optional_string(settings.get("gateway_model_map_path")),
        )
        supports_temperature = registry_entry.get("supports_temperature")
        supports_reasoning_effort = bool(registry_entry.get("supports_reasoning_effort", False))
        raw_temperature = settings.get("temperature")
        resolved_temperature: float | None
        if supports_temperature is False:
            resolved_temperature = None
        elif raw_temperature is None:
            resolved_temperature = None
        else:
            resolved_temperature = float(raw_temperature)
        default_generation_max_completion_tokens = int(
            registry_entry.get("generation_max_completion_tokens", 1024)
        )
        max_completion_tokens = _optional_int(settings.get("max_tokens"))
        resolved_reasoning_effort = None
        if supports_reasoning_effort:
            resolved_reasoning_effort = _optional_string(settings.get("reasoning_effort")) or _optional_string(
                registry_entry.get("default_reasoning_effort")
            )

        return cls(
            provider=GATEWAY_PROVIDER,
            model_alias=alias,
            gateway_model=gateway_model,
            display_name=str(registry_entry["display_name"]),
            supports_temperature=supports_temperature,
            supports_reasoning_effort=supports_reasoning_effort,
            reasoning_effort=resolved_reasoning_effort,
            generation_max_completion_tokens=default_generation_max_completion_tokens,
            smoke_max_completion_tokens=int(registry_entry.get("smoke_max_completion_tokens", 256)),
            temperature=resolved_temperature,
            max_completion_tokens=max_completion_tokens or default_generation_max_completion_tokens,
            retries=max(1, int(settings.get("retries", 3))),
            request_timeout=float(settings.get("request_timeout", 60.0)),
            retry_backoff_seconds=float(settings.get("retry_backoff_seconds", 1.0)),
            api_key=_optional_string(settings.get("api_key")),
            api_base=_resolve_gateway_base_override(settings),
            system_prompt=str(settings.get("system_prompt", DEFAULT_SYSTEM_PROMPT)),
        )


@dataclass(frozen=True)
class SmokeTestResult:
    alias: str
    gateway_model: str | None
    status: str
    raw_text: str
    matched_ok: bool
    error: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "gateway_model": self.gateway_model,
            "status": self.status,
            "raw_text": self.raw_text,
            "response_preview": _truncate(self.raw_text, limit=120),
            "matched_ok": self.matched_ok,
            "error": self.error,
        }


@dataclass(frozen=True)
class GatewayModelDiscoveryResult:
    models: list[str]
    policy_enforced: bool
    errors: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "models": self.models,
            "policy_enforced": self.policy_enforced,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class GatewayProbeResult:
    alias: str
    candidate_model: str
    status: str
    response_preview: str
    error: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "candidate_model": self.candidate_model,
            "status": self.status,
            "response_preview": self.response_preview,
            "error": self.error,
        }


class GatewayModelClient(ModelClient):
    """JHU AI Gateway-backed implementation using compat chat completions."""

    def __init__(self, settings: ModelSettings):
        self._settings = settings
        self._api_key = require_api_key(settings.api_key)
        self._base_url = validate_gateway_base_url(settings.api_base or get_gateway_base_from_env())
        self._url = f"{self._base_url.rstrip('/')}{COMPAT_CHAT_COMPLETIONS_PATH}"
        self._last_trace: GenerationTrace | None = None
        self._last_raw_output: str | None = None

    @property
    def provider(self) -> str:
        return GATEWAY_PROVIDER

    @property
    def model_name(self) -> str:
        return self._settings.model_alias

    @property
    def model_target(self) -> str:
        return self._settings.model_alias

    @property
    def last_trace(self) -> GenerationTrace | None:
        return self._last_trace

    @property
    def last_raw_output(self) -> str | None:
        return self._last_raw_output

    @property
    def gateway_model_id(self) -> str:
        return self._settings.gateway_model

    def generate(self, prompt: str, *, prompt_id: str | None = None) -> str:
        return self.generate_text(
            build_generation_user_prompt(prompt),
            prompt_id=prompt_id,
            output_validator=validate_python_only_output,
        )

    def generate_text(
        self,
        prompt: str,
        *,
        prompt_id: str | None = None,
        output_validator: Callable[[str], str] | None = None,
    ) -> str:
        request_id = new_request_id()
        text, response, response_json, payload = call_with_retries(
            lambda attempt: self._call_gateway(
                prompt=prompt,
                request_id=request_id,
                attempt=attempt,
                prompt_id=prompt_id,
            ),
            settings=self._settings,
            url=self._url,
            model_alias=self._settings.model_alias,
            gateway_model=self._settings.gateway_model,
        )
        self._last_raw_output = text
        if output_validator is not None:
            text = output_validator(text)

        request_metadata = build_request_metadata(
            provider=self.provider,
            model_alias=self._settings.model_alias,
            gateway_model=self._settings.gateway_model,
            prompt=prompt,
            max_completion_tokens=self._settings.max_completion_tokens,
            temperature=payload.get("temperature"),
            reasoning_effort=payload.get("reasoning_effort"),
            request_timeout=self._settings.request_timeout,
            request_id=request_id,
            url=self._url,
            prompt_id=prompt_id,
        )
        self._last_trace = GenerationTrace(
            provider=self.provider,
            model_name=self._settings.model_alias,
            model_target=self._settings.model_alias,
            request=request_metadata,
            response={
                "status_code": response.status_code,
                "gateway_request_id": response.headers.get("x-request-id")
                or response.headers.get("x-gateway-request-id"),
                "gateway_model": self._settings.gateway_model,
                "usage": normalize_usage(extract_usage(response_json)),
                "finish_reason": extract_finish_reason(response_json),
            },
        )
        return text

    def _call_gateway(
        self,
        *,
        prompt: str,
        request_id: str,
        attempt: int,
        prompt_id: str | None,
    ) -> tuple[str, requests.Response, Any, Mapping[str, Any]]:
        del request_id
        include_temperature = self._settings.temperature is not None
        return self._post_with_optional_temperature(
            prompt=prompt,
            attempt=attempt,
            prompt_id=prompt_id,
            include_temperature=include_temperature,
            temperature_retry_allowed=True,
            empty_output_retry_allowed=True,
            max_completion_tokens_override=None,
            system_prompt_override=None,
        )

    def _post_with_optional_temperature(
        self,
        *,
        prompt: str,
        attempt: int,
        prompt_id: str | None,
        include_temperature: bool,
        temperature_retry_allowed: bool,
        empty_output_retry_allowed: bool,
        max_completion_tokens_override: int | None,
        system_prompt_override: str | None,
    ) -> tuple[str, requests.Response, Any, Mapping[str, Any]]:
        current_max_completion_tokens = (
            max_completion_tokens_override or self._settings.max_completion_tokens
        )
        payload = build_chat_payload(
            gateway_model=self._settings.gateway_model,
            prompt=prompt,
            system_prompt=system_prompt_override or self._settings.system_prompt,
            max_completion_tokens=current_max_completion_tokens,
            temperature=self._settings.temperature if include_temperature else None,
            reasoning_effort=self._settings.reasoning_effort,
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "Gateway request prompt_id=%s model_alias=%s gateway_model=%s url=%s attempt=%s auth_present=%s key_prefix=%s",
            prompt_id,
            self._settings.model_alias,
            self._settings.gateway_model,
            self._url,
            attempt,
            bool(headers.get("Authorization")),
            masked_key_prefix(self._api_key),
        )
        response = requests.post(
            self._url,
            headers=headers,
            json=payload,
            timeout=self._settings.request_timeout,
        )
        response_json = safe_response_json(response)
        error_code, error_message = extract_gateway_error(response_json)

        if response.status_code >= 400:
            redacted_payload = redact_payload_for_logs(payload)
            logger.warning(
                "Gateway request failed attempt=%s url=%s model_alias=%s gateway_model=%s "
                "prompt_id=%s status=%s error_code=%s error_message=%s auth_present=%s "
                "key_prefix=%s provider_error_type=%s body=%s payload=%s",
                attempt,
                self._url,
                self._settings.model_alias,
                self._settings.gateway_model,
                prompt_id,
                response.status_code,
                error_code,
                error_message,
                bool(headers.get("Authorization")),
                masked_key_prefix(self._api_key),
                classify_gateway_error(response.status_code, response.text, error_message),
                _truncate(response.text),
                redacted_payload,
            )

            if (
                response.status_code == 400
                and include_temperature
                and temperature_retry_allowed
                and is_temperature_rejection(error_code, error_message, response_json, response.text)
            ):
                logger.info(
                    "Gateway rejected temperature; retrying once with temperature omitted. "
                    "model_alias=%s gateway_model=%s",
                    self._settings.model_alias,
                    self._settings.gateway_model,
                )
                return self._post_with_optional_temperature(
                    prompt=prompt,
                    attempt=attempt,
                    prompt_id=prompt_id,
                    include_temperature=False,
                    temperature_retry_allowed=False,
                    empty_output_retry_allowed=empty_output_retry_allowed,
                    max_completion_tokens_override=max_completion_tokens_override,
                    system_prompt_override=system_prompt_override,
                )

            if response.status_code == 400 and is_invalid_provider_error(error_code, error_message):
                raise ProviderResponseError(
                    "Invalid provider means the gateway model ID is wrong or the alias was sent "
                    f"instead of a provider-qualified model ID. alias='{self._settings.model_alias}' "
                    f"gateway_model='{self._settings.gateway_model}'"
                )

            if not is_retryable_gateway_error(response.status_code, response.text):
                raise ProviderResponseError(
                    build_non_retryable_gateway_error(
                        model_alias=self._settings.model_alias,
                        gateway_model=self._settings.gateway_model,
                        status_code=response.status_code,
                        body=response.text,
                        error_message=error_message,
                    )
                )

            http_error = requests.HTTPError(
                f"{response.status_code} Client Error: {response.reason} for url: {self._url}"
            )
            http_error.response = response
            raise http_error

        if not isinstance(response_json, (Mapping, list)):
            raise ProviderResponseError("Gateway returned a non-object JSON response.")

        finish_reason = extract_finish_reason(response_json)
        if is_truncated_finish_reason(finish_reason):
            logger.warning(
                "Generation may be truncated: finish_reason=%s model=%s prompt_id=%s max_completion_tokens=%s",
                finish_reason,
                self._settings.model_alias,
                prompt_id,
                current_max_completion_tokens,
            )

        try:
            text = extract_text_from_gateway_response(response_json)
        except ProviderResponseError as exc:
            if empty_output_retry_allowed and is_reasoning_exhaustion_response(response_json):
                logger.info(
                    "Gateway response parser found no visible text after reasoning exhaustion; "
                    "retrying once with strict visible-code prompt. model_alias=%s gateway_model=%s",
                    self._settings.model_alias,
                    self._settings.gateway_model,
                )
                return self._post_with_optional_temperature(
                    prompt=prompt,
                    attempt=attempt,
                    prompt_id=prompt_id,
                    include_temperature=False,
                    temperature_retry_allowed=False,
                    empty_output_retry_allowed=False,
                    max_completion_tokens_override=self._settings.max_completion_tokens,
                    system_prompt_override=STRICT_VISIBLE_CODE_SYSTEM_PROMPT,
                )
            raise ProviderResponseError(
                build_empty_response_error(
                    model_alias=self._settings.model_alias,
                    gateway_model=self._settings.gateway_model,
                    prompt_id=prompt_id,
                    max_completion_tokens=current_max_completion_tokens,
                    response_json=response_json,
                    parser_details=str(exc),
                )
            ) from exc

        if not text.strip():
            if empty_output_retry_allowed and is_reasoning_exhaustion_response(response_json):
                logger.info(
                    "Gateway returned no visible text after reasoning exhaustion; retrying once "
                    "with strict visible-code prompt. model_alias=%s gateway_model=%s",
                    self._settings.model_alias,
                    self._settings.gateway_model,
                )
                return self._post_with_optional_temperature(
                    prompt=prompt,
                    attempt=attempt,
                    prompt_id=prompt_id,
                    include_temperature=False,
                    temperature_retry_allowed=False,
                    empty_output_retry_allowed=False,
                    max_completion_tokens_override=self._settings.max_completion_tokens,
                    system_prompt_override=STRICT_VISIBLE_CODE_SYSTEM_PROMPT,
                )
            raise ProviderResponseError(
                build_empty_response_error(
                    model_alias=self._settings.model_alias,
                    gateway_model=self._settings.gateway_model,
                    prompt_id=prompt_id,
                    max_completion_tokens=current_max_completion_tokens,
                    response_json=response_json,
                )
            )

        return text, response, response_json, payload


def normalize_model_alias(model: str) -> str:
    normalized = str(model).strip().lower()
    normalized = MODEL_ALIAS_REDIRECTS.get(normalized, normalized)
    if normalized not in MODEL_ALIASES:
        supported = tuple(ALL_MODEL_ALIASES) + tuple(MODEL_ALIAS_REDIRECTS)
        raise ConfigurationError(
            f"Unsupported model '{model}'. Supported values: {', '.join(supported)}"
        )
    return normalized


def resolve_model_selection(model_spec: str) -> list[str]:
    normalized = str(model_spec or "").strip().lower()
    if not normalized:
        raise ConfigurationError("Model selection must be a non-empty string.")
    if normalized == "all":
        return list(ALL_MODEL_ALIASES)

    aliases: list[str] = []
    seen: set[str] = set()
    for token in normalized.split(","):
        alias = token.strip().lower()
        if not alias:
            continue
        alias = normalize_model_alias(alias)
        if alias not in seen:
            aliases.append(alias)
            seen.add(alias)
    if not aliases:
        raise ConfigurationError(
            f"Model selection '{model_spec}' did not contain any valid model aliases."
        )
    return aliases


def resolve_gateway_model(
    alias: str,
    gateway_model_override: str | None = None,
    *,
    gateway_model_map: Mapping[str, Any] | None = None,
    gateway_model_map_path: str | None = None,
) -> str:
    model_alias = normalize_model_alias(alias)
    override = _optional_string(gateway_model_override)
    if override is not None:
        return override

    model_map = resolve_gateway_model_map(
        gateway_model_map=gateway_model_map,
        gateway_model_map_path=gateway_model_map_path,
    )
    mapped_gateway_model = _optional_string(model_map.get(model_alias))
    if mapped_gateway_model:
        return mapped_gateway_model

    gateway_model = MODEL_ALIASES[model_alias]["gateway_model"]
    if gateway_model is None:
        raise ConfigurationError(
            f"Model alias '{model_alias}' does not have a configured gateway model ID."
        )
    return str(gateway_model)


def parse_model_target(
    model: str,
    *,
    gateway_model_override: str | None = None,
) -> tuple[str, str, str]:
    alias = normalize_model_alias(model)
    gateway_model = resolve_gateway_model(alias, gateway_model_override=gateway_model_override)
    return GATEWAY_PROVIDER, gateway_model, alias


def alias_to_gateway_model_env_var(alias: str) -> str:
    normalized = normalize_model_alias(alias)
    token = "".join(char if char.isalnum() else "_" for char in normalized).upper()
    return f"GATEWAY_MODEL_{token}"


def resolve_gateway_model_map(
    *,
    gateway_model_map: Mapping[str, Any] | None = None,
    gateway_model_map_path: str | None = None,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    if gateway_model_map is not None:
        resolved.update(_normalize_gateway_model_mapping(gateway_model_map))
    if gateway_model_map_path:
        resolved.update(load_gateway_model_map(Path(gateway_model_map_path)))
    return resolved


def load_gateway_model_map(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigurationError(f"Gateway model map file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Gateway model map file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ConfigurationError(f"Gateway model map file must contain a JSON object: {path}")
    return _normalize_gateway_model_mapping(data)


def _normalize_gateway_model_mapping(data: Mapping[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for alias, value in data.items():
        normalized_alias = normalize_model_alias(alias)
        normalized_value = _optional_string(value)
        if normalized_value and not normalized_value.startswith("<exact-"):
            resolved[normalized_alias] = normalized_value
    return resolved


def build_model_client(
    model: str,
    config: Mapping[str, Any] | None = None,
) -> ModelClient:
    settings = ModelSettings.from_inputs(model, config)
    return GatewayModelClient(settings)


def generate_code(prompt: str, model: str, config: dict[str, Any] | None = None) -> str:
    client = build_model_client(model, config)
    return client.generate(prompt)


def smoke_test_gateway(
    model: str = "gpt-5",
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    alias = normalize_model_alias(model)
    smoke_config = build_smoke_test_config(alias, config)
    gateway_model = resolve_gateway_model(
        alias,
        gateway_model_override=_optional_string(smoke_config.get("gateway_model_override")),
        gateway_model_map=smoke_config.get("gateway_model_map"),
        gateway_model_map_path=_optional_string(smoke_config.get("gateway_model_map_path")),
    )

    try:
        client = build_model_client(alias, smoke_config)
    except ConfigurationError as exc:
        return SmokeTestResult(
            alias=alias,
            gateway_model=gateway_model,
            status="error",
            raw_text="",
            matched_ok=False,
            error=str(exc),
        ).as_dict()

    assert isinstance(client, GatewayModelClient)
    try:
        text = client.generate_text(SMOKE_TEST_PROMPT, output_validator=None)
        normalized = validate_smoke_test_output(text)
        matched_ok = "OK" in normalized.upper()
        status = "pass" if matched_ok else "fail"
        error = "" if matched_ok else "Smoke test response did not contain OK."
        return SmokeTestResult(
            alias=alias,
            gateway_model=client.gateway_model_id,
            status=status,
            raw_text=normalized,
            matched_ok=matched_ok,
            error=error,
        ).as_dict()
    except GenerationError as exc:
        return SmokeTestResult(
            alias=alias,
            gateway_model=client.gateway_model_id,
            status="error",
            raw_text="",
            matched_ok=False,
            error=str(exc),
        ).as_dict()


def smoke_test_models(
    model_spec: str,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    aliases = resolve_model_selection(model_spec)
    auto_repair = bool((config or {}).get("auto_repair_policy_models")) or str(model_spec).strip().lower() == "all"
    results: list[dict[str, Any]] = []
    for alias in aliases:
        alias_config = dict(config or {})
        if len(aliases) != 1:
            alias_config.pop("gateway_model_override", None)
        result = smoke_test_gateway(alias, alias_config)
        if auto_repair and should_attempt_model_repair(result):
            result = repair_smoke_test_gateway(alias, alias_config, result)
        results.append(result)
    return results


def build_smoke_test_config(
    alias: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    smoke_config = dict(config or {})
    registry_entry = MODEL_ALIASES[normalize_model_alias(alias)]
    smoke_config["system_prompt"] = SMOKE_TEST_SYSTEM_PROMPT
    smoke_config["max_tokens"] = int(registry_entry.get("smoke_max_completion_tokens", 256))
    if registry_entry.get("supports_temperature") is False:
        smoke_config["temperature"] = None
    return smoke_config


def validate_smoke_test_output(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        raise ProviderResponseError("Smoke test returned empty text.")
    return normalized


def discover_gateway_models(
    config: Mapping[str, Any] | None = None,
) -> GatewayModelDiscoveryResult:
    settings = dict(config or {})
    api_key = require_api_key(_optional_string(settings.get("api_key")))
    base_url = validate_gateway_base_url(
        _resolve_gateway_base_override(settings) or get_gateway_base_from_env()
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = float(settings.get("request_timeout", 60.0))
    errors: list[dict[str, Any]] = []
    policy_enforced = False

    for path in MODEL_DISCOVERY_PATHS:
        url = f"{base_url.rstrip('/')}{path}"
        try:
            logger.info("Gateway model discovery url=%s", url)
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code >= 400:
                response_json = safe_response_json(response)
                _, error_message = extract_gateway_error(response_json)
                body_text = response.text or ""
                if POLICY_ENFORCEMENT_ERROR in body_text.upper() or POLICY_ENFORCEMENT_ERROR in error_message.upper():
                    policy_enforced = True
                logger.warning(
                    "Gateway model discovery failed url=%s status=%s body=%s",
                    url,
                    response.status_code,
                    _truncate(response.text),
                )
                errors.append(
                    {
                        "url": url,
                        "status_code": response.status_code,
                        "body": _truncate(body_text),
                        "error_message": error_message,
                    }
                )
                continue
            data = safe_response_json(response)
            models = parse_model_list_response(data)
            if models:
                return GatewayModelDiscoveryResult(
                    models=models,
                    policy_enforced=False,
                    errors=errors,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gateway model discovery failed url=%s error=%s", url, exc)
            errors.append({"url": url, "error": str(exc)})
    return GatewayModelDiscoveryResult(models=[], policy_enforced=policy_enforced, errors=errors)


def list_gateway_models(config: Mapping[str, Any] | None = None) -> list[str]:
    return discover_gateway_models(config).models


def gateway_model_listing_guidance() -> str:
    return (
        "JHU Gateway did not allow generic model listing because policy enforcement requires a model field. "
        "This is expected for some keys. Use one of the following:\n\n"
        "1. Check the JHU Gateway docs in your authenticated browser:\n"
        "   https://gateway.engineering.jhu.edu/docs#openai\n\n"
        "2. Probe candidate model IDs:\n"
        f"   python run_pipeline.py --probe-gateway-models --candidate-models-file {DEFAULT_CANDIDATE_MODELS_FILE}\n\n"
        "3. Pass a model explicitly:\n"
        "   python run_pipeline.py --smoke-test --model gemini-pro --gateway-model '<exact-gateway-model-id>'\n\n"
        "4. Use a model map:\n"
        "   python run_pipeline.py --smoke-test-all --gateway-model-map gateway_models.json"
    )


def load_gateway_model_candidates(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        raise ConfigurationError(f"Gateway candidate models file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Gateway candidate models file is not valid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise ConfigurationError(f"Gateway candidate models file must contain a JSON object: {path}")

    normalized: dict[str, list[str]] = {}
    for alias, values in data.items():
        normalized_alias = normalize_model_alias(alias)
        if isinstance(values, str):
            candidates = [_optional_string(values)] if _optional_string(values) else []
        elif isinstance(values, list):
            candidates = [candidate for value in values if (candidate := _optional_string(value))]
        else:
            raise ConfigurationError(
                f"Candidate models for alias '{normalized_alias}' must be a string or list of strings."
            )
        normalized[normalized_alias] = candidates
    return normalized


def probe_gateway_models(
    candidate_models: Mapping[str, list[str]],
    config: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    settings = dict(config or {})
    api_key = require_api_key(_optional_string(settings.get("api_key")))
    base_url = validate_gateway_base_url(
        _resolve_gateway_base_override(settings) or get_gateway_base_from_env()
    )
    timeout = float(settings.get("request_timeout", 60.0))
    url = f"{base_url.rstrip('/')}{COMPAT_CHAT_COMPLETIONS_PATH}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    results: dict[str, list[dict[str, Any]]] = {}
    for alias in ALL_MODEL_ALIASES:
        alias_candidates = list(candidate_models.get(alias, []))
        alias_results: list[dict[str, Any]] = []
        for candidate_model in alias_candidates:
            alias_results.append(
                probe_gateway_candidate(
                    alias=alias,
                    candidate_model=candidate_model,
                    url=url,
                    headers=headers,
                    timeout=timeout,
                ).as_dict()
            )
        results[alias] = alias_results
    return results


def probe_gateway_candidate(
    *,
    alias: str,
    candidate_model: str,
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> GatewayProbeResult:
    payload = {
        "model": candidate_model,
        "messages": [
            {"role": "system", "content": SMOKE_TEST_SYSTEM_PROMPT},
            {"role": "user", "content": SMOKE_TEST_PROMPT},
        ],
        "max_completion_tokens": 256,
    }
    logger.info(
        "Gateway probe alias=%s candidate_model=%s url=%s",
        alias,
        candidate_model,
        url,
    )
    try:
        response = requests.post(url, headers=dict(headers), json=payload, timeout=timeout)
    except requests.Timeout as exc:
        return GatewayProbeResult(
            alias=alias,
            candidate_model=candidate_model,
            status="failed",
            response_preview="",
            error=f"timeout: {exc}",
        )
    except requests.ConnectionError as exc:
        return GatewayProbeResult(
            alias=alias,
            candidate_model=candidate_model,
            status="failed",
            response_preview="",
            error=f"connection error: {exc}",
        )

    response_json = safe_response_json(response)
    error_code, error_message = extract_gateway_error(response_json)
    body_text = response.text or ""
    upper_body = body_text.upper()
    error_type = classify_gateway_error(response.status_code, body_text, error_message)

    if response.status_code == 403 and "MODEL_NOT_ALLOWED_FOR_KEY" in upper_body:
        return GatewayProbeResult(alias, candidate_model, "model_not_allowed", "", "MODEL_NOT_ALLOWED_FOR_KEY")
    if response.status_code == 400 and is_policy_enforcement_error(error_code, error_message, response_json, body_text):
        return GatewayProbeResult(alias, candidate_model, "model_required_for_policy", "", POLICY_ENFORCEMENT_ERROR)
    if response.status_code == 400 and is_invalid_provider_error(error_code, error_message):
        return GatewayProbeResult(alias, candidate_model, "invalid_provider", "", error_message or body_text)
    if response.status_code == 400 and is_invalid_model_error(error_message, body_text):
        return GatewayProbeResult(alias, candidate_model, "invalid_model", "", error_message or body_text)
    if response.status_code == 401 and error_type == "upstream_provider_auth_error":
        return GatewayProbeResult(alias, candidate_model, "upstream_provider_auth_error", "", error_message or body_text)
    if response.status_code == 401 and error_type == "upstream_openai_auth_error_or_wrong_endpoint":
        return GatewayProbeResult(
            alias,
            candidate_model,
            "upstream_openai_auth_error_or_wrong_endpoint",
            "",
            error_message or body_text,
        )
    if response.status_code == 401:
        return GatewayProbeResult(
            alias,
            candidate_model,
            "failed",
            "",
            "missing or invalid GATEWAY_KEY",
        )
    if response.status_code >= 400:
        if error_type == "unsupported_endpoint":
            return GatewayProbeResult(alias, candidate_model, "unsupported_endpoint", "", error_message or body_text)
        return GatewayProbeResult(
            alias,
            candidate_model,
            "failed",
            "",
            _truncate(error_message or body_text, limit=240),
        )

    try:
        text = extract_text_from_gateway_response(response_json)
    except ProviderResponseError as exc:
        return GatewayProbeResult(
            alias,
            candidate_model,
            "unknown",
            "",
            _truncate(str(exc), limit=240),
        )

    preview = _truncate(text.strip(), limit=120)
    if preview:
        return GatewayProbeResult(alias, candidate_model, "pass", preview, "")
    return GatewayProbeResult(
        alias,
        candidate_model,
        "unknown",
        "",
        "Response did not contain visible text.",
    )


def is_invalid_model_error(error_message: str, body_text: str) -> bool:
    combined = f"{error_message} {body_text}".lower()
    return any(
        token in combined
        for token in (
            "model not found",
            "unknown model",
            "invalid model",
            "model_not_found",
            "no such model",
        )
    )


def suggest_gateway_model_map(discovered_models: list[str]) -> dict[str, str]:
    suggestions: dict[str, str] = {}
    lowered_models = {model_id: model_id.lower() for model_id in discovered_models}

    def unique_match(*keywords: str) -> str | None:
        matches = [
            model_id
            for model_id, lowered in lowered_models.items()
            if all(keyword in lowered for keyword in keywords)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    matchers = {
        "gpt-5": ("gpt-5",),
        "gemini-pro": ("gemini", "pro"),
        "gemini-flash": ("gemini", "flash"),
        "claude-sonnet": ("claude", "sonnet"),
        "claude-haiku": ("claude", "haiku"),
        "gpt-4o-realtime": ("gpt-4o", "realtime"),
    }

    for alias in ALL_MODEL_ALIASES:
        default_model = _optional_string(MODEL_ALIASES[alias].get("gateway_model"))
        if default_model:
            suggestions[alias] = default_model
            continue
        guessed = unique_match(*matchers[alias])
        suggestions[alias] = guessed or MODEL_MAP_PLACEHOLDERS[alias]

    return suggestions


def write_gateway_model_map(path: Path, discovered_models: list[str]) -> dict[str, str]:
    suggestions = suggest_gateway_model_map(discovered_models)
    path.write_text(json.dumps(suggestions, indent=2) + "\n", encoding="utf-8")
    return suggestions


def recommend_probed_gateway_models(
    probe_results: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, str | None]:
    recommendations: dict[str, str | None] = {}
    for alias in ALL_MODEL_ALIASES:
        alias_results = list(probe_results.get(alias, []))
        allowed = [
            str(result["candidate_model"])
            for result in alias_results
            if result.get("status") in {"allowed", "pass"}
        ]
        recommendations[alias] = allowed[0] if len(allowed) == 1 else None
    return recommendations


def write_probed_gateway_model_map(
    path: Path,
    probe_results: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, str | None]:
    recommendations = recommend_probed_gateway_models(probe_results)
    path.write_text(json.dumps(recommendations, indent=2) + "\n", encoding="utf-8")
    return recommendations


def write_resolved_gateway_model_map(
    path: Path,
    smoke_results: list[Mapping[str, Any]],
) -> dict[str, str | None]:
    resolved: dict[str, str | None] = {}
    by_alias = {str(result.get("alias")): result for result in smoke_results}
    for alias in ALL_MODEL_ALIASES:
        result = by_alias.get(alias, {})
        resolved[alias] = (
            str(result.get("gateway_model"))
            if result.get("status") == "pass" and result.get("gateway_model")
            else None
        )
    path.write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    return resolved


def new_request_id() -> str:
    return uuid4().hex


def build_chat_payload(
    *,
    gateway_model: str,
    prompt: str,
    system_prompt: str,
    max_completion_tokens: int,
    temperature: float | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    payload = {
        "model": gateway_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": max_completion_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def build_generation_user_prompt(prompt: str) -> str:
    return PYTHON_CODE_PROMPT_TEMPLATE.format(prompt=prompt)


def build_request_metadata(
    *,
    provider: str,
    model_alias: str,
    gateway_model: str,
    prompt: str,
    max_completion_tokens: int,
    temperature: float | None,
    reasoning_effort: str | None,
    request_timeout: float,
    request_id: str,
    url: str,
    prompt_id: str | None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "provider": provider,
        "model_alias": model_alias,
        "gateway_model": gateway_model,
        "prompt_id": prompt_id,
        "temperature": temperature,
        "reasoning_effort": reasoning_effort,
        "max_completion_tokens": max_completion_tokens,
        "request_timeout": request_timeout,
        "cache_disabled": True,
        "url": url,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
    }


def validate_generated_output(text: str) -> str:
    return validate_python_only_output(text)


def validate_python_only_output(text: str) -> str:
    raw_text = text or ""
    normalized = raw_text.strip()
    if not normalized:
        raise ProviderResponseError("Gateway returned no visible generated artifact text.")
    if any(pattern in normalized for pattern in STATIC_OUTPUT_PATTERNS):
        raise ProviderResponseError("Gateway returned a known static placeholder output.")
    stripped = strip_markdown_fences(normalized)
    leading = stripped.lstrip().lower()
    disallowed_prefixes = (
        "```python",
        "```",
        "here is",
        "explanation:",
        "sure",
        "this script",
    )
    if any(leading.startswith(prefix) for prefix in disallowed_prefixes):
        raise ProviderResponseError("Gateway returned non-code wrapper text instead of Python source.")
    if is_mostly_non_code(stripped):
        raise ProviderResponseError("Gateway returned mostly non-code text instead of Python source.")
    incomplete_reason = detect_incomplete_python_source(stripped)
    if incomplete_reason:
        raise ProviderResponseError(
            f"Gateway returned incomplete Python source: {incomplete_reason}."
        )
    if stripped != normalized:
        logger.warning("Removed markdown code fences from generated output before evaluation.")
        return stripped
    return raw_text


def strip_markdown_fences(text: str) -> str:
    normalized = text.strip()
    if not normalized.startswith("```"):
        return normalized
    lines = normalized.splitlines()
    if lines and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return normalized


def is_mostly_non_code(text: str) -> bool:
    stripped = text.strip()
    lowered = stripped.lower()
    if not stripped:
        return True
    if lowered.startswith(("here is", "explanation:", "sure", "this script")):
        return True
    try:
        ast.parse(stripped)
        return False
    except SyntaxError:
        pass
    code_markers = (
        "import ",
        "from ",
        "def ",
        "class ",
        "print(",
        "if __name__",
        "=",
        "for ",
        "while ",
        "try:",
        "with ",
    )
    return not any(marker in stripped for marker in code_markers)


def detect_incomplete_python_source(text: str) -> str | None:
    stripped = text.rstrip()
    if not stripped:
        return "empty output"
    last_line = stripped.splitlines()[-1].strip()
    block_headers = (
        "if ",
        "elif ",
        "else:",
        "for ",
        "while ",
        "try:",
        "except",
        "finally:",
        "def ",
        "class ",
        "with ",
        "async def ",
        "async for ",
        "async with ",
        "match ",
        "case ",
    )
    if last_line.endswith(":") and last_line.startswith(block_headers):
        return f"output ends with an unfinished block header ({last_line})"
    if last_line.endswith(("(", "[", "{", "\\", ",")):
        return f"output ends with incomplete punctuation ({last_line[-1]})"
    try:
        ast.parse(stripped)
    except SyntaxError as exc:
        message = str(exc).lower()
        incomplete_markers = (
            "unexpected eof",
            "was never closed",
            "unterminated string literal",
            "unterminated triple-quoted string literal",
            "eof while scanning",
            "expected an indented block",
        )
        if any(marker in message for marker in incomplete_markers):
            return exc.msg
    return None


def is_truncated_finish_reason(finish_reason: Any) -> bool:
    return isinstance(finish_reason, str) and finish_reason.lower() in {"length", "max_tokens"}


def masked_key_prefix(api_key: str | None) -> str:
    if not api_key:
        return ""
    return f"{api_key[:8]}..."


def classify_gateway_error(
    status_code: int | None,
    body: str | None,
    error_message: str = "",
) -> str:
    combined = f"{body or ''} {error_message}".lower()
    if "invalid anthropic api key" in combined:
        return "upstream_provider_auth_error"
    if "you didn't provide an api key" in combined or "you did not provide an api key" in combined:
        return "upstream_openai_auth_error_or_wrong_endpoint"
    if status_code == 401:
        return "gateway_auth_error"
    if POLICY_ENFORCEMENT_ERROR.lower() in combined:
        return "model_required_for_policy"
    if "invalid provider" in combined:
        return "invalid_provider"
    if "model_not_allowed_for_key" in combined:
        return "model_not_allowed"
    if "unsupported endpoint" in combined or "not supported on this endpoint" in combined:
        return "unsupported_endpoint"
    return "unknown"


def is_reasoning_exhaustion_response(response_json: Any) -> bool:
    diagnostics = describe_response_diagnostics(response_json)
    finish_reason = diagnostics.get("finish_reason")
    if not (isinstance(finish_reason, str) and finish_reason.lower() in {"length", "max_tokens"}):
        return False
    usage = diagnostics.get("usage")
    if not isinstance(usage, Mapping):
        return False
    completion_tokens = usage.get("completion_tokens")
    reasoning_tokens = extract_reasoning_tokens(usage)
    return (
        isinstance(completion_tokens, int)
        and isinstance(reasoning_tokens, int)
        and completion_tokens > 0
        and reasoning_tokens >= completion_tokens
    )


def call_with_retries(
    operation: Any,
    *,
    settings: ModelSettings,
    url: str,
    model_alias: str,
    gateway_model: str,
) -> tuple[str, requests.Response, Any, Mapping[str, Any]]:
    attempts = max(1, settings.retries)
    last_error: Exception | None = None
    attempts_made = 0

    for attempt in range(1, attempts + 1):
        attempts_made = attempt
        try:
            return operation(attempt)
        except (ConfigurationError, ProviderResponseError):
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Gateway generation attempt=%s/%s failed url=%s model_alias=%s gateway_model=%s error=%s",
                attempt,
                attempts,
                url,
                model_alias,
                gateway_model,
                exc,
            )
            if attempt >= attempts or not is_retryable_exception(exc):
                break
            time.sleep(settings.retry_backoff_seconds * (2 ** (attempt - 1)))

    assert last_error is not None
    raise GenerationError(
        f"{settings.provider} generation failed for alias='{model_alias}' "
        f"gateway_model='{gateway_model}' after {attempts_made} attempt(s) at {url}: {last_error}"
    ) from last_error


def is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        status_code = response.status_code if response is not None else None
        response_text = response.text if response is not None else None
        return is_retryable_gateway_error(status_code, response_text)
    return False


def is_retryable_gateway_error(status_code: int | None, response_text: str | None) -> bool:
    if status_code in RETRYABLE_STATUS_CODES:
        return True

    if status_code in NON_RETRYABLE_STATUS_CODES:
        return False

    if response_text:
        upper = response_text.upper()
        if "MODEL_NOT_ALLOWED_FOR_KEY" in upper:
            return False
        if POLICY_ENFORCEMENT_ERROR in upper:
            return False
        if "INVALID PROVIDER" in upper:
            return False
        if '"CODE":2008' in upper:
            return False

    return False


def build_non_retryable_gateway_error(
    *,
    model_alias: str,
    gateway_model: str,
    status_code: int | None,
    body: str | None,
    error_message: str,
) -> str:
    body_text = _truncate(body or "", limit=400)
    upper_body = (body or "").upper()

    if status_code == 400 and is_policy_enforcement_error(None, error_message, None, body or ""):
        return (
            f"{POLICY_ENFORCEMENT_ERROR} means the Gateway did not recognize the model string "
            "for policy enforcement.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}\n\n"
            "Try a provider-qualified Gateway ID such as openai/..., anthropic/..., or "
            "google-ai-studio/...."
        )

    if status_code == 403 and "MODEL_NOT_ALLOWED_FOR_KEY" in upper_body:
        return (
            "Gateway model is not allowed for this key.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}\n\n"
            "This usually means the model mapping is wrong or the key is not approved for that model.\n"
            "For GPT-5, use openai/gpt-5, not openai/gpt-5.2."
        )

    if status_code == 401 and classify_gateway_error(status_code, body, error_message) == "upstream_provider_auth_error":
        return (
            f"{UPSTREAM_PROVIDER_AUTH_ERROR}.\n"
            "The shared JHU Gateway key works for other models, but this model produced an "
            "upstream Anthropic authentication error. This likely means the model ID or "
            "provider namespace is wrong for the Gateway, or the request is bypassing Gateway "
            "routing/provider routing is misconfigured. Do not assume GATEWAY_KEY is globally invalid.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}"
        )

    if status_code == 401 and classify_gateway_error(status_code, body, error_message) == "upstream_openai_auth_error_or_wrong_endpoint":
        return (
            f"{UPSTREAM_OPENAI_AUTH_ERROR_OR_WRONG_ENDPOINT}.\n"
            "The shared JHU Gateway key works for other models, but this model produced an "
            "upstream OpenAI-style authentication error. This likely means the request is "
            "bypassing Gateway routing, the model ID is wrong, or GPT-4o Realtime is not "
            "supported on the chat completions endpoint.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}"
        )

    if status_code == 401:
        return (
            "Gateway authentication failed.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}\n\n"
            "Check that GATEWAY_KEY is set and approved for this JHU Gateway deployment."
        )

    if status_code == 403:
        return (
            "Gateway request was forbidden.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}\n\n"
            "This usually means the key is not approved for the requested model."
        )

    if status_code == 400 and is_invalid_provider_error(None, f"{error_message} {body or ''}"):
        return (
            "Invalid provider means the gateway model ID is wrong or the alias was sent instead "
            "of a provider-qualified model ID.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}"
        )

    if status_code == 400 and is_temperature_rejection(None, error_message, None, body or ""):
        return (
            "Gateway rejected the supplied temperature.\n"
            f"model_alias={model_alias}\n"
            f"gateway_model={gateway_model}\n"
            f"status={status_code}\n"
            f"body={body_text}"
        )

    return (
        "Gateway returned a non-retryable error.\n"
        f"model_alias={model_alias}\n"
        f"gateway_model={gateway_model}\n"
        f"status={status_code}\n"
        f"body={body_text}"
    )


def build_empty_response_error(
    *,
    model_alias: str,
    gateway_model: str,
    prompt_id: str | None,
    max_completion_tokens: int,
    response_json: Any,
    parser_details: str | None = None,
) -> str:
    diagnostics = describe_response_diagnostics(response_json)
    lines = [
        "Gateway returned no visible text.",
        "",
        "Diagnostics:",
        f"- model_alias={model_alias}",
        f"- gateway_model={gateway_model}",
        f"- prompt_id={prompt_id}",
        f"- max_completion_tokens={max_completion_tokens}",
        f"- finish_reason={diagnostics.get('finish_reason')}",
        f"- usage={diagnostics.get('usage')}",
        f"- response_keys={diagnostics.get('top_level_keys')}",
        f"- first_choice={diagnostics.get('first_choice')}",
        f"- response_json={diagnostics.get('response_json')}",
    ]
    if parser_details:
        lines.append(f"- parser_details={parser_details}")
    finish_reason = diagnostics.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason.lower() in {"length", "max_tokens"}:
        lines.append(
            "- The finish_reason suggests token exhaustion. Try increasing max_completion_tokens."
        )
    usage = diagnostics.get("usage")
    if isinstance(usage, Mapping):
        completion_tokens = usage.get("completion_tokens")
        if isinstance(completion_tokens, int) and completion_tokens > 0:
            lines.append(
                f"- The gateway reports completion_tokens={completion_tokens} but no visible text was extracted."
            )
        reasoning_tokens = extract_reasoning_tokens(usage)
        if isinstance(reasoning_tokens, int) and reasoning_tokens > 0:
            lines.append(
                f"- The gateway reports reasoning_tokens={reasoning_tokens}."
            )
            if isinstance(completion_tokens, int) and reasoning_tokens >= completion_tokens:
                lines.append(
                    "- Completion tokens were consumed by reasoning without visible output. "
                    "Try a lower reasoning effort or a higher max_completion_tokens budget."
                )
    return "\n".join(lines)


def require_api_key(explicit_api_key: str | None = None) -> str:
    api_key = _optional_string(explicit_api_key)
    if api_key:
        return api_key
    env_key = _optional_string(os.getenv("GATEWAY_KEY"))
    if env_key:
        return env_key
    legacy_key = _optional_string(os.getenv("JHU_AI_GATEWAY_API_KEY"))
    if legacy_key:
        logger.warning(
            "Using deprecated environment variable JHU_AI_GATEWAY_API_KEY. Prefer GATEWAY_KEY."
        )
        return legacy_key
    raise ConfigurationError(
        "GATEWAY_KEY is not set. Export GATEWAY_KEY='jhu_live_sk_...' before running."
    )


def get_gateway_base_from_env() -> str:
    base = _optional_string(os.getenv("GATEWAY_BASE"))
    if base:
        return base
    legacy_base = _optional_string(os.getenv("JHU_AI_GATEWAY_API_BASE_URL"))
    if legacy_base:
        logger.warning(
            "Using deprecated environment variable JHU_AI_GATEWAY_API_BASE_URL. Prefer GATEWAY_BASE."
        )
        return legacy_base
    return DEFAULT_GATEWAY_BASE_URL


def validate_gateway_base_url(url: str | None) -> str:
    normalized = _optional_string(url)
    if not normalized:
        raise ConfigurationError("GATEWAY_BASE must be set to a valid gateway URL.")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(
            f"GATEWAY_BASE is invalid: '{normalized}'. Expected something like "
            "'https://gateway.engineering.jhu.edu/gateway'."
        )
    return normalized


def optional_string(value: Any) -> str | None:
    return _optional_string(value)


def normalize_usage(usage: Any) -> Any:
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        return dict(usage)

    usage_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    normalized = {
        field: getattr(usage, field)
        for field in usage_fields
        if hasattr(usage, field) and getattr(usage, field) is not None
    }
    if normalized:
        return normalized

    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    to_dict = getattr(usage, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if hasattr(usage, "__dict__"):
        return usage.__dict__
    return str(usage)


def extract_reasoning_tokens(usage: Any) -> int | None:
    normalized = normalize_usage(usage)
    if not isinstance(normalized, Mapping):
        return None
    details = normalized.get("completion_tokens_details")
    if isinstance(details, Mapping):
        reasoning_tokens = details.get("reasoning_tokens")
        if isinstance(reasoning_tokens, int):
            return reasoning_tokens
    return None


def extract_text_from_gateway_response(response_json: Any) -> str:
    if isinstance(response_json, Mapping):
        choices = response_json.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            first_choice = choices[0]
            message = first_choice.get("message")
            if isinstance(message, Mapping):
                content = _extract_text_candidate(message.get("content"))
                if content is not None:
                    return content
            direct_content = _extract_text_candidate(first_choice.get("content"))
            if direct_content is not None:
                return direct_content
            choice_text = _extract_text_candidate(first_choice.get("text"))
            if choice_text is not None:
                return choice_text

        for key in ("result", "output_text", "content"):
            candidate = _extract_text_candidate(response_json.get(key))
            if candidate is not None:
                return candidate

    elif isinstance(response_json, list):
        candidate = _extract_text_candidate(response_json)
        if candidate is not None:
            return candidate

    raise ProviderResponseError(build_response_parser_diagnostics(response_json))


def extract_gateway_response_text(response_json: Any) -> str:
    return extract_text_from_gateway_response(response_json)


def _extract_text_candidate(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            candidate = _extract_text_candidate(item)
            if candidate is not None:
                parts.append(candidate)
        return "".join(parts) if parts else None
    if isinstance(value, Mapping):
        for key in ("text", "output_text"):
            candidate = _extract_text_candidate(value.get(key))
            if candidate is not None:
                return candidate
        message = value.get("message")
        if message is not None:
            candidate = _extract_text_candidate(message)
            if candidate is not None:
                return candidate
        content = value.get("content")
        if content is not None:
            candidate = _extract_text_candidate(content)
            if candidate is not None:
                return candidate
        result = value.get("result")
        if result is not None:
            candidate = _extract_text_candidate(result)
            if candidate is not None:
                return candidate
    return None


def build_response_parser_diagnostics(response_json: Any) -> str:
    diagnostics = describe_response_diagnostics(response_json)
    return (
        "Could not extract visible text from gateway response.\n"
        f"top_level_keys={diagnostics.get('top_level_keys')}\n"
        f"choices_length={diagnostics.get('choices_length')}\n"
        f"first_choice_keys={diagnostics.get('first_choice_keys')}\n"
        f"message_keys={diagnostics.get('message_keys')}\n"
        f"finish_reason={diagnostics.get('finish_reason')}\n"
        f"usage={diagnostics.get('usage')}\n"
        f"response_json={diagnostics.get('response_json')}"
    )


def describe_response_diagnostics(response_json: Any) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "top_level_keys": None,
        "choices_length": None,
        "first_choice_keys": None,
        "message_keys": None,
        "finish_reason": None,
        "usage": None,
        "first_choice": None,
        "response_json": _truncate(json.dumps(response_json, default=str), limit=4000),
    }
    if isinstance(response_json, Mapping):
        diagnostics["top_level_keys"] = sorted(str(key) for key in response_json.keys())
        choices = response_json.get("choices")
        if isinstance(choices, list):
            diagnostics["choices_length"] = len(choices)
            if choices and isinstance(choices[0], Mapping):
                diagnostics["first_choice_keys"] = sorted(str(key) for key in choices[0].keys())
                diagnostics["first_choice"] = _truncate(
                    json.dumps(choices[0], default=str),
                    limit=800,
                )
                message = choices[0].get("message")
                if isinstance(message, Mapping):
                    diagnostics["message_keys"] = sorted(str(key) for key in message.keys())
        diagnostics["finish_reason"] = extract_finish_reason(response_json)
        diagnostics["usage"] = normalize_usage(extract_usage(response_json))
    elif isinstance(response_json, list):
        diagnostics["top_level_keys"] = f"list[{len(response_json)}]"
    else:
        diagnostics["top_level_keys"] = type(response_json).__name__
    return diagnostics


def safe_response_json(response: requests.Response) -> Mapping[str, Any] | list[Any] | None:
    try:
        return response.json()
    except ValueError:
        return None


def extract_usage(data: Any) -> Any:
    if isinstance(data, Mapping) and "usage" in data:
        return data["usage"]
    return None


def extract_finish_reason(data: Any) -> Any:
    if isinstance(data, Mapping):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            return choices[0].get("finish_reason")
        return data.get("status")
    return None


def extract_gateway_error(data: Any) -> tuple[Any, str]:
    if isinstance(data, Mapping):
        error = data.get("error")
        if isinstance(error, list) and error and isinstance(error[0], Mapping):
            return error[0].get("code"), str(error[0].get("message", ""))
        if isinstance(error, Mapping):
            return error.get("code"), str(error.get("message", ""))
        if isinstance(error, str):
            return None, error
        message = data.get("message")
        if isinstance(message, str):
            return None, message
    return None, ""


def is_invalid_provider_error(error_code: Any, error_message: str) -> bool:
    if error_code == INVALID_PROVIDER_CODE:
        return True
    return "invalid provider" in error_message.lower()


def is_policy_enforcement_error(
    error_code: Any,
    error_message: str,
    response_json: Any,
    response_text: str,
) -> bool:
    del error_code
    lowered = f"{error_message} {response_text}".upper()
    if POLICY_ENFORCEMENT_ERROR in lowered:
        return True
    if isinstance(response_json, Mapping):
        error = response_json.get("error")
        if isinstance(error, str) and POLICY_ENFORCEMENT_ERROR in error.upper():
            return True
        if isinstance(error, Mapping):
            message = str(error.get("message", ""))
            if POLICY_ENFORCEMENT_ERROR in message.upper():
                return True
    return False


def is_temperature_rejection(
    error_code: Any,
    error_message: str,
    response_json: Any,
    response_text: str,
) -> bool:
    lowered = f"{error_message} {response_text}".lower()
    if error_code == "unsupported_value" and "temperature" in lowered:
        return True
    if isinstance(response_json, Mapping):
        error = response_json.get("error")
        if isinstance(error, Mapping):
            if error.get("param") == "temperature" and error.get("code") == "unsupported_value":
                return True
    return "temperature" in lowered and "unsupported value" in lowered


def parse_model_list_response(data: Any) -> list[str]:
    if isinstance(data, Mapping):
        records = data.get("data")
        if isinstance(records, list):
            return [
                record["id"]
                for record in records
                if isinstance(record, Mapping) and isinstance(record.get("id"), str)
            ]
        result = data.get("result")
        if isinstance(result, list):
            return [
                record["id"]
                for record in result
                if isinstance(record, Mapping) and isinstance(record.get("id"), str)
            ]
    if isinstance(data, list):
        return [
            record["id"]
            for record in data
            if isinstance(record, Mapping) and isinstance(record.get("id"), str)
        ]
    return []


def describe_response_shape(data: Any) -> dict[str, Any]:
    if isinstance(data, Mapping):
        return {"type": "mapping", "keys": sorted(str(key) for key in data.keys())}
    if isinstance(data, list):
        return {"type": "list", "length": len(data)}
    return {"type": type(data).__name__}


def redact_payload_for_logs(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    messages = []
    for message in payload.get("messages", []):
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        redacted_content = content
        if isinstance(content, str) and message.get("role") == "user":
            redacted_content = _truncate(content, limit=160)
        messages.append({"role": message.get("role"), "content": redacted_content})
    if messages:
        sanitized["messages"] = messages
    return sanitized


def should_attempt_model_repair(result: Mapping[str, Any]) -> bool:
    if result.get("status") == "pass":
        return False
    error = str(result.get("error", ""))
    upper_error = error.upper()
    return (
        POLICY_ENFORCEMENT_ERROR in upper_error
        or UPSTREAM_PROVIDER_AUTH_ERROR in upper_error
        or UPSTREAM_OPENAI_AUTH_ERROR_OR_WRONG_ENDPOINT in upper_error
    )


def should_attempt_policy_repair(result: Mapping[str, Any]) -> bool:
    return should_attempt_model_repair(result)


def repair_smoke_test_gateway(
    alias: str,
    config: Mapping[str, Any] | None,
    initial_result: Mapping[str, Any],
) -> dict[str, Any]:
    current_gateway_model = _optional_string(initial_result.get("gateway_model")) or _optional_string(
        MODEL_ALIASES[alias].get("gateway_model")
    )
    attempts: list[str] = []

    for candidate in unique_candidate_gateway_models(alias, current_gateway_model):
        logger.info(
            "Trying candidate model_alias=%s candidate_gateway_model=%s",
            alias,
            candidate,
        )
        candidate_config = dict(config or {})
        candidate_config["gateway_model_override"] = candidate
        result = smoke_test_gateway(alias, candidate_config)
        attempts.append(
            f"{candidate} -> {result.get('status')}: {_truncate(str(result.get('error', '')) or str(result.get('response_preview', '')), limit=180)}"
        )

        if result.get("status") == "pass":
            logger.info("Candidate passed model_alias=%s gateway_model=%s", alias, candidate)
            set_gateway_model(alias, candidate, verified=True)
            return dict(result)

        if should_attempt_model_repair(result):
            logger.info(
                "Candidate rejected by Gateway routing/auth model_alias=%s gateway_model=%s",
                alias,
                candidate,
            )

    repaired = dict(initial_result)
    attempts_text = "\n".join(f"- {attempt}" for attempt in attempts) if attempts else "- none"
    repaired["error"] = (
        f"{initial_result.get('error', '')}\n\nCandidate attempts:\n{attempts_text}"
    ).strip()
    return repaired


def unique_candidate_gateway_models(alias: str, current_gateway_model: str | None) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in CANDIDATE_GATEWAY_MODELS.get(alias, []):
        if candidate == current_gateway_model:
            continue
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    return candidates


def unresolved_aliases(
    aliases: list[str],
    *,
    gateway_model_map: Mapping[str, Any] | None = None,
    gateway_model_map_path: str | Path | None = None,
) -> list[str]:
    unresolved: list[str] = []
    path_value = str(gateway_model_map_path) if gateway_model_map_path is not None else None
    for alias in aliases:
        try:
            resolve_gateway_model(
                alias,
                gateway_model_map=gateway_model_map,
                gateway_model_map_path=path_value,
            )
        except ConfigurationError:
            unresolved.append(alias)
    return unresolved


def _resolve_gateway_base_override(settings: Mapping[str, Any]) -> str | None:
    return _optional_string(settings.get("gateway_base_url") or settings.get("api_base"))


def _truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
