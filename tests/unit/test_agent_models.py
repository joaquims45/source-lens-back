import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from sourcelens.agent.models import get_chat_model
from sourcelens.api.errors import DomainError
from sourcelens.config import Settings


def test_get_chat_model_requires_an_api_key_for_anthropic():
    settings = Settings(agent_provider="anthropic", anthropic_api_key=None)
    with pytest.raises(DomainError, match="ANTHROPIC_API_KEY"):
        get_chat_model(settings)


def test_get_chat_model_selects_anthropic_when_configured():
    settings = Settings(agent_provider="anthropic", anthropic_api_key="sk-ant-test")
    model = get_chat_model(settings)
    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-sonnet-5"


def test_get_chat_model_requires_an_api_key_for_openai():
    settings = Settings(agent_provider="openai", openai_api_key=None)
    with pytest.raises(DomainError, match="OPENAI_API_KEY"):
        get_chat_model(settings)


def test_get_chat_model_selects_openai_when_configured():
    settings = Settings(agent_provider="openai", openai_api_key="sk-test")
    model = get_chat_model(settings)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o-mini"


def test_get_chat_model_rejects_unknown_providers():
    settings = Settings(agent_provider="not-a-real-provider")
    with pytest.raises(DomainError, match="Unknown agent provider"):
        get_chat_model(settings)
