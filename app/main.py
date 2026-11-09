from collections.abc import Iterator
from typing import Literal

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import load_settings
from app.prompts import load_prompt

settings = load_settings()
prompt = load_prompt(settings.prompt_name)

# No API key: the app gets short-lived Entra ID tokens for its managed identity
# (or for your `az login` / `azd auth login` account when running locally).
token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)
client = OpenAI(base_url=f"{settings.openai_endpoint}/openai/v1/", api_key=token_provider)

app = FastAPI(title="Contoso IT Helpdesk")


class ChatRequest(BaseModel):
    question: str


# The shape the model must return. The service sends this schema, field descriptions included,
# to the model, so they're part of the prompt. (A docstring would be sent too, hence a comment.)
class HelpdeskAnswer(BaseModel):
    answer: str = Field(description="The reply to show the user, in plain text.")
    category: Literal["hardware", "software", "account_access", "network", "other"]
    needs_human: bool = Field(
        description="True if a support engineer has to act, e.g. faulty hardware or a locked account."
    )


class ChatResponse(HelpdeskAnswer):
    prompt: str  # which prompt file produced this answer


def messages_for(question: str) -> list[dict]:
    return [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": question},
    ]


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """For other systems: a typed answer they can route on, guaranteed to match the schema."""
    completion = client.chat.completions.parse(
        model=settings.openai_deployment,
        messages=messages_for(request.question),
        response_format=HelpdeskAnswer,  # strict JSON schema, enforced by the service
        reasoning_effort="low",  # enough thought to get category and needs_human right
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:  # the model refused, or ran out of tokens mid-JSON
        raise HTTPException(status_code=502, detail="Model did not return a valid answer")
    return ChatResponse(**parsed.model_dump(), prompt=prompt.name)


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """For people: plain text that appears as it's generated."""
    stream = client.chat.completions.create(
        model=settings.openai_deployment,
        messages=messages_for(request.question),
        reasoning_effort="minimal",  # reasoning happens before the first token; keep it short for people
        stream=True,
    )

    def tokens() -> Iterator[str]:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return StreamingResponse(tokens(), media_type="text/plain; charset=utf-8")
