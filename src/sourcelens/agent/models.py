from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from sourcelens.api.errors import DomainError
from sourcelens.config import Settings


def get_chat_model(settings: Settings) -> BaseChatModel:
    if not settings.anthropic_api_key:
        raise DomainError("agent_unconfigured", "ANTHROPIC_API_KEY is not set", 500)
    return ChatAnthropic(
        model_name=settings.agent_model,
        api_key=SecretStr(settings.anthropic_api_key),
        timeout=60,
        stop=None,
    )
