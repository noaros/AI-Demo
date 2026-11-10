"""Sends many questions at once, as one user, and shows what happens to each: for the v11 demo.

    python -m scripts.burst 8                      # against SERVICE_API_URI, with the azd values exported
    python -m scripts.burst 8 http://localhost:8000
"""

import asyncio
import os
import subprocess
import sys
import time

import httpx

QUESTIONS = [
    "How long can contractors keep VPN access?",
    "What's the password policy?",
    "Can I use a personal USB stick at work?",
    "My laptop CTSO-10442 has a cracked screen. Is it covered?",
    "How do I report a phishing email?",
    "Which video call tools are approved?",
]


async def ask(client: httpx.AsyncClient, n: int, started: float) -> None:
    response = await client.post("/chat", json={"question": QUESTIONS[n % len(QUESTIONS)]})
    took = time.monotonic() - started
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    detail = (f"{body['usage']['input_tokens'] + body['usage']['output_tokens']} tokens" if response.is_success
              else body.get("detail", ""))
    retry = f", Retry-After {response.headers['retry-after']}s" if "retry-after" in response.headers else ""
    print(f"#{n + 1:<2} {response.status_code}  {took:5.1f}s  {detail}{retry}  trace {body.get('trace_id', '-')}")


async def main(count: int, url: str) -> None:
    scope = f"api://{os.environ['AZURE_API_CLIENT_ID']}/Helpdesk.Chat"
    token = subprocess.check_output(["az", "account", "get-access-token", "--scope", scope,
                                     "--query", "accessToken", "-o", "tsv"], text=True).strip()
    async with httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=120) as client:
        started = time.monotonic()
        await asyncio.gather(*(ask(client, n, started) for n in range(count)))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8,
                     sys.argv[2] if len(sys.argv) > 2 else os.environ["SERVICE_API_URI"]))
