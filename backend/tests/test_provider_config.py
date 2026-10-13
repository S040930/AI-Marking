"""provider 配置服务测试:读写语义与密钥不回显。"""

from __future__ import annotations

import json

import pytest

from app.services.provider_config import (
    OPENAI_API_KEY_KEY,
    OPENAI_BASE_URL_KEY,
    OPENAI_DEFAULT_MODEL_KEY,
    PROVIDER_CHATGPT_SUB,
    PROVIDER_DEFAULT_KEY,
    PROVIDER_OPENAI_API,
    ProviderConfigError,
    get_provider_config,
    provider_default,
    provider_summary,
    set_provider_config,
)


def test_set_and_get_round_trip(db_session):
    set_provider_config(
        db_session,
        {
            OPENAI_API_KEY_KEY: "sk-test-1234567890abcd",
            OPENAI_DEFAULT_MODEL_KEY: "gpt-5.1-codex",
            PROVIDER_DEFAULT_KEY: PROVIDER_OPENAI_API,
        },
    )
    config = get_provider_config(db_session)
    assert config[OPENAI_API_KEY_KEY] == "sk-test-1234567890abcd"
    assert config[OPENAI_DEFAULT_MODEL_KEY] == "gpt-5.1-codex"


def test_none_keeps_existing_value(db_session):
    set_provider_config(db_session, {OPENAI_API_KEY_KEY: "sk-keep-me-1234"})
    set_provider_config(db_session, {OPENAI_API_KEY_KEY: None})
    assert get_provider_config(db_session)[OPENAI_API_KEY_KEY] == "sk-keep-me-1234"


def test_empty_string_clears_key(db_session):
    set_provider_config(db_session, {OPENAI_API_KEY_KEY: "sk-remove-me"})
    set_provider_config(db_session, {OPENAI_API_KEY_KEY: ""})
    assert OPENAI_API_KEY_KEY not in get_provider_config(db_session)


def test_unknown_key_rejected(db_session):
    with pytest.raises(ProviderConfigError):
        set_provider_config(db_session, {"bogus_key": "x"})


def test_summary_hides_secret_and_reports_last4(db_session):
    set_provider_config(
        db_session,
        {
            OPENAI_API_KEY_KEY: "sk-abcdefgh1234",
            OPENAI_BASE_URL_KEY: "https://api.example/v1",
        },
    )
    summary = provider_summary(db_session)
    text = json.dumps(summary, ensure_ascii=False)
    assert "sk-abcdefgh1234" not in text
    provider = summary["providers"][PROVIDER_OPENAI_API]
    assert provider["configured"] is True
    assert provider["key_last4"] == "…1234"
    assert provider["base_url"] == "https://api.example/v1"


def test_summary_unconfigured_defaults(db_session):
    summary = provider_summary(db_session)
    assert summary["provider_default"] == PROVIDER_OPENAI_API
    assert summary["providers"][PROVIDER_OPENAI_API]["configured"] is False
    assert summary["providers"][PROVIDER_OPENAI_API]["key_last4"] is None
    # 订阅通道:占位可用性 false
    assert summary["providers"][PROVIDER_CHATGPT_SUB]["available"] is False


def test_provider_default_falls_back(db_session):
    assert provider_default(db_session) == PROVIDER_OPENAI_API
    set_provider_config(db_session, {PROVIDER_DEFAULT_KEY: PROVIDER_CHATGPT_SUB})
    assert provider_default(db_session) == PROVIDER_CHATGPT_SUB
