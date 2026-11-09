import asyncio
import dataclasses
import logging
import re
from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID, uuid4

from agent_framework import AgentResponse, AgentSession, Content, Message
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, Field

from app.agent import Toolbox, build_agent
from app.auth import User, current_user
from app.backend import ServiceDesk
from app.config import load_settings
from app.memory import NotYours, SessionStore, trim_to_budget
from app.prompts import load_prompt
from app.safety import ContentSafety
from app.search import MIN_RERANKER_SCORE, Passage, PolicySearch
from app.telemetry import annotate, current_trace_id
from app import telemetry

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
telemetry.setup(settings.appinsights_connection_string, credential)  # before FastAPI(): requests get traced


async def async_token_provider() -> str:  # the async OpenAI client needs an async token function
    return await asyncio.to_thread(token_provider)


openai_base_url = f"{settings.openai_endpoint}/openai/v1/"
sessions = SessionStore(settings.cosmos_endpoint, credential)
desk = ServiceDesk(settings.cosmos_endpoint, credential)
policies = PolicySearch(
    settings.search_endpoint, credential, OpenAI(base_url=openai_base_url, api_key=token_provider),
    settings.embedding_deployment,
)
safety = ContentSafety(settings.content_safety_endpoint, async_token_provider, enabled=settings.safety_checks)
agent = build_agent(
    AsyncOpenAI(base_url=openai_base_url, api_key=async_token_provider), settings.openai_deployment, prompt.text
)

app = FastAPI(title="Contoso IT Helpdesk")


@app.middleware("http")
async def remember_request_span(request, call_next):
    telemetry.request_span.set(trace.get_current_span())  # runs inside the request's span
    return await call_next(request)


class ChatRequest(BaseModel):
    question: str
    session_id: UUID | None = Field(
        default=None, description="Omit to start a conversation; send the returned one back to continue it."
    )


class ApprovalRequest(BaseModel):
    session_id: UUID
    decisions: dict[str, bool] = Field(description="For each pending action's approval_id: true to approve, false to reject.")


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


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int  # includes reasoning tokens: they're billed as output
    estimated_cost_usd: float  # chat model only, at the configured prices; embeddings and Content Safety not included


class ToolCall(BaseModel):
    name: str
    arguments: dict
    withheld: bool = False  # the result contained a prompt injection, so the model didn't see it


class ChatResponse(HelpdeskAnswer):
    status: Literal["answered"] = "answered"
    prompt: str  # which prompt file produced this answer
    session_id: UUID
    history_sent: int  # earlier messages the model saw; older ones were trimmed to fit the token budget
    tool_calls: list[ToolCall]  # what the model looked up or did to answer, in order
    citations: list[Citation]
    usage: Usage | None
    trace_id: str  # find this request in Application Insights


class PendingAction(BaseModel):
    approval_id: str
    tool: str
    arguments: dict  # exactly what will run if approved
    warnings: list[str] = []  # sentences the agent wrote that nothing the user said or a tool returned supports


class ApprovalRequired(BaseModel):
    status: Literal["approval_required"] = "approval_required"
    session_id: UUID
    actions: list[PendingAction]  # approve or reject each with POST /chat/approve
    tool_calls: list[ToolCall]  # what already ran: lookups don't need approval
    usage: Usage | None
    trace_id: str


def load_session(session_id: UUID | None, user: User) -> dict:
    try:
        return sessions.load(str(session_id or uuid4()), user.id)
    except NotYours:
        raise HTTPException(404, "No such session") from None  # someone else's session looks like no session


def start_turn(doc: dict, question: str, user: User) -> list[Message]:
    """Who's asking, the trimmed history and the new question: the conversation sent to the model."""
    if doc.get("pending"):
        raise HTTPException(409, "An action is waiting for approval: approve or reject it with POST /chat/approve")
    history = trim_to_budget(doc["messages"], settings.history_token_budget)
    who = (f"Signed-in user: {user.name}. Their account: {user.username}. "
           f"Service Desk agent: {'yes' if user.is_agent else 'no'}.")
    return [Message("system", [who]), *[Message(m["role"], [m["content"]]) for m in history], Message("user", [question])]


def citations(answer: str, passages: list[Passage]) -> list[Citation]:
    """The passages the answer actually cites, by number. Made-up numbers are dropped."""
    cited = []
    for n in sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)}):
        if 1 <= n <= len(passages):
            p = passages[n - 1]
            cited.append(Citation(number=n, policy_id=p.policy_id, title=p.title, section=p.section))
    return cited


def usage_of(response: AgentResponse) -> Usage | None:
    """Tokens for the whole turn (every model call in the loop), and what they cost."""
    details = response.usage_details or {}
    if details.get("input_token_count") is None:
        return None
    tokens_in, tokens_out = details["input_token_count"], details.get("output_token_count") or 0
    cost = (tokens_in * settings.price_input_per_million + tokens_out * settings.price_output_per_million) / 1e6
    return Usage(input_tokens=tokens_in, output_tokens=tokens_out, estimated_cost_usd=round(cost, 6))


