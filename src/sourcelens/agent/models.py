from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings


def get_chat_model(settings: Settings) -> BaseChatModel:
    if settings.agent_provider == "openai":
        if not settings.openai_api_key:
            raise DomainError("agent_unconfigured", "OPENAI_API_KEY is not set", 500)
        return ChatOpenAI(model=settings.agent_model, api_key=SecretStr(settings.openai_api_key))
    if settings.agent_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise DomainError("agent_unconfigured", "ANTHROPIC_API_KEY is not set", 500)
        return ChatAnthropic(
            model_name=settings.agent_model,
            api_key=SecretStr(settings.anthropic_api_key),
            timeout=60,
            stop=None,
        )
    raise DomainError(
        "agent_provider_unknown", f"Unknown agent provider {settings.agent_provider!r}", 500
    )
