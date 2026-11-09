from collections.abc import Iterator
from typing import Literal
from uuid import UUID, uuid4

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import load_settings
from app.memory import SessionStore, trim_to_budget
from app.prompts import load_prompt

settings = load_settings()
prompt = load_prompt(settings.prompt_name)

# No API keys: the app gets short-lived Entra ID tokens for its managed identity
# (or for your `az login` / `azd auth login` account when running locally).
credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
client = OpenAI(base_url=f"{settings.openai_endpoint}/openai/v1/", api_key=token_provider)
sessions = SessionStore(settings.cosmos_endpoint, credential)

app = FastAPI(title="Contoso IT Helpdesk")


class ChatRequest(BaseModel):
    question: str
    session_id: UUID | None = Field(
        default=None, description="Omit to start a conversation; send the returned one back to continue it."
    )


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
    session_id: UUID
    history_sent: int  # earlier messages the model saw; older ones were trimmed to fit the token budget


def start_turn(request: ChatRequest) -> tuple[str, list[dict]]:
    """Returns the session id and the part of its history that fits in the token budget."""
    session_id = str(request.session_id or uuid4())
    history = trim_to_budget(sessions.history(session_id), settings.history_token_budget)
    return session_id, history


def messages_for(question: str, history: list[dict]) -> list[dict]:
    return [
        {"role": "system", "content": prompt.text},
        *history,
        {"role": "user", "content": question},
    ]


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """For other systems: a typed answer they can route on, guaranteed to match the schema."""
    session_id, history = start_turn(request)
    completion = client.chat.completions.parse(
        model=settings.openai_deployment,
        messages=messages_for(request.question, history),
        response_format=HelpdeskAnswer,  # strict JSON schema, enforced by the service
        reasoning_effort="low",  # enough thought to get category and needs_human right
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:  # the model refused, or ran out of tokens mid-JSON
        raise HTTPException(status_code=502, detail="Model did not return a valid answer")
    sessions.append(
        session_id,
        {"role": "user", "content": request.question},
        {"role": "assistant", "content": parsed.answer},
    )
    return ChatResponse(
        **parsed.model_dump(), prompt=prompt.name, session_id=session_id, history_sent=len(history)
    )


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """For people: plain text that appears as it's generated. The session id comes back in a header."""
    session_id, history = start_turn(request)
    stream = client.chat.completions.create(
        model=settings.openai_deployment,
        messages=messages_for(request.question, history),
        reasoning_effort="minimal",  # reasoning happens before the first token; keep it short for people
        stream=True,
    )

    def tokens() -> Iterator[str]:
        answer = []
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                answer.append(chunk.choices[0].delta.content)
                yield chunk.choices[0].delta.content
        # Saved only once the whole answer has been sent; a dropped connection leaves no half answer.
        sessions.append(
            session_id,
            {"role": "user", "content": request.question},
            {"role": "assistant", "content": "".join(answer)},
        )

    headers = {"X-Session-Id": session_id, "X-History-Sent": str(len(history))}
    return StreamingResponse(tokens(), media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/sessions/{session_id}")
def get_session(session_id: UUID) -> dict:
    """The full stored conversation, including messages trimmed from what the model sees."""
    messages = sessions.history(str(session_id))
    if not messages:
        raise HTTPException(status_code=404, detail="No such session")
    return {"session_id": session_id, "messages": messages}
