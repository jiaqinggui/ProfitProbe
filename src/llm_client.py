import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
_GEMINI_RATE_LOCK = threading.Lock()
_LAST_GEMINI_REQUEST = 0.0
_OPENROUTER_RATE_LOCK = threading.Lock()
_LAST_OPENROUTER_REQUEST = 0.0
_GROQ_RATE_LOCK = threading.Lock()
_LAST_GROQ_REQUEST = 0.0


def _load_environment() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv
    except ImportError:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    else:
        load_dotenv(env_path)


def _load_configuration() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        with CONFIG_PATH.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
        settings_name = config.get("llm_settings_path", "llm_settings.json")
        settings_path = (PROJECT_ROOT / settings_name).resolve()
        if PROJECT_ROOT not in settings_path.parents:
            raise RuntimeError("The configured LLM settings path must remain inside the project.")
        with settings_path.open(encoding="utf-8") as settings_file:
            return config, json.load(settings_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load LLM settings from {CONFIG_PATH}: {exc}") from exc


def _load_settings() -> dict[str, Any]:
    return _load_configuration()[1]


def load_agent_limits() -> dict[str, Any]:
    """Load shared agent and SQL limits from the editable settings file."""
    config, settings = _load_configuration()
    limits = settings.get("agent_limits")
    if not isinstance(limits, dict):
        raise RuntimeError("llm_settings.json is missing a valid agent_limits object.")
    return limits


def _wait_for_gemini_slot(settings: dict[str, Any]) -> None:
    global _LAST_GEMINI_REQUEST
    interval = float(settings.get("agent_limits", {}).get("gemini_min_interval_seconds", 13))
    if interval <= 0:
        raise RuntimeError("gemini_min_interval_seconds must be greater than zero.")
    with _GEMINI_RATE_LOCK:
        delay = interval - (time.monotonic() - _LAST_GEMINI_REQUEST)
        if delay > 0:
            print(f"    Gemini rate-limit pacing: waiting {delay:.1f}s...", flush=True)
            time.sleep(delay)
        _LAST_GEMINI_REQUEST = time.monotonic()


def _rate_limit_delay(exc: Exception) -> float | None:
    message = str(exc)
    if "429" not in message and "ResourceExhausted" not in type(exc).__name__:
        return None
    if "PerDay" in message or "per day limit exceeded" in message.lower():
        raise RuntimeError(
            "Gemini daily free-tier quota is exhausted. Wait for the quota reset "
            "or use a project/API key with available quota."
        ) from exc
    match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", message, re.IGNORECASE)
    return max(float(match.group(1)) if match else 15.0, 15.0)


def _wait_for_openrouter_slot(settings: dict[str, Any]) -> None:
    global _LAST_OPENROUTER_REQUEST
    interval = float(
        settings.get("agent_limits", {}).get("openrouter_min_interval_seconds", 4)
    )
    if interval <= 0:
        raise RuntimeError("openrouter_min_interval_seconds must be greater than zero.")
    with _OPENROUTER_RATE_LOCK:
        delay = interval - (time.monotonic() - _LAST_OPENROUTER_REQUEST)
        if delay > 0:
            print(f"    OpenRouter rate-limit pacing: waiting {delay:.1f}s...", flush=True)
            time.sleep(delay)
        _LAST_OPENROUTER_REQUEST = time.monotonic()


def _wait_for_groq_slot(settings: dict[str, Any]) -> None:
    global _LAST_GROQ_REQUEST
    interval = float(settings.get("agent_limits", {}).get("groq_min_interval_seconds", 10))
    if interval <= 0:
        raise RuntimeError("groq_min_interval_seconds must be greater than zero.")
    with _GROQ_RATE_LOCK:
        delay = interval - (time.monotonic() - _LAST_GROQ_REQUEST)
        if delay > 0:
            print(f"    Groq rate-limit pacing: waiting {delay:.1f}s...", flush=True)
            time.sleep(delay)
        _LAST_GROQ_REQUEST = time.monotonic()


def _generate_openrouter_completion(
    api_key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    top_p: Any,
    stop_sequences: list[str] | None,
    timeout: float,
    settings: dict[str, Any],
) -> str:
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "reasoning": {"exclude": True},
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if stop_sequences:
        payload["stop"] = stop_sequences

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jiaqinggui/TeleTask",
            "X-Title": "TeleTask",
        },
        method="POST",
    )
    retries = int(settings.get("agent_limits", {}).get("openrouter_rate_limit_retries", 1))
    for attempt in range(retries + 1):
        _wait_for_openrouter_slot(settings)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            choices = response_data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError(
                    f"OpenRouter returned no choices (response keys: {sorted(response_data)})."
                )
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict):
                raise RuntimeError("OpenRouter returned a choice without a message object.")
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
            if isinstance(content, str) and content.strip():
                return content.strip()
            refusal = message.get("refusal")
            finish_reason = choice.get("finish_reason", "unknown")
            raise RuntimeError(
                "OpenRouter returned no usable text "
                f"(finish_reason={finish_reason}, message_fields={sorted(message)}"
                + (f", refusal={refusal}" if refusal else "")
                + ")."
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code != 429 or attempt >= retries:
                raise RuntimeError(f"OpenRouter request failed ({exc.code}): {body}") from exc
            print(
                f"    OpenRouter rate limit reached; retrying in 15s "
                f"({attempt + 1}/{retries})...",
                flush=True,
            )
            time.sleep(15)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter network request failed: {exc.reason}") from exc
    raise RuntimeError("OpenRouter request failed after retries.")


def generate_completion(system_prompt: str, user_prompt: str, role: str) -> str:
    """Generate a completion using the provider configured for the requested role."""
    _load_environment()
    config, settings = _load_configuration()
    role_config = settings.get("roles", {}).get(role)
    if not isinstance(role_config, dict):
        roles = settings.get("roles", {})
        supported_roles = ", ".join(sorted(roles)) if isinstance(roles, dict) else ""
        raise ValueError(
            f"Unsupported LLM role '{role}'. Configured roles: {supported_roles or 'none'}."
        )

    provider_name = role_config.get("provider")
    if provider_name not in {"groq", "gemini", "openrouter"}:
        raise RuntimeError(f"Unsupported provider '{provider_name}' configured for role '{role}'.")

    api_key_names = {
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    api_key_name = api_key_names[provider_name]
    api_key = os.getenv(api_key_name)
    if not api_key:
        raise RuntimeError(f"Missing API key. Set {api_key_name} in .env or the process environment.")

    provider_defaults = config.get("llm", {}).get("providers", {}).get(provider_name, {})
    model_name = role_config.get("model_name") or provider_defaults.get("model_name")
    if not isinstance(model_name, str) or not model_name:
        raise RuntimeError(f"LLM provider '{provider_name}' is missing model_name.")

    temperature = role_config.get("temperature", 0.0)
    max_tokens = role_config.get("max_tokens", 1000)
    top_p = role_config.get("top_p")
    stop_sequences = role_config.get("stop_sequences") or None
    api_timeout = float(settings.get("agent_limits", {}).get("api_timeout_seconds", 30))
    if api_timeout <= 0:
        raise RuntimeError("api_timeout_seconds must be greater than zero.")
    print(
        f"    LLM call: role={role}, provider={provider_name}, model={model_name}",
        flush=True,
    )

    if provider_name == "openrouter":
        content = _generate_openrouter_completion(
            api_key,
            model_name,
            system_prompt,
            user_prompt,
            temperature,
            int(max_tokens),
            top_p,
            stop_sequences,
            api_timeout,
            settings,
        )
    elif provider_name == "groq":
        try:
            from groq import Groq
        except ImportError as exc:
            raise RuntimeError(
                "The Groq SDK is required. Install dependencies from requirements.txt."
            ) from exc

        _wait_for_groq_slot(settings)
        client = Groq(api_key=api_key, timeout=api_timeout, max_retries=0)
        request = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "frequency_penalty": role_config.get("frequency_penalty", 0.0),
        }
        if top_p is not None:
            request["top_p"] = top_p
        if stop_sequences:
            request["stop"] = stop_sequences
        response = client.chat.completions.create(**request)
        content = response.choices[0].message.content
    elif provider_name == "gemini":
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError(
                "The Gemini SDK is required. Install dependencies from requirements.txt."
            ) from exc

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
        )
        max_rate_limit_retries = int(
            settings.get("agent_limits", {}).get("gemini_rate_limit_retries", 1)
        )
        attempts = 0
        while True:
            _wait_for_gemini_slot(settings)
            try:
                response = model.generate_content(
                    user_prompt,
                    generation_config={
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                        **({"top_p": top_p} if top_p is not None else {}),
                        **({"stop_sequences": stop_sequences} if stop_sequences else {}),
                    },
                    request_options={"timeout": api_timeout},
                )
                break
            except Exception as exc:
                delay = _rate_limit_delay(exc)
                if delay is None or attempts >= max_rate_limit_retries:
                    raise
                attempts += 1
                print(
                    f"    Gemini rate limit reached; retrying in {delay:.1f}s "
                    f"({attempts}/{max_rate_limit_retries})...",
                    flush=True,
                )
                time.sleep(delay)
        content = response.text
    else:
        raise ValueError(f"Unsupported LLM provider '{provider_name}'.")

    if not content or not content.strip():
        raise RuntimeError(f"LLM provider '{provider_name}' returned an empty response.")
    return content.strip()
