"""The helpdesk agent, built on Microsoft Agent Framework.

v5's hand-written loop (call the model, run the tools it asks for, append the results, repeat) is now the
framework's job. What's left for us: the tools, the instructions, and middleware around tool calls.
"""

import logging
from typing import Annotated, Any, Literal

from agent_framework import Agent, FunctionInvocationContext, FunctionTool, tool
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.auth import User
from app.backend import ServiceDesk
from app.search import Passage, PolicySearch

log = logging.getLogger("agent")

# Model calls per question. When it's reached, the framework stops offering tools and makes the model answer.
MAX_STEPS = 5


def build_agent(openai: AsyncOpenAI, deployment: str, instructions: str) -> Agent:
    client = OpenAIChatCompletionClient(
        model=deployment,
        async_client=openai,
        function_invocation_configuration={"max_iterations": MAX_STEPS},
    )
    return Agent(client, instructions, name="contoso-helpdesk")


class Toolbox:
    """The tools for one question, acting as one user. The framework builds each tool's schema from the method's
    signature: the type hints and Field descriptions, just as the Pydantic models did in v5.

    `user` is never a tool argument: the model can't choose who it acts as. It comes from the verified token."""

    def __init__(self, policies: PolicySearch, desk: ServiceDesk, user: User, passages: list[Passage] | None = None):
        self.policies = policies
        self.desk = desk
        self.user = user
        self.passages: list[Passage] = passages or []  # every excerpt found this turn; [n] in the answer is passages[n-1]
        self.calls: list[dict] = []  # what the model asked for, returned to the caller for visibility

    # ---------- read ----------

    def search_policies(
        self, query: Annotated[str, Field(description="A standalone search query, e.g. 'contractor VPN access duration'.")]
    ) -> dict:
        found = self.policies.find(query)
        first = len(self.passages) + 1  # numbering continues across searches in the same turn
        self.passages += found
        if not found:
            return {"excerpts": [], "note": "No relevant policy found."}
        return {
            "excerpts": [
                {"number": first + i, "source": f"{p.title} ({p.policy_id}), {p.section}", "text": p.content}
                for i, p in enumerate(found)
            ]
        }

    def get_ticket(self, ticket_id: Annotated[str, Field(description="Ticket number, like INC0010001.")]) -> dict:
        return self.desk.get_ticket(ticket_id, self.user) or {"error": f"No ticket {ticket_id} found that you can see."}

    def check_laptop_warranty(
        self, asset_tag: Annotated[str, Field(description="Asset tag from the sticker on the laptop, like CTSO-10442.")]
    ) -> dict:
        return self.desk.get_laptop(asset_tag) or {"error": f"No laptop with asset tag {asset_tag} found."}

    # ---------- write: these change things ----------

    def create_ticket(
        self,
        title: Annotated[str, Field(description="One line summary of the problem or request.")],
        description: Annotated[str, Field(description="What happened, what the user tried, error messages, asset tag.")],
        priority: Annotated[Literal[1, 2, 3, 4], Field(description="1: a team or critical system is down. 2: one person "
                                                       "can't work. 3: a problem with a workaround. 4: a request.")],
    ) -> dict:
        return self.desk.create_ticket(title, description, priority, self.user)

    def reset_password(
        self, account: Annotated[str, Field(description="The Contoso account's email address, like alex.kim@contoso.com.")]
    ) -> dict:
        try:
            return self.desk.reset_password(account, self.user)
        except (PermissionError, LookupError) as e:
            return {"error": str(e)}

    # ---------- wiring ----------

    def tools(self) -> list[FunctionTool]:
        # The descriptions are how the model decides which tool to call, and when: they're part of the prompt.
        return [
            tool(self.search_policies, name="search_policies",
                 description="Search Contoso's IT policies. Use it for anything specific to Contoso: rules, limits, "
                 "contacts, approved tools and processes. Returns numbered excerpts to cite."),
            tool(self.get_ticket, name="get_ticket",
                 description="Look up a Service Desk ticket: status, priority, owner, updates."),
            tool(self.check_laptop_warranty, name="check_laptop_warranty",
                 description="Look up a Contoso laptop by asset tag: model, purchase date, warranty end, refresh date."),
            # These two change things, so a person must approve each call: the framework stops before running
            # them and returns an approval request instead. See /chat/approve.
            tool(self.create_ticket, name="create_ticket", approval_mode="always_require",
                 description="Open a new Service Desk ticket in the user's name. Returns the new ticket number."),
            tool(self.reset_password, name="reset_password", approval_mode="always_require",
                 description="Reset a Contoso account's password. A temporary password is sent by text message "
                 "to the phone number on file."),
        ]

    async def record_tool_call(self, context: FunctionInvocationContext, call_next) -> None:
        """Function middleware: the framework runs this around every tool call."""
        arguments: Any = context.arguments
        if isinstance(arguments, BaseModel):
            arguments = arguments.model_dump()
        self.calls.append({"name": context.function.name, "arguments": dict(arguments)})
        log.info("tool call: %s(%s)", context.function.name, arguments)
        await call_next()
