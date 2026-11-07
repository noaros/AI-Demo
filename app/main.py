from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

from app.config import load_settings

settings = load_settings()

# No API key: the app gets short-lived Entra ID tokens for its managed identity
# (or for your `az login` / `azd auth login` account when running locally).
token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)
client = OpenAI(base_url=f"{settings.openai_endpoint}/openai/v1/", api_key=token_provider)

app = FastAPI(title="Contoso IT Helpdesk")


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    response = client.chat.completions.create(
        model=settings.openai_deployment,
        messages=[
            {"role": "system", "content": "You are Contoso's IT helpdesk assistant."},
            {"role": "user", "content": request.question},
        ],
    )
    return ChatResponse(answer=response.choices[0].message.content)
