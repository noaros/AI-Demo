import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID, uuid4

from agent_framework import Message
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, Field

from app.agent import Toolbox, build_agent
from app.backend import ServiceDesk
from app.config import load_settings
from app.memory import SessionStore, trim_to_budget
from app.prompts import load_prompt
from app.search import MIN_RERANKER_SCORE, Passage, PolicySearch

# Tool calls show in the container logs. Only our "agent" logger is turned up: at INFO, the Azure SDKs
# would log the headers of every Cosmos DB and AI Search request.
logging.basicConfig(format="%(name)s: %(message)s")
logging.getLogger("agent").setLevel(logging.INFO)

settings = load_settings()
prompt = load_prompt(settings.prompt_name)

# No API keys: the app gets short-lived Entra ID tokens for its managed identity
# (or for your `az login` / `azd auth login` account when running locally).
credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")


async def async_token_provider() -> str:  # the async OpenAI client needs an async token function
    return await asyncio.to_thread(token_provider)


openai_base_url = f"{settings.openai_endpoint}/openai/v1/"
sessions = SessionStore(settings.cosmos_endpoint, credential)
desk = ServiceDesk(settings.cosmos_endpoint, credential)
policies = PolicySearch(
    settings.search_endpoint, credential, OpenAI(base_url=openai_base_url, api_key=token_provider),
    settings.embedding_deployment,
)
agent = build_agent(
    AsyncOpenAI(base_url=openai_base_url, api_key=async_token_provider), settings.openai_deployment, prompt.text
)

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


class ToolCall(BaseModel):
    name: str
    arguments: dict


class ChatResponse(HelpdeskAnswer):
    prompt: str  # which prompt file produced this answer
    session_id: UUID
    history_sent: int  # earlier messages the model saw; older ones were trimmed to fit the token budget
    tool_calls: list[ToolCall]  # what the model looked up or did to answer, in order
    citations: list[Citation]


def load_turn(request: ChatRequest) -> tuple[str, list[Message]]:
    """Returns the session id and the conversation to send: trimmed history plus the new question."""
    session_id = str(request.session_id or uuid4())
    history = trim_to_budget(sessions.history(session_id), settings.history_token_budget)
    messages = [Message(m["role"], [m["content"]]) for m in history]
    return session_id, [*messages, Message("user", [request.question])]


def save_turn(session_id: str, question: str, answer: str) -> None:
    # Only the question and the final answer are kept. Tool results are a snapshot (a ticket's status
    # changes), so later turns look them up again instead of trusting an old copy.
    sessions.append(session_id, {"role": "user", "content": question}, {"role": "assistant", "content": answer})


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
async def chat(request: ChatRequest) -> ChatResponse:
    """For other systems: a typed answer they can route on, guaranteed to match the schema."""
    session_id, messages = await asyncio.to_thread(load_turn, request)  # Cosmos DB client is synchronous
    toolbox = Toolbox(policies, desk)
    response = await agent.run(
        messages,
        tools=toolbox.tools(),
        middleware=[toolbox.record_tool_call],
        options={"response_format": HelpdeskAnswer, "reasoning_effort": "low"},
    )
    parsed = response.value
    if parsed is None:  # the model refused, or ran out of tokens mid-JSON
        raise HTTPException(status_code=502, detail="Model did not return a valid answer")
    await asyncio.to_thread(save_turn, session_id, request.question, parsed.answer)
    return ChatResponse(
        **parsed.model_dump(),
        prompt=prompt.name,
        session_id=session_id,
        history_sent=len(messages) - 1,
        tool_calls=toolbox.calls,
        citations=citations(parsed.answer, toolbox.passages),
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """For people: plain text that appears as it's generated, then the sources it cited."""
    session_id, messages = await asyncio.to_thread(load_turn, request)
    toolbox = Toolbox(policies, desk)
    stream = agent.run(
        messages,
        stream=True,
        tools=toolbox.tools(),
        middleware=[toolbox.record_tool_call],
        options={"reasoning_effort": "minimal"},  # reasoning happens before the first token; keep it short for people
    )

    async def tokens() -> AsyncIterator[str]:
        async for update in stream:
            if update.text:
                yield update.text
        answer = (await stream.get_final_response()).text
        if cited := citations(answer, toolbox.passages):
            yield "\n\nSources:\n" + "".join(f"[{c.number}] {c.title} ({c.policy_id}), {c.section}\n" for c in cited)
        # Saved only once the whole answer has been sent; a dropped connection leaves no half answer.
        await asyncio.to_thread(save_turn, session_id, request.question, answer)

    headers = {"X-Session-Id": session_id, "X-History-Sent": str(len(messages) - 1)}
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
