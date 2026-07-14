from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable


def load_config_value(
    name: str,
    *,
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> str | None:
    environment = environ if environ is not None else os.environ
    env_value = clean_env_value(environment.get(name))
    if env_value:
        return env_value
    values = read_dotenv_values(env_path or Path(".env"), allowed_keys={name})
    return values.get(name)


def load_positive_int_config(
    name: str,
    *,
    default: int,
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    value = load_config_value(name, env_path=env_path, environ=environ)
    if value is None:
        if default < 1:
            raise ValueError(f"{name} default must be at least 1")
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be at least 1")
    return parsed


def read_dotenv_values(path: Path, *, allowed_keys: Iterable[str] | None = None) -> dict[str, str]:
    if not path.exists():
        return {}

    allowed = set(allowed_keys) if allowed_keys is not None else None
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if allowed is not None and key not in allowed:
            continue
        cleaned = clean_env_value(value)
        if cleaned:
            values[key] = cleaned
    return values


def clean_env_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text or None
