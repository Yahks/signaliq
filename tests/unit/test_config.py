"""Config must load from env and validate required fields."""
import os
from importlib import reload

import pytest

from packages.core.config import Settings


def test_config_requires_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """If SECRET_KEY is absent from both env and .env, Settings must fail."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    # _env_file=None bypasses .env loading for this one instantiation.
    with pytest.raises(Exception):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "abcdefghijklmnop")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("DATABASE_URL_SYNC", "postgresql://x:x@localhost/x")

    from packages.core import config as config_module

    reload(config_module)
    config_module.get_settings.cache_clear()
    s = config_module.get_settings()
    assert s.secret_key == "abcdefghijklmnop"
    assert s.jwt_algorithm == "HS256"
