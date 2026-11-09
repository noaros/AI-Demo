# AI-Demo

An iterative demo that grows a minimal Azure OpenAI script into a production-grade AI agent, one production concern at a time.

The running example is **Contoso IT Helpdesk**, an internal assistant that answers IT policy questions and eventually takes actions (creating tickets, resetting passwords) on the user's behalf.

## How to follow along

Each iteration is a git tag. Check one out to see the code at that stage, and diff two tags to see exactly what changed:

```bash
git checkout v0
git diff v0 v1
```

Each iteration has its own write-up in [`docs/iterations/`](docs/iterations/) covering its goal, what changed, a demo script and what's still missing. This README always describes the code at the current tag.

## Getting started

**Current iteration: [v3: Memory](docs/iterations/v3.md)**

You need the [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) (`azd`) and an Azure subscription where you can create role assignments (Owner or User Access Administrator).

```bash
azd auth login
azd up          # pick an environment name, a subscription and a region (e.g. eastus2)
```

`azd up` provisions everything in `infra/`, builds the container image in Azure and deploys it. When it finishes, try it:

```bash
URL=$(azd env get-value SERVICE_API_URI)

# Typed answer for other systems: answer, category, needs_human, prompt version
curl -X POST $URL/chat -H 'Content-Type: application/json' \
  -d '{"question": "My laptop screen is cracked"}'

# Plain text for people, streamed as it's written (-N turns off curl's buffering)
curl -N -X POST $URL/chat/stream -H 'Content-Type: application/json' \
  -d '{"question": "How do I connect a second monitor?"}'

# Continue a conversation: send back the session_id from the previous answer
# (/chat/stream returns it in the X-Session-Id header)
curl -X POST $URL/chat -H 'Content-Type: application/json' \
  -d '{"question": "What about a third one?", "session_id": "<session_id>"}'
curl $URL/sessions/<session_id>    # the full stored conversation
```

Conversations are stored in Cosmos DB for 7 days after their last message. Only the newest messages that fit in `HISTORY_TOKEN_BUDGET` tokens (default 2000) are sent to the model.

The system prompt lives in [`prompts/`](prompts/). Set `PROMPT_NAME` to use a different prompt file (default `helpdesk-v1`).

To run the API locally against the same Azure OpenAI and Cosmos DB resources (it uses your `azd`/`az` login, no keys):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
azd env get-values > .env
uvicorn app.main:app --reload --env-file .env    # then open http://localhost:8000/docs
```

When you're done, remove everything:

```bash
azd down --purge
```

## Roadmap

| Tag | Iteration | Status |
|---|---|---|
| v0 | [Hello LLM](docs/iterations/v0.md) | ✅ done |
| v1 | [Real service: FastAPI, Container Apps, managed identity, `azd up`](docs/iterations/v1.md) | ✅ done |
| v2 | [Prompting done right: prompt files, structured outputs, streaming](docs/iterations/v2.md) | ✅ done |
| v3 | [Memory: conversation state in Cosmos DB, token-window trimming](docs/iterations/v3.md) | ✅ current |
| v4 | Grounding: RAG with Azure AI Search and citations | planned |
| v5 | First tools: hand-written agent loop with read-only tools | planned |
| v6 | Agent framework + write actions | planned |
| v7 | Human-in-the-loop approval + least privilege (on-behalf-of user identity) | planned |
| v8 | Safety: Prompt Shields for direct and indirect injection, groundedness | planned |
| v9 | Observability: tracing, tokens and cost per request | planned |
| v10 | Evaluations as a CI gate | planned |
| v11 | Resilience and cost control: AI gateway, failover, caching, agent step limits | planned |
| v12 | Continuous improvement: feedback loop, red teaming | planned |
