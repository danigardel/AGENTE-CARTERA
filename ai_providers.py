from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

import requests


@dataclass(frozen=True)
class AIProviderConfig:
    provider_order: tuple[str, ...] = (
        "gemini",
        "gemini_fallback",
        "gemini_37_fallback",
        "gemini_flash_lite",
    )
    max_retries: int = 2
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    circuit_failure_threshold: int = 5
    circuit_cooldown_seconds: int = 300
    max_prompt_chars: int = 150_000
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    gemini_tpm_safety_limit_tokens: int = 200_000
    gemini_summary_chunk_bytes: int = 80_000
    gemini_summary_output_tokens: int = 2_048


DEFAULT_AI_PROVIDER_CONFIG = AIProviderConfig()


class ProviderCircuitOpen(requests.RequestException):
    pass


class PromptTooLarge(requests.RequestException):
    pass


class InvalidProviderResponse(ValueError):
    pass


@dataclass(frozen=True)
class ProviderCompletion:
    content: str
    model_used: str


def load_ai_config(path: Path | None = None) -> AIProviderConfig:
    config_path = path or Path(os.getenv("AI_CONFIG_PATH", "ai_config.json"))
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_AI_PROVIDER_CONFIG
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"No se pudo leer la configuración IA {config_path}: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError("La configuración IA debe ser un objeto JSON.")
    supported_keys = {
        "provider_order",
        "max_retries",
        "base_backoff_seconds",
        "max_backoff_seconds",
        "circuit_failure_threshold",
        "circuit_cooldown_seconds",
        "max_prompt_chars",
        "gemini_tpm_safety_limit_tokens",
        "gemini_summary_chunk_bytes",
        "gemini_summary_output_tokens",
        "providers",
        "profiles",
    }
    unknown_keys = set(raw) - supported_keys
    if unknown_keys:
        raise ValueError(
            "Claves no admitidas en configuración IA: "
            + ", ".join(sorted(str(key) for key in unknown_keys))
        )

    providers = raw.get("provider_order", ["gemini"])
    if (
        not isinstance(providers, list)
        or not providers
        or any(not isinstance(provider, str) for provider in providers)
    ):
        raise ValueError("provider_order debe ser una lista no vacía de proveedores.")
    env_order = os.getenv("AI_PROVIDERS")
    if env_order:
        providers = [item.strip() for item in env_order.split(",") if item.strip()]
        if not providers:
            raise ValueError("AI_PROVIDERS no puede ser una lista vacía.")
    provider_configs = raw.get("providers", {})
    if not isinstance(provider_configs, dict):
        raise ValueError("providers debe ser un objeto JSON.")
    unknown_providers = (
        set(providers) - set(provider_configs) if provider_configs else set()
    )
    if unknown_providers:
        raise ValueError(
            "Proveedores no definidos en ai_config.json: "
            + ", ".join(sorted(str(item) for item in unknown_providers))
        )
    profiles = raw.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("profiles debe ser un objeto JSON.")
    for profile_name, profile in profiles.items():
        if not isinstance(profile_name, str) or not isinstance(profile, dict):
            raise ValueError("Cada perfil de payload debe ser un objeto.")
        for key in (
            "max_prompt_chars",
            "company_news_items",
            "news_per_company",
            "macro_headlines",
            "watchlist_items",
            "summary_chars",
        ):
            if key in profile:
                _config_int(profile, key, 0, minimum=0, maximum=1_000_000)

    config = AIProviderConfig(
        provider_order=tuple(dict.fromkeys(providers)),
        max_retries=_config_int(raw, "max_retries", 2, minimum=0, maximum=5),
        base_backoff_seconds=_config_float(
            raw, "base_backoff_seconds", 1.0, minimum=0.0, maximum=30.0
        ),
        max_backoff_seconds=_config_float(
            raw, "max_backoff_seconds", 30.0, minimum=0.1, maximum=300.0
        ),
        circuit_failure_threshold=_config_int(
            raw, "circuit_failure_threshold", 5, minimum=1, maximum=20
        ),
        circuit_cooldown_seconds=_config_int(
            raw, "circuit_cooldown_seconds", 300, minimum=1, maximum=86400
        ),
        max_prompt_chars=_config_int(
            raw, "max_prompt_chars", 150_000, minimum=20_000, maximum=1_000_000
        ),
        providers={
            str(name): dict(spec)
            for name, spec in provider_configs.items()
            if isinstance(name, str) and isinstance(spec, dict)
        },
        profiles={
            str(name): dict(profile)
            for name, profile in profiles.items()
            if isinstance(name, str) and isinstance(profile, dict)
        },
        gemini_tpm_safety_limit_tokens=_config_int(
            raw,
            "gemini_tpm_safety_limit_tokens",
            200_000,
            minimum=1_000,
            maximum=1_000_000,
        ),
        gemini_summary_chunk_bytes=_config_int(
            raw,
            "gemini_summary_chunk_bytes",
            80_000,
            minimum=1_000,
            maximum=200_000,
        ),
        gemini_summary_output_tokens=_config_int(
            raw,
            "gemini_summary_output_tokens",
            2_048,
            minimum=256,
            maximum=16_384,
        ),
    )
    if config.max_backoff_seconds < config.base_backoff_seconds:
        raise ValueError("max_backoff_seconds no puede ser menor que base_backoff_seconds.")
    return config


