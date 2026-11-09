import logging
import re
from collections.abc import Iterator
from typing import Literal
from uuid import UUID, uuid4

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from app import tools
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
client = OpenAI(base_url=f"{settings.openai_endpoint}/openai/v1/", api_key=token_provider)
sessions = SessionStore(settings.cosmos_endpoint, credential)
policies = PolicySearch(settings.search_endpoint, credential, client, settings.embedding_deployment)

# A model can keep asking for tools. Each step is a model call you pay for and wait for, so cap them.
MAX_STEPS = 5

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
    tool_calls: list[ToolCall]  # what the model looked up to answer, in order
    citations: list[Citation]


def start_turn(request: ChatRequest) -> tuple[str, list[dict]]:
    """Returns the session id and the start of the conversation sent to the model."""
    session_id = str(request.session_id or uuid4())
    history = trim_to_budget(sessions.history(session_id), settings.history_token_budget)
    messages = [
        {"role": "system", "content": prompt.text},
        *history,
        {"role": "user", "content": request.question},
    ]
    return session_id, messages


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
def chat(request: ChatRequest) -> ChatResponse:
    """For other systems: a typed answer they can route on, guaranteed to match the schema."""
    session_id, messages = start_turn(request)
    history_sent = len(messages) - 2
    toolbox = tools.Toolbox(policies)

    # The agent loop: call the model; if it asks for tools, run them, add the results and call it again.
    # It ends when the model answers instead of asking for a tool.
    for _ in range(MAX_STEPS):
        completion = client.chat.completions.parse(
            model=settings.openai_deployment,
            messages=messages,
            tools=tools.SCHEMAS,
            response_format=HelpdeskAnswer,  # the shape of the final answer, once there are no more tool calls
            reasoning_effort="low",
        )
        message = completion.choices[0].message
        if not message.tool_calls:
            break
        toolbox.answer_tool_calls(message, messages)
    else:
        raise HTTPException(status_code=502, detail=f"No answer after {MAX_STEPS} steps")

    parsed = message.parsed
    if parsed is None:  # the model refused, or ran out of tokens mid-JSON
        raise HTTPException(status_code=502, detail="Model did not return a valid answer")
    save_turn(session_id, request.question, parsed.answer)
    return ChatResponse(
        **parsed.model_dump(),
        prompt=prompt.name,
        session_id=session_id,
        history_sent=history_sent,
        tool_calls=toolbox.calls,
        citations=citations(parsed.answer, toolbox.passages),
    )


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """For people: plain text that appears as it's generated, then the sources it cited."""
    session_id, messages = start_turn(request)
    history_sent = len(messages) - 2
    toolbox = tools.Toolbox(policies)

    def tokens() -> Iterator[str]:
        # The same loop, streamed: text is passed on as it arrives, tool calls are collected by the SDK.
        for _ in range(MAX_STEPS):
            with client.chat.completions.stream(
                model=settings.openai_deployment,
                messages=messages,
                tools=tools.SCHEMAS,
                reasoning_effort="minimal",  # reasoning happens before the first token; keep it short for people
            ) as stream:
                for event in stream:
                    if event.type == "content.delta":
                        yield event.delta
                message = stream.get_final_completion().choices[0].message
            if not message.tool_calls:
                break
            toolbox.answer_tool_calls(message, messages)
        else:
            yield f"\n\n(Stopped: no answer after {MAX_STEPS} steps.)"
            return

        answer = message.content or ""
        if cited := citations(answer, toolbox.passages):
            yield "\n\nSources:\n" + "".join(f"[{c.number}] {c.title} ({c.policy_id}), {c.section}\n" for c in cited)
        # Saved only once the whole answer has been sent; a dropped connection leaves no half answer.
        save_turn(session_id, request.question, answer)

    headers = {"X-Session-Id": session_id, "X-History-Sent": str(history_sent)}
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
