import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Everything that differs between environments comes from env vars, never from code."""

    openai_endpoint: str
    openai_deployment: str
    prompt_name: str
    cosmos_endpoint: str
    history_token_budget: int


def load_settings() -> Settings:
    return Settings(
        openai_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/"),
        openai_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        prompt_name=os.environ.get("PROMPT_NAME", "helpdesk-v1"),
        cosmos_endpoint=os.environ["AZURE_COSMOS_ENDPOINT"],
        history_token_budget=int(os.environ.get("HISTORY_TOKEN_BUDGET", "2000")),
    )