def _config_int(
    raw: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} debe ser un entero.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} debe estar entre {minimum} y {maximum}.")
    return value


def _config_float(
    raw: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} debe ser numérico.")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{key} debe estar entre {minimum} y {maximum}.")
    return number


def classify_error(error: requests.RequestException) -> Literal[
    "retryable", "permanent", "prompt_too_large"
]:
    if isinstance(error, PromptTooLarge):
        return "prompt_too_large"
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return "retryable"
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429 and _response_indicates_prompt_size(response):
        return "prompt_too_large"
    if status == 413:
        return "prompt_too_large"
    if status == 429 or (isinstance(status, int) and 500 <= status <= 599):
        return "retryable"
    return "permanent"


def _response_indicates_prompt_size(response: Any) -> bool:
    json_method = getattr(response, "json", None)
    if not callable(json_method):
        return False
    try:
        payload = json_method()
    except (ValueError, AttributeError):
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message", "")
        code = error.get("code", "")
        text = f"{message} {code}"
    else:
        text = str(error or "")
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in (
            "prompt too large",
            "prompt is too long",
            "context length exceeded",
            "maximum context length",
            "context window exceeded",
            "too many tokens in prompt",
        )
    )


def parse_api_keys(value: str | None) -> list[str]:
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(key.strip() for key in value.split(",") if key.strip()))


def configured_model(spec: dict[str, Any]) -> str:
    model_env = spec.get("model_env")
    model = os.getenv(model_env) if isinstance(model_env, str) else None
    if not model:
        model = spec.get("default_model")
    return model.strip() if isinstance(model, str) else ""


def configured_api_keys(spec: dict[str, Any]) -> list[str]:
    key_env = spec.get("api_key_env")
    return parse_api_keys(os.getenv(key_env) if isinstance(key_env, str) else None)


