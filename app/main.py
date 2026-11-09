import re
from collections.abc import Iterator
from dataclasses import dataclass
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
from app.search import MIN_RERANKER_SCORE, Passage, PolicySearch

settings = load_settings()
prompt = load_prompt(settings.prompt_name)
rewrite_prompt = load_prompt("search-query-v1")

# No API keys: the app gets short-lived Entra ID tokens for its managed identity
# (or for your `az login` / `azd auth login` account when running locally).
credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
client = OpenAI(base_url=f"{settings.openai_endpoint}/openai/v1/", api_key=token_provider)
sessions = SessionStore(settings.cosmos_endpoint, credential)
policies = PolicySearch(settings.search_endpoint, credential, client, settings.embedding_deployment)

app = FastAPI(title="Contoso IT Helpdesk")


class ChatRequest(BaseModel):
    question: str
    session_id: UUID | None = Field(
        default=None, description="Omit to start a conversation; send the returned one back to continue it."
    )


# The shape the model must return. The service sends this schema, field descriptions included,
# to the model, so they're part of the prompt. (A docstring would be sent too, hence a comment.)
class HelpdeskAnswer(BaseModel):
    answer: str = Field(
        description="The reply to show the user, in plain text, citing policy excerpts inline like [1]."
    )
    category: Literal["hardware", "software", "account_access", "network", "other"]
    needs_human: bool = Field(
        description="True if a support engineer has to act, e.g. faulty hardware or a locked account."
    )


class Citation(BaseModel):
    number: int  # the [n] marker in the answer
    policy_id: str
    title: str
    section: str


class ChatResponse(HelpdeskAnswer):
    prompt: str  # which prompt file produced this answer
    session_id: UUID
    history_sent: int  # earlier messages the model saw; older ones were trimmed to fit the token budget
    search_query: str  # what was searched for: the question, rewritten to stand alone if it was a follow-up
    citations: list[Citation]


@dataclass(frozen=True)
class Turn:
    session_id: str
    history: list[dict]
    search_query: str
    passages: list[Passage]


def standalone_query(question: str, history: list[dict]) -> str:
    """"What about on a Mac?" finds nothing on its own. Rewrite follow-ups using the conversation."""
    if not history:
        return question  # a first question has nothing to resolve: skip the extra model call
    completion = client.chat.completions.create(
        model=settings.openai_deployment,
        messages=[{"role": "system", "content": rewrite_prompt.text}, *history, {"role": "user", "content": question}],
        reasoning_effort="minimal",
    )
    return (completion.choices[0].message.content or "").strip() or question


def start_turn(request: ChatRequest) -> Turn:
    """Loads the history that fits the token budget and finds the policy passages for the question."""
    session_id = str(request.session_id or uuid4())
    history = trim_to_budget(sessions.history(session_id), settings.history_token_budget)
    query = standalone_query(request.question, history)
    return Turn(session_id, history, query, policies.find(query))


def messages_for(question: str, turn: Turn) -> list[dict]:
    excerpts = "\n\n".join(
        f"[{n}] {p.title} ({p.policy_id}), {p.section}:\n{p.content}" for n, p in enumerate(turn.passages, 1)
    )
    return [
        {"role": "system", "content": prompt.text},
        *turn.history,
        {"role": "system", "content": f"Contoso IT policy excerpts for this question:\n\n{excerpts or '(none found)'}"},
        {"role": "user", "content": question},
    ]


def citations(answer: str, passages: list[Passage]) -> list[Citation]:
    """The passages the answer actually cites, by number. Made-up numbers are dropped."""
    cited = []
    for n in sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)}):
        if 1 <= n <= len(passages):
            p = passages[n - 1]
            cited.append(Citation(number=n, policy_id=p.policy_id, title=p.title, section=p.section))
    return cited


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """For other systems: a typed answer they can route on, guaranteed to match the schema."""
    turn = start_turn(request)
    completion = client.chat.completions.parse(
        model=settings.openai_deployment,
        messages=messages_for(request.question, turn),
        response_format=HelpdeskAnswer,  # strict JSON schema, enforced by the service
        reasoning_effort="low",  # enough thought to get category and needs_human right
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:  # the model refused, or ran out of tokens mid-JSON
        raise HTTPException(status_code=502, detail="Model did not return a valid answer")
    sessions.append(
        turn.session_id,
        {"role": "user", "content": request.question},
        {"role": "assistant", "content": parsed.answer},
    )
    return ChatResponse(
        **parsed.model_dump(),
        prompt=prompt.name,
        session_id=turn.session_id,
        history_sent=len(turn.history),
        search_query=turn.search_query,
        citations=citations(parsed.answer, turn.passages),
    )


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """For people: plain text that appears as it's generated, then the sources it cited."""
    turn = start_turn(request)
    stream = client.chat.completions.create(
        model=settings.openai_deployment,
        messages=messages_for(request.question, turn),
        reasoning_effort="minimal",  # reasoning happens before the first token; keep it short for people
        stream=True,
    )

    def tokens() -> Iterator[str]:
        answer = []
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                answer.append(chunk.choices[0].delta.content)
                yield chunk.choices[0].delta.content
        text = "".join(answer)
        if cited := citations(text, turn.passages):
            yield "\n\nSources:\n" + "".join(f"[{c.number}] {c.title} ({c.policy_id}), {c.section}\n" for c in cited)
        # Saved only once the whole answer has been sent; a dropped connection leaves no half answer.
        sessions.append(
            turn.session_id,
            {"role": "user", "content": request.question},
            {"role": "assistant", "content": text},
        )

    headers = {"X-Session-Id": turn.session_id, "X-History-Sent": str(len(turn.history))}
    return StreamingResponse(tokens(), media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/sessions/{session_id}")
def get_session(session_id: UUID) -> dict:
    """The full stored conversation, including messages trimmed from what the model sees."""
    messages = sessions.history(str(session_id))
    if not messages:
        raise HTTPException(status_code=404, detail="No such session")
    return {"session_id": session_id, "messages": messages}


@app.get("/search")
def search(q: str) -> list[dict]:
    """What retrieval finds for a query, including passages below the score cut-off. For seeing how search behaves."""
    return [
        {"score": round(p.score, 2), "sent": p.score >= MIN_RERANKER_SCORE, "policy_id": p.policy_id, "section": p.section}
        for p in policies.find(q, min_score=0)
    ]
