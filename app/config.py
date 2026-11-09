import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Everything that differs between environments comes from env vars, never from code."""

    openai_endpoint: str
    openai_deployment: str
    prompt_name: str
    cosmos_endpoint: str
    embedding_deployment: str
    search_endpoint: str
    auth_tenant_id: str
    auth_client_id: str
    content_safety_endpoint: str
    safety_checks: bool
    appinsights_connection_string: str | None
    price_input_per_million: float
    price_output_per_million: float
    history_token_budget: int


def load_settings() -> Settings:
    return Settings(
        openai_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/"),
        openai_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        prompt_name=os.environ.get("PROMPT_NAME", "helpdesk-v6"),
        cosmos_endpoint=os.environ["AZURE_COSMOS_ENDPOINT"],
        embedding_deployment=os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"],
        search_endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        auth_tenant_id=os.environ["AUTH_TENANT_ID"],
        auth_client_id=os.environ["AUTH_CLIENT_ID"],
        content_safety_endpoint=os.environ["CONTENT_SAFETY_ENDPOINT"],
        safety_checks=os.environ.get("SAFETY_CHECKS", "on") != "off",  # "off" only to demo the attacks
        appinsights_connection_string=os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"),
        # US$ per million tokens for the chat model, for cost estimates. Defaults: gpt-5-mini, Global Standard.
        # Prices change: check https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/
        price_input_per_million=float(os.environ.get("PRICE_INPUT_PER_MILLION", "0.25")),
        price_output_per_million=float(os.environ.get("PRICE_OUTPUT_PER_MILLION", "2.00")),
        history_token_budget=int(os.environ.get("HISTORY_TOKEN_BUDGET", "2000")),
    )
