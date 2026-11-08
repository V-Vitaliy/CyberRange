import os
import unittest
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.config import ROOT_DIR, Settings, require_strong_jwt_secret


class TestSettings(unittest.TestCase):
    def setUp(self):
        env_patch = patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_local_settings_do_not_require_cloud_or_smtp(self):
        settings = Settings(_env_file=None)

        self.assertEqual(settings.GROQ_API_KEY, "")
        self.assertEqual(settings.SMTP_HOST, "")
        self.assertEqual(settings.SMTP_PORT, 587)
        self.assertEqual(settings.SMTP_USER, "")
        self.assertEqual(settings.SMTP_PASSWORD, "")

    def test_example_environment_loads_without_external_credentials(self):
        example = ROOT_DIR / ".env.example"
        settings = Settings(_env_file=example)

        self.assertEqual(settings.GROQ_API_KEY, "")
        self.assertEqual(settings.SMTP_HOST, "")
        self.assertEqual(
            settings.DATABASE_URL,
            "postgresql+asyncpg://cyberadmin:<POSTGRES_PASSWORD>@localhost:5432/cyberrange",
        )
        self.assertEqual(settings.REDIS_URL, "redis://:<REDIS_PASSWORD>@localhost:6379/0")

    def test_environment_overrides_example_settings(self):
        example = ROOT_DIR / ".env.example"
        overrides = {
            "DATABASE_URL": "postgresql+asyncpg://student:example@state-server:5433/lab",
            "REDIS_URL": "redis://state-server:6380/2",
            "GROQ_API_KEY": "test-key-unused",
            "SMTP_HOST": "smtp.example.invalid",
            "SMTP_PORT": "2525",
            "SMTP_USER": "test-user",
            "SMTP_PASSWORD": "test-password",
        }
        with patch.dict(os.environ, overrides):
            settings = Settings(_env_file=example)

        for name, value in overrides.items():
            with self.subTest(setting=name):
                expected = int(value) if name == "SMTP_PORT" else value
                self.assertEqual(getattr(settings, name), expected)


def test_provider_settings_are_explicit_and_legacy_default_is_preserved(monkeypatch):
    for key in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_MAX_TOKENS", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.LLM_PROVIDER == settings.GROQ_API_KEY == settings.LLM_API_KEY == ""
    assert settings.LLM_BASE_URL == ""
    assert settings.LLM_MODEL == ""
    assert settings.LLM_MAX_TOKENS == 2048
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_PROVIDER="typo")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_MAX_TOKENS=0)


def test_http_connection_settings_are_loaded_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER=openai_compatible\n"
        "LLM_BASE_URL=http://inference.invalid:9001/v1\n"
        "LLM_MODEL=custom-model-id\n"
        "LLM_API_KEY=\n"
        "LLM_MAX_TOKENS=3072\n"
    )
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=env)
    assert settings.LLM_PROVIDER == "openai_compatible"
    assert settings.LLM_BASE_URL == "http://inference.invalid:9001/v1"
    assert settings.LLM_MODEL == "custom-model-id"
    assert settings.LLM_API_KEY == ""
    assert settings.LLM_MAX_TOKENS == 3072


@pytest.mark.parametrize(
    "secret",
    ["", "short-secret", "super-secret-blue-team-key", "replace_this_with_a_long_random_string_in_production"],
)
def test_weak_or_published_jwt_secret_is_rejected(secret):
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        require_strong_jwt_secret(secret)


def test_random_jwt_secret_is_accepted():
    require_strong_jwt_secret("a" * 32)


def test_frontend_origins_default_to_local_dev_server_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)
    assert Settings(_env_file=None).FRONTEND_ORIGINS == ["http://localhost:3000", "http://127.0.0.1:3000"]
    monkeypatch.setenv("FRONTEND_ORIGINS", '["https://range.example"]')
    assert Settings(_env_file=None).FRONTEND_ORIGINS == ["https://range.example"]


def test_object_storage_settings_default_to_local_gateway_without_credentials(monkeypatch):
    for key in ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.S3_ENDPOINT_URL == "http://localhost:8333"
    assert settings.S3_BUCKET == "lab-uploads"
    assert settings.S3_ACCESS_KEY == settings.S3_SECRET_KEY == ""


def test_model_concurrency_defaults_to_one_generation_per_lab(monkeypatch):
    for key in ("LLM_MAX_CONCURRENCY", "LLM_MAX_CONCURRENCY_PER_LAB"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert (settings.LLM_MAX_CONCURRENCY, settings.LLM_MAX_CONCURRENCY_PER_LAB) == (1, 1)
    example = Settings(_env_file=ROOT_DIR / ".env.example")
    assert (example.LLM_MAX_CONCURRENCY, example.LLM_MAX_CONCURRENCY_PER_LAB) == (1, 1)
    for key in ("LLM_MAX_CONCURRENCY", "LLM_MAX_CONCURRENCY_PER_LAB"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{key: 0})
