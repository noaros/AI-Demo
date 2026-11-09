import json
import logging

from openai import pydantic_function_tool
from pydantic import BaseModel, Field, ValidationError

from app import backend
from app.search import Passage, PolicySearch

log = logging.getLogger("agent")


# Each tool's arguments are a Pydantic model. The SDK turns it into a strict JSON schema the model
# must follow, and the same model validates the arguments the model sends back.
class SearchPolicies(BaseModel):
    query: str = Field(description="A standalone search query, e.g. 'contractor VPN access duration'.")


class GetTicket(BaseModel):
    ticket_id: str = Field(description="Service Desk ticket number, like INC0010001.")


class CheckLaptopWarranty(BaseModel):
    asset_tag: str = Field(description="Asset tag from the sticker on the bottom of the laptop, like CTSO-10442.")


# The descriptions are how the model decides which tool to call, and when: they're part of the prompt.
SCHEMAS = [
    pydantic_function_tool(
        SearchPolicies,
        name="search_policies",
        description="Search Contoso's IT policies. Use it for anything specific to Contoso: rules, limits, "
        "contacts, approved tools and processes. Returns numbered excerpts to cite.",
    ),
    pydantic_function_tool(
        GetTicket, name="get_ticket", description="Look up a Service Desk ticket: status, priority, owner, updates."
    ),
    pydantic_function_tool(
        CheckLaptopWarranty,
        name="check_laptop_warranty",
        description="Look up a Contoso laptop by asset tag: model, purchase date, warranty end, refresh date.",
    ),
]


class Toolbox:
    """Runs the tools for one turn. Read-only: nothing here changes anything."""

    def __init__(self, policies: PolicySearch):
        self.policies = policies
        self.passages: list[Passage] = []  # every excerpt found this turn; [n] in the answer is passages[n-1]
        self.calls: list[dict] = []  # what the model asked for, returned to the caller for visibility

    def search_policies(self, args: SearchPolicies) -> dict:
        found = self.policies.find(args.query)
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

    def get_ticket(self, args: GetTicket) -> dict:
        return backend.get_ticket(args.ticket_id) or {"error": f"No ticket {args.ticket_id} found."}

    def check_laptop_warranty(self, args: CheckLaptopWarranty) -> dict:
        return backend.get_laptop(args.asset_tag) or {"error": f"No laptop with asset tag {args.asset_tag} found."}

    def run(self, name: str, arguments: str) -> str:
        """Runs one tool call. Problems go back to the model as an error it can react to, not as a crash."""
        self.calls.append({"name": name, "arguments": json.loads(arguments or "{}")})
        log.info("tool call: %s(%s)", name, arguments)
        handlers = {
            "search_policies": (SearchPolicies, self.search_policies),
            "get_ticket": (GetTicket, self.get_ticket),
            "check_laptop_warranty": (CheckLaptopWarranty, self.check_laptop_warranty),
        }
        if name not in handlers:
            return json.dumps({"error": f"Unknown tool {name}."})
        args_model, handler = handlers[name]
        try:
            return json.dumps(handler(args_model.model_validate_json(arguments)))
        except ValidationError as e:
            return json.dumps({"error": f"Invalid arguments: {e}"})

    def answer_tool_calls(self, message, messages: list[dict]) -> None:
        """Appends the model's tool-call request and one result per call: the API needs both, in this order."""
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in message.tool_calls
            ],
        })
        for call in message.tool_calls:
            result = self.run(call.function.name, call.function.arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
