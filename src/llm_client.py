import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
_GEMINI_RATE_LOCK = threading.Lock()
_LAST_GEMINI_REQUEST = 0.0


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
    match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", message, re.IGNORECASE)
    return max(float(match.group(1)) if match else 15.0, 15.0)


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
    if provider_name not in {"groq", "gemini"}:
        raise RuntimeError(f"Unsupported provider '{provider_name}' configured for role '{role}'.")

    api_key_name = "GROQ_API_KEY" if provider_name == "groq" else "GEMINI_API_KEY"
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

    if provider_name == "groq":
        try:
            from groq import Groq
        except ImportError as exc:
            raise RuntimeError(
                "The Groq SDK is required. Install dependencies from requirements.txt."
            ) from exc

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
