"""Required root configuration and secrets read directly from files."""

import json
import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class Config:
    memory_dir: Path
    index_path: Path
    host: str
    port: int
    timezone: str
    max_file_bytes: int
    search_default_limit: int
    read_max_paths: int
    context_token_budget: int
    duplicate_threshold: float
    sensitive_patterns: list[str]
    git_author_name: str
    git_author_email: str
    env_path: Path
    clients_path: Path
    oauth_db_path: Path
    access_log_path: Path
    public_url: str
    oauth_apps: list[str]
    oauth_scopes: list[str]
    authorization_code_ttl_seconds: int
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    rate_limit_per_minute: int
    allowed_hosts: list[str]
    allowed_origins: list[str]
    deployment: dict


def load_config(path: Path) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    required = {field.name for field in fields(Config)}
    missing = required - data.keys()
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(sorted(missing)))
    unknown = data.keys() - required
    if unknown:
        raise ValueError("Unknown configuration: " + ", ".join(sorted(unknown)))
    for name in ("host", "timezone", "git_author_name", "git_author_email"):
        if not isinstance(data[name], str) or not data[name].strip():
            raise ValueError(f"Invalid configuration: {name}")
    for name in ("port", "max_file_bytes", "search_default_limit", "read_max_paths", "context_token_budget",
                 "authorization_code_ttl_seconds", "access_token_ttl_seconds",
                 "refresh_token_ttl_seconds", "rate_limit_per_minute"):
        if type(data[name]) is not int or data[name] <= 0:
            raise ValueError(f"Invalid configuration: {name}")
    if data["port"] > 65535:
        raise ValueError("Invalid configuration: port")
    if not isinstance(data["duplicate_threshold"], (int, float)) or not 0 < data["duplicate_threshold"] <= 1:
        raise ValueError("Invalid configuration: duplicate_threshold")
    public = urlsplit(data["public_url"])
    if (public.scheme not in {"https", "http"} or not public.hostname or
            public.path or public.query or public.fragment or public.username or public.password or
            (public.scheme == "http" and public.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Invalid configuration: public_url must be an HTTPS origin (HTTP loopback for development)")
    for name in ("oauth_apps", "oauth_scopes", "allowed_hosts", "allowed_origins"):
        if not isinstance(data[name], list) or not data[name] or not all(isinstance(x, str) and x for x in data[name]):
            raise ValueError(f"Invalid configuration: {name}")
    if not all(re.fullmatch(r"[a-zA-Z0-9_-]+", x) for x in data["oauth_apps"]):
        raise ValueError("Invalid configuration: oauth_apps")
    deployment_keys = {"operator_user", "service_user", "home_dir", "app_dir", "state_dir", "code_remote",
                       "memory_remote", "python_command", "health_timeout_seconds", "tunnel_name", "tunnel_metrics"}
    deployment = data["deployment"]
    if not isinstance(deployment, dict) or set(deployment) != deployment_keys:
        raise ValueError("Invalid configuration: deployment requires " + ", ".join(sorted(deployment_keys)))
    if type(deployment["health_timeout_seconds"]) is not int or deployment["health_timeout_seconds"] <= 0:
        raise ValueError("Invalid configuration: deployment.health_timeout_seconds")
    if not all(isinstance(value, str) and value for key, value in deployment.items() if key != "health_timeout_seconds"):
        raise ValueError("Invalid configuration: deployment values")
    patterns = data["sensitive_patterns"]
    if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) and p for p in patterns):
        raise ValueError("Invalid configuration: sensitive_patterns")
    for pattern in patterns:
        re.compile(pattern)
    ZoneInfo(data["timezone"])
    root = path.resolve().parent
    for name in ("memory_dir", "index_path", "env_path", "clients_path", "oauth_db_path", "access_log_path"):
        if not isinstance(data[name], str) or not data[name].strip():
            raise ValueError(f"Invalid configuration: {name}")
        data[name] = (root / data[name]).resolve()
    config = Config(**data)
    if any(p.is_relative_to(config.memory_dir) for p in (config.index_path, config.env_path, config.oauth_db_path, config.access_log_path)):
        raise ValueError("Index and secrets must be outside the memory repository")
    return config


def load_tokens(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError("Secrets file is missing")
    values = dotenv_values(path, interpolate=False)
    raw = values.get("STATIC_TOKENS")
    if raw is None:
        raise ValueError("Missing STATIC_TOKENS in secrets file")
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Invalid STATIC_TOKENS JSON") from None
    if not isinstance(tokens, dict) or not tokens:
        raise ValueError("STATIC_TOKENS must be a nonempty token-to-identity mapping")
    if not all(isinstance(token, str) and token and isinstance(identity, str)
               and re.fullmatch(r"[a-zA-Z0-9_-]+", identity)
               for token, identity in tokens.items()):
        raise ValueError("Invalid token or identity in STATIC_TOKENS")
    return tokens
