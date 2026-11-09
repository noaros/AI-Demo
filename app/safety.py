"""Azure AI Content Safety checks, called over REST (the Python SDK doesn't cover these yet).

- Prompt Shields, for the user's message: is it an attempt to override the assistant's instructions (a jailbreak)?
- Prompt Shields, for tool results: does content someone else wrote (a ticket, a document) contain instructions
  aimed at the assistant? That's *indirect* prompt injection: the attacker never talks to the agent.
- Groundedness detection: which parts of a text aren't supported by the given sources?

If Content Safety can't be reached, the checks raise: the request fails rather than going ahead unchecked.
"""

import logging
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger("agent")

SHIELD_API = "contentsafety/text:shieldPrompt?api-version=2024-09-01"
GROUNDEDNESS_API = "contentsafety/text:detectGroundedness?api-version=2024-09-15-preview"
MAX_DOCUMENT_CHARS = 10_000  # Prompt Shields limit for documents


class ContentSafety:
    def __init__(self, endpoint: str, token: Callable[[], Awaitable[str]], enabled: bool = True):
        self.endpoint = endpoint.rstrip("/") + "/"
        self.token = token
        self.enabled = enabled  # off only to demonstrate what happens without these checks
        self.http = httpx.AsyncClient(timeout=10)

    async def _post(self, path: str, body: dict) -> dict:
        response = await self.http.post(
            self.endpoint + path, json=body, headers={"Authorization": f"Bearer {await self.token()}"}
        )
        response.raise_for_status()
        return response.json()

    async def is_prompt_attack(self, user_message: str) -> bool:
        if not self.enabled:
            return False
        result = await self._post(SHIELD_API, {"userPrompt": user_message, "documents": []})
        return result["userPromptAnalysis"]["attackDetected"]

    async def has_injection(self, document: str) -> bool:
        if not self.enabled:
            return False
        result = await self._post(SHIELD_API, {"userPrompt": "", "documents": [document[:MAX_DOCUMENT_CHARS]]})
        return result["documentsAnalysis"][0]["attackDetected"]

    async def ungrounded(self, text: str, sources: list[str]) -> list[str]:
        """The sentences of `text` that nothing in `sources` supports."""
        if not self.enabled or not text.strip():
            return []
        result = await self._post(GROUNDEDNESS_API, {
            "domain": "Generic", "task": "Summarization", "text": text, "groundingSources": sources, "reasoning": False,
        })
        return [d["text"] for d in result.get("ungroundedDetails", [])]