def is_openrouter_model_allowed(model: str) -> bool:
    return (
        model == "openrouter/free"
        or model.endswith(":free")
        or os.getenv("OPENROUTER_ALLOW_PAID", "").strip().lower() == "true"
    )


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key_env: str,
        model_env: str,
        default_model: str,
        extra_headers: dict[str, str] | None = None,
        json_mode: bool = True,
        max_prompt_chars: int = 20_000,
        *,
        config: AIProviderConfig = DEFAULT_AI_PROVIDER_CONFIG,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model_env = model_env
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.json_mode = json_mode
        self.max_prompt_chars = max_prompt_chars
        self.config = config

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        state_path: Path | None = None,
    ) -> ProviderCompletion:
        selected_model = model or os.getenv(self.model_env) or self.default_model
        selected_model = selected_model.strip()
        if self.name == "openrouter" and not is_openrouter_model_allowed(
            selected_model
        ):
            logging.warning(
                "OpenRouter se desactiva: el modelo configurado no es gratuito; "
                "solo se permiten openrouter/free o modelos :free."
            )
            raise ValueError("El modelo de OpenRouter no supera la salvaguarda gratuita.")
        if len(prompt) > self.max_prompt_chars:
            raise PromptTooLarge(
                f"El prompt supera el límite configurado para {self.name}."
            )
        keys = parse_api_keys(os.getenv(self.api_key_env))
        if not keys:
            raise ValueError(f"No hay claves configuradas para {self.name}.")

        breaker_name = (
            "openrouter/free"
            if self.name == "openrouter"
            else f"{self.name}:{selected_model}"
        )
        breaker = PersistentCircuitBreaker(
            state_path,
            self.config,
            provider=breaker_name,
        )
        breaker.ensure_closed()
        base_headers = {
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        request_body: dict[str, Any] = {
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        if self.json_mode:
            request_body["response_format"] = {"type": "json_object"}
        last_error: requests.RequestException | None = None
        for key_index, key in enumerate(keys):
            headers = {
                **base_headers,
                "Authorization": f"Bearer {key}",
            }
            for attempt in range(self.config.max_retries + 1):
                try:
                    response = requests.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=request_body,
                        timeout=(10, 60),
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise InvalidProviderResponse("La respuesta no es un objeto.")
                    choices = body.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise InvalidProviderResponse("La respuesta no contiene choices.")
                    message = choices[0].get("message") if isinstance(choices[0], dict) else None
                    content = message.get("content") if isinstance(message, dict) else None
                    if isinstance(content, list):
                        content = "".join(
                            part.get("text", "")
                            for part in content
                            if isinstance(part, dict)
                            and isinstance(part.get("text"), str)
                        )
                    if not isinstance(content, str) or not content.strip():
                        raise InvalidProviderResponse("El proveedor devolvió contenido vacío.")
                    content = strip_json_fence(content)
                    model_used = body.get("model")
                    breaker.record_success()
                    return ProviderCompletion(
                        content=content,
                        model_used=(
                            model_used.strip()
                            if isinstance(model_used, str) and model_used.strip()
                            else selected_model
                        ),
                    )
                except requests.RequestException as error:
                    last_error = error
                    error_class = classify_error(error)
                    if error_class == "prompt_too_large" or error_class == "permanent":
                        break
                    if attempt >= self.config.max_retries:
                        break
                    delay = _backoff_delay(error, self.config, attempt)
                    logging.warning(
                        "Fallo transitorio en %s; reintento %d/%d en %.1f s.",
                        self.name,
                        attempt + 1,
                        self.config.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                except InvalidProviderResponse:
                    raise
            if last_error and classify_error(last_error) == "prompt_too_large":
                breaker.record_failure()
                raise last_error
            if last_error and classify_error(last_error) == "permanent":
                if key_index + 1 < len(keys):
                    continue
                breaker.record_failure()
                raise last_error
        breaker.record_failure()
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"No se obtuvo respuesta válida de {self.name}.")


def strip_json_fence(content: str) -> str:
    value = content.strip()
    if value.startswith("```"):
        value = value[3:]
        if value[:4].lower() == "json":
            value = value[4:]
        if value.endswith("```"):
            value = value[:-3]
    return value.strip()


def _backoff_delay(
    error: requests.RequestException,
    config: AIProviderConfig,
    attempt: int,
) -> float:
    retry_after = _retry_after_seconds(error)
    if retry_after is not None:
        return min(retry_after, config.max_backoff_seconds)
    exponential = min(
        config.max_backoff_seconds,
        config.base_backoff_seconds * (2**attempt),
    )
    return min(
        config.max_backoff_seconds,
        exponential * random.uniform(0.75, 1.25),
    )


def _retry_after_seconds(error: requests.RequestException) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    header_getter = getattr(headers, "get", None)
    value = header_getter("Retry-After") if callable(header_getter) else None
    if not isinstance(value, str):
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None


def count_gemini_tokens(
    url: str,
    api_key: str,
    prompt: str,
    *,
    timeout: tuple[int, int] = (10, 60),
) -> int:
    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    total_tokens = body.get("totalTokens") if isinstance(body, dict) else None
    if (
        not isinstance(total_tokens, int)
        or isinstance(total_tokens, bool)
        or total_tokens < 0
    ):
        raise InvalidProviderResponse(
            "countTokens devolvió un totalTokens no válido."
        )
    return total_tokens


class PersistentCircuitBreaker:
    def __init__(
        self,
        state_path: Path | None,
        config: AIProviderConfig,
        provider: str = "gemini",
    ) -> None:
        self.state_path = state_path
        self.config = config
        self.provider = provider

    def ensure_closed(self) -> None:
        state = self._read()
        provider_state = state.get(self.provider, {})
        if not isinstance(provider_state, dict):
            logging.warning("Se reinicia el estado inválido del cortacircuitos Gemini.")
            state.pop(self.provider, None)
            self._write(state)
            return
        opened_until = provider_state.get("opened_until")
        if not isinstance(opened_until, str):
            return
        try:
            parsed = datetime.fromisoformat(opened_until)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("opened_until sin zona horaria")
        except ValueError:
            logging.warning("Se reinicia el estado inválido del cortacircuitos Gemini.")
            state.pop(self.provider, None)
            self._write(state)
            return
        if datetime.now(timezone.utc) < parsed.astimezone(timezone.utc):
            raise ProviderCircuitOpen("El cortacircuitos Gemini está abierto temporalmente.")
        state.pop(self.provider, None)
        self._write(state)

    def record_success(self) -> None:
        state = self._read()
        state.pop(self.provider, None)
        self._write(state)

    def record_failure(self) -> None:
        state = self._read()
        provider_state = state.get(self.provider, {})
        if not isinstance(provider_state, dict):
            provider_state = {}
        failures = provider_state.get("failures", 0)
        if not isinstance(failures, int) or isinstance(failures, bool):
            failures = 0
        failures += 1
        provider_state = {"failures": failures}
        if failures >= self.config.circuit_failure_threshold:
            opened_until = datetime.now(timezone.utc).timestamp() + (
                self.config.circuit_cooldown_seconds
            )
            provider_state["opened_until"] = datetime.fromtimestamp(
                opened_until, timezone.utc
            ).isoformat()
        state[self.provider] = provider_state
        self._write(state)

    def _read(self) -> dict[str, Any]:
        if self.state_path is None:
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            logging.warning(
                "No se pudo leer el estado del cortacircuitos %s: %s",
                self.state_path,
                error,
            )
            return {}
        if not isinstance(value, dict):
            logging.warning("Se reinicia el estado inválido del cortacircuitos.")
            return {}
        return value

    def _write(self, state: dict[str, Any]) -> None:
        if self.state_path is None:
            return
        temporary_path = self.state_path.with_name(f".{self.state_path.name}.tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(state, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
            )
            os.replace(temporary_path, self.state_path)
        except (OSError, TypeError, ValueError) as error:
            logging.error(
                "No se pudo guardar el cortacircuitos %s: %s",
                self.state_path,
                error,
            )
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logging.error(
                    "No se pudo limpiar el estado temporal %s: %s",
                    temporary_path,
                    cleanup_error,
                )


def post_gemini_request(
    url: str,
    api_key: str,
    request_body: dict[str, Any],
    *,
    config: AIProviderConfig = DEFAULT_AI_PROVIDER_CONFIG,
    state_path: Path | None = None,
    timeout: tuple[int, int] = (10, 60),
    model: str | None = None,
) -> requests.Response:
    if model is None:
        path = urlsplit(url).path
        if "/models/" in path:
            model = unquote(path.rsplit("/models/", 1)[-1].split(":", 1)[0])
    breaker = PersistentCircuitBreaker(
        state_path,
        config,
        provider=f"gemini:{model}" if model else "gemini",
    )
    breaker.ensure_closed()
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    for attempt in range(config.max_retries + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=request_body,
                timeout=timeout,
            )
            response.raise_for_status()
            breaker.record_success()
            return response
        except requests.RequestException as error:
            if classify_error(error) != "retryable":
                raise
            if attempt >= config.max_retries:
                breaker.record_failure()
                raise
            retry_after = _retry_after_seconds(error)
            exponential_delay = min(
                config.max_backoff_seconds,
                config.base_backoff_seconds * (2**attempt),
            )
            delay = (
                min(retry_after, config.max_backoff_seconds)
                if retry_after is not None
                else min(
                    config.max_backoff_seconds,
                    exponential_delay * random.uniform(0.75, 1.25),
                )
            )
            logging.warning(
                "Gemini falló de forma transitoria; reintento %d/%d en %.1f s.",
                attempt + 1,
                config.max_retries,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError("La secuencia de reintentos Gemini terminó inesperadamente.")