def record_turn(user: User, session_id: str, status: str, usage: Usage | None, toolbox: Toolbox) -> None:
    """Puts what matters about this request on its span, so it can be queried: tokens, cost, outcome."""
    annotate(
        user_id=user.id, session_id=session_id, status=status, tool_calls=len(toolbox.calls),
        withheld=sum(1 for c in toolbox.calls if c.get("withheld")),
        input_tokens=usage.input_tokens if usage else None, output_tokens=usage.output_tokens if usage else None,
        cost_usd=usage.estimated_cost_usd if usage else None,
    )


async def check_question(question: str) -> None:
    if await safety.is_prompt_attack(question):
        annotate(blocked="prompt_attack")
        logging.getLogger("agent").warning("safety: blocked a prompt attack in the user's message")
        raise HTTPException(400, "Blocked by Prompt Shields: this looks like an attempt to override the assistant's instructions")


async def pending_actions(response: AgentResponse, user: User, said: list[str], tool_results: list[str]) -> list[PendingAction]:
    """The actions waiting for approval, with warnings for the person approving them:
    - a ticket description is checked against what the user said and the tools returned (groundedness),
      so made-up sentences are pointed out;
    - a password reset for someone else must name an account the user mentioned. If only a ticket or document
      the agent read names it, the request may have been injected (provenance)."""
    actions = []
    for r in response.user_input_requests:
        arguments = r.function_call.parse_arguments() or {}
        warnings = []
        if r.function_call.name == "create_ticket":
            warnings += [f"Not supported by anything you said: \"{s}\""
                         for s in await safety.ungrounded(arguments.get("description", ""), said + tool_results)]
        if r.function_call.name == "reset_password":
            account = arguments.get("account", "").lower()
            if account != user.username and account not in " ".join(said).lower():
                warnings.append(f"You never mentioned {account}: this request may come from content the agent "
                                "read (a ticket or document), not from you.")
        actions.append(PendingAction(approval_id=r.id, tool=r.function_call.name, arguments=arguments, warnings=warnings))
    return actions


def said_by_user(doc: dict, question: str) -> list[str]:
    return [m["content"] for m in doc["messages"] if m["role"] == "user"] + [question]


async def park_turn(doc: dict, user: User, question: str, session: AgentSession, toolbox: Toolbox,
                    response: AgentResponse) -> None:
    """The agent stopped to ask for approval. Keep the turn in Cosmos DB until the user decides, possibly
    minutes later and on another replica: the framework's session (with the pending request), the question,
    and the passages found so far (for citations)."""
    pending = {
        "question": question,
        "session": session.to_dict(),
        "requests": [r.to_dict() for r in response.user_input_requests],
        "passages": [dataclasses.asdict(p) for p in toolbox.passages],
        "results": toolbox.results,
        "history_sent": len(doc["messages"]),
    }
    await asyncio.to_thread(sessions.set_pending, doc["id"], user.id, pending)


async def finish_turn(doc: dict, user: User, question: str, answer: str) -> None:
    # Only the question and the final answer are kept. Tool results are a snapshot (a ticket's status
    # changes), so later turns look them up again instead of trusting an old copy.
    await asyncio.to_thread(sessions.append, doc["id"], user.id,
                            {"role": "user", "content": question}, {"role": "assistant", "content": answer})
    if doc.get("pending"):
        await asyncio.to_thread(sessions.set_pending, doc["id"], user.id, None)


async def answer_or_park(doc: dict, user: User, question: str, session: AgentSession, toolbox: Toolbox,
                         response: AgentResponse, history_sent: int) -> ChatResponse | ApprovalRequired:
    usage = usage_of(response)
    if response.user_input_requests:
        await park_turn(doc, user, question, session, toolbox, response)
        actions = await pending_actions(response, user, said_by_user(doc, question), toolbox.results)
        record_turn(user, doc["id"], "approval_required", usage, toolbox)
        return ApprovalRequired(session_id=doc["id"], actions=actions, tool_calls=toolbox.calls, usage=usage,
                                trace_id=current_trace_id())
    parsed = response.value
    if parsed is None:  # the model refused, or ran out of tokens mid-JSON
        raise HTTPException(status_code=502, detail="Model did not return a valid answer")
    await finish_turn(doc, user, question, parsed.answer)
    record_turn(user, doc["id"], "answered", usage, toolbox)
    return ChatResponse(
        **parsed.model_dump(),
        prompt=prompt.name,
        session_id=doc["id"],
        history_sent=history_sent,
        tool_calls=toolbox.calls,
        citations=citations(parsed.answer, toolbox.passages),
        usage=usage,
        trace_id=current_trace_id(),
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: ChatRequest, user: User = Depends(current_user)) -> ChatResponse | ApprovalRequired:
    """For other systems: a typed answer they can route on, or the actions waiting for the user's approval."""
    await check_question(request.question)
    doc = await asyncio.to_thread(load_session, request.session_id, user)  # Cosmos DB client is synchronous
    messages = start_turn(doc, request.question, user)
    session = agent.create_session()  # the framework's state for this turn; it holds pending approvals
    toolbox = Toolbox(policies, desk, safety, user)
    response = await agent.run(
        messages,
        session=session,
        tools=toolbox.tools(),
        middleware=[toolbox.record_tool_call],
        options={"response_format": HelpdeskAnswer, "reasoning_effort": "low"},
    )
    return await answer_or_park(doc, user, request.question, session, toolbox, response, len(messages) - 2)


