from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import DEFAULT_APP_SECRET, Settings


def test_production_with_default_secret_refuses_to_start():
    # The default app_secret signs the admin session cookie and the OAuth
    # state cookie. Shipping it unchanged to production makes every admin
    # session forgeable by anyone who reads this open-source default.
    with pytest.raises(ValidationError, match="CIRCLEJERK_APP_SECRET"):
        Settings(environment="production", app_secret=DEFAULT_APP_SECRET)


def test_production_with_overridden_secret_is_fine():
    settings = Settings(environment="production", app_secret="a-unique-production-secret")
    assert settings.app_secret == "a-unique-production-secret"


def test_local_with_default_secret_is_fine():
    settings = Settings(environment="local", app_secret=DEFAULT_APP_SECRET)
    assert settings.app_secret == DEFAULT_APP_SECRET


def test_test_environment_with_default_secret_is_fine():
    # This is the environment every other test in the suite runs under
    # (monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")). The guard must
    # never fire here, or it breaks the entire backend suite.
    settings = Settings(environment="test", app_secret=DEFAULT_APP_SECRET)
    assert settings.app_secret == DEFAULT_APP_SECRET
