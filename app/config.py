import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Everything that differs between environments comes from env vars, never from code."""

    openai_endpoint: str
    ai_gateway_endpoint: str | None
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
    session_token_budget: int
    turn_timeout_seconds: float


def load_settings() -> Settings:
    return Settings(
        openai_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/"),
        # Model calls go through the AI gateway when there is one (v11); otherwise straight to Azure OpenAI.
        ai_gateway_endpoint=(os.environ.get("AI_GATEWAY_ENDPOINT") or "").rstrip("/") or None,
        openai_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        prompt_name=os.environ.get("PROMPT_NAME", "helpdesk-v8"),
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
        # Tokens one conversation may use in total, every model call counted. A conversation over it is closed.
        session_token_budget=int(os.environ.get("SESSION_TOKEN_BUDGET", "100000")),
        # Longest a question may take, every model call and tool included. Users give up long before.
        turn_timeout_seconds=float(os.environ.get("TURN_TIMEOUT_SECONDS", "60")),
    )