@app.post("/chat/approve")
async def approve(request: ApprovalRequest, user: User = Depends(current_user)) -> ChatResponse | ApprovalRequired:
    """Approve or reject the actions the agent asked to take, and let it finish its answer."""
    doc = await asyncio.to_thread(load_session, request.session_id, user)
    pending = doc.get("pending")
    if not pending:
        raise HTTPException(409, "Nothing is waiting for approval in this session")
    requests = [Content.from_dict(r) for r in pending["requests"]]
    if missing := {r.id for r in requests} - request.decisions.keys():
        raise HTTPException(400, f"Decide on every pending action; missing: {sorted(missing)}")

    # The framework only accepts these responses for requests its own session issued: a made-up approval for an
    # action the agent never asked for is ignored.
    session = AgentSession.from_dict(pending["session"])
    toolbox = Toolbox(policies, desk, safety, user, passages=[Passage(**p) for p in pending["passages"]],
                      results=pending["results"])
    response = await agent.run(
        [Message("user", [r.to_function_approval_response(request.decisions[r.id]) for r in requests])],
        session=session,
        tools=toolbox.tools(),
        middleware=[toolbox.record_tool_call],
        options={"response_format": HelpdeskAnswer, "reasoning_effort": "low"},
    )
    return await answer_or_park(doc, user, pending["question"], session, toolbox, response, pending["history_sent"])


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, user: User = Depends(current_user)) -> StreamingResponse:
    """For people: plain text that appears as it's generated, then the sources it cited."""
    await check_question(request.question)
    doc = await asyncio.to_thread(load_session, request.session_id, user)
    messages = start_turn(doc, request.question, user)
    session = agent.create_session()
    toolbox = Toolbox(policies, desk, safety, user)
    stream = agent.run(
        messages,
        stream=True,
        session=session,
        tools=toolbox.tools(),
        middleware=[toolbox.record_tool_call],
        options={"reasoning_effort": "minimal"},  # reasoning happens before the first token; keep it short for people
    )

    async def tokens() -> AsyncIterator[str]:
        async for update in stream:
            if update.text:
                yield update.text
        response = await stream.get_final_response()
        record_turn(user, doc["id"], "approval_required" if response.user_input_requests else "answered",
                    usage_of(response), toolbox)
        if response.user_input_requests:
            await park_turn(doc, user, request.question, session, toolbox, response)
            actions = await pending_actions(response, user, said_by_user(doc, request.question), toolbox.results)
            yield "\n\nWaiting for your approval (POST /chat/approve):\n" + "".join(
                f"- {a.approval_id}: {a.tool}({a.arguments})\n" + "".join(f"  ! {w}\n" for w in a.warnings)
                for a in actions
            )
            return
        if cited := citations(response.text, toolbox.passages):
            yield "\n\nSources:\n" + "".join(f"[{c.number}] {c.title} ({c.policy_id}), {c.section}\n" for c in cited)
        # Saved only once the whole answer has been sent; a dropped connection leaves no half answer.
        await finish_turn(doc, user, request.question, response.text)

    headers = {"X-Session-Id": doc["id"], "X-History-Sent": str(len(messages) - 2), "X-Trace-Id": current_trace_id()}
    return StreamingResponse(tokens(), media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/sessions/{session_id}")
def get_session(session_id: UUID, user: User = Depends(current_user)) -> dict:
    """Your stored conversation, including messages trimmed from what the model sees."""
    doc = load_session(session_id, user)
    if not doc["messages"] and not doc.get("pending"):
        raise HTTPException(status_code=404, detail="No such session")
    waiting = [{"tool": r["function_call"]["name"], "arguments": r["function_call"]["arguments"]}
               for r in (doc.get("pending") or {}).get("requests", [])]
    return {"session_id": session_id, "messages": doc["messages"], "waiting_for_approval": waiting}


@app.get("/search")
def search(q: str, user: User = Depends(current_user)) -> list[dict]:
    """What retrieval finds for a query, including passages below the score cut-off. For seeing how search behaves."""
    return [
        {"score": round(p.score, 2), "sent": p.score >= MIN_RERANKER_SCORE, "policy_id": p.policy_id, "section": p.section}
        for p in policies.find(q, min_score=0)
    ]
