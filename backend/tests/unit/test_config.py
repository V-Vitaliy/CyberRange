import os
import unittest
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.config import ROOT_DIR, Settings


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
            "postgresql+asyncpg://cyberadmin:cyberpassword123@localhost:5432/cyberrange",
        )
        self.assertEqual(settings.REDIS_URL, "redis://localhost:6379")

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
    env = tmp_path / '.env'
    env.write_text(
        'LLM_PROVIDER=openai_compatible\n'
        'LLM_BASE_URL=http://inference.invalid:9001/v1\n'
        'LLM_MODEL=custom-model-id\n'
        'LLM_API_KEY=\n'
        'LLM_MAX_TOKENS=3072\n'
    )
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=env)
    assert settings.LLM_PROVIDER == 'openai_compatible'
    assert settings.LLM_BASE_URL == 'http://inference.invalid:9001/v1'
    assert settings.LLM_MODEL == 'custom-model-id'
    assert settings.LLM_API_KEY == ''
    assert settings.LLM_MAX_TOKENS == 3072
