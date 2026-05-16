from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

BASE_DIR = Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    return Path(os.getenv("NODEPOINT_SETTINGS", BASE_DIR / "settings.toml"))


@lru_cache(maxsize=1)
def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def get_section(name: str) -> dict[str, Any]:
    section = load_settings().get(name, {})
    return section if isinstance(section, dict) else {}


def _env_or(section: dict[str, Any], env_key: str, section_key: str, default: Any = None) -> Any:
    value = os.getenv(env_key)
    if value is not None and value != "":
        return value
    return section.get(section_key, default)


def postgres_config() -> dict[str, Any]:
    section = get_section("postgresql")
    return {
        "NAME": _env_or(section, "POSTGRES_NAME", "name"),
        "USER": _env_or(section, "POSTGRES_USER", "user"),
        "PASSWORD": _env_or(section, "POSTGRES_PASSWORD", "password"),
        "HOST": _env_or(section, "POSTGRES_HOST", "host"),
        "PORT": _env_or(section, "POSTGRES_PORT", "port"),
    }


def redis_config() -> dict[str, Any]:
    section = get_section("redis")
    host = _env_or(section, "REDIS_HOST", "host", "localhost")
    port = _env_or(section, "REDIS_PORT", "port", 6379)
    db = _env_or(section, "REDIS_DB", "db", 0)
    return {
        "HOST": host,
        "PORT": int(port) if port is not None else 6379,
        "DB": int(db) if db is not None else 0,
    }


def mongo_config() -> dict[str, Any]:
    section = get_section("mongo")
    return {
        "user": _env_or(section, "MONGO_USER", "user", "admin"),
        "password": _env_or(section, "MONGO_PASSWORD", "password", "password"),
        "host": _env_or(section, "MONGO_HOST", "host", "localhost"),
        "port": int(_env_or(section, "MONGO_PORT", "port", 27017)),
        "database": _env_or(section, "MONGO_DATABASE", "database", "contentdb"),
        "auth_source": _env_or(section, "MONGO_AUTH_SOURCE", "auth_source", "admin"),
    }


def mongo_uri() -> str:
    cfg = mongo_config()
    user = quote_plus(str(cfg["user"]))
    password = quote_plus(str(cfg["password"]))
    host = cfg["host"]
    port = cfg["port"]
    database = cfg["database"]
    auth_source = cfg["auth_source"]
    return (
        f"mongodb://{user}:{password}@{host}:{port}/{database}"
        f"?authSource={auth_source}"
    )


def quadrant_config() -> dict[str, Any]:
    section = get_section("quadrant")
    return {
        "host": _env_or(section, "QDRANT_HOST", "host", "localhost"),
        "port": int(_env_or(section, "QDRANT_PORT", "port", 6333)),
        "collection_name": _env_or(section, "QDRANT_COLLECTION", "collection_name", "nodepoint"),
        "vector_size": int(_env_or(section, "QDRANT_VECTOR_SIZE", "vector_size", 256)),
    }
