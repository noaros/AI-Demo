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

**Current iteration: [v10: Evaluations as a CI gate](docs/iterations/v10.md)**

You need the [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) (`azd`), the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (`az`), Python 3, an Azure subscription where you can create role assignments (Owner or User Access Administrator), and permission to create app registrations in its Entra ID tenant.

```bash
az login
azd auth login
azd up          # pick an environment name, a subscription and a region (e.g. eastus2)
```

`azd up` creates an Entra ID app registration for the API, provisions everything in `infra/`, fills the search index from [`data/policies/`](data/policies/), builds the container image in Azure and deploys it. If it fails with `InsufficientResourcesAvailable` on the search service, run `azd env set AZURE_SEARCH_LOCATION eastus` (or another region) and `azd up` again.

Every endpoint except `/healthz` needs a signed-in user's token. Get one with Azure CLI, then try it:

```bash
URL=$(azd env get-value SERVICE_API_URI)
CID=$(azd env get-value AZURE_API_CLIENT_ID)
TOKEN=$(az account get-access-token --scope "api://$CID/Helpdesk.Chat" --query accessToken -o tsv)
AUTH="Authorization: Bearer $TOKEN"

# Typed answer for other systems: answer, the tools it used, citations, category, needs_human, ...
curl -X POST $URL/chat -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"question": "My laptop CTSO-10442 has a cracked screen. Is it covered?"}'

# Plain text for people, streamed as it's written (-N turns off curl's buffering)
curl -N -X POST $URL/chat/stream -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"question": "How do I connect a second monitor?"}'

# Actions need your approval: this returns "approval_required" with the exact action and an approval_id
curl -X POST $URL/chat -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"question": "Please reset my password"}'
curl -X POST $URL/chat/approve -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"session_id": "<session_id>", "decisions": {"<approval_id>": true}}'

# Continue a conversation: send back the session_id (/chat/stream returns it in the X-Session-Id header)
curl $URL/sessions/<session_id> -H "$AUTH"    # your stored conversation

# What search finds for a query, with relevance scores
curl -G $URL/search -H "$AUTH" --data-urlencode "q=my laptop got stolen"
```

The assistant is an agent built on [Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/). It searches the policies in `data/policies/` (indexed in Azure AI Search), looks up tickets and checks laptop warranties in a fake backend (`app/backend.py`; try asset tags `CTSO-10442`, `CTSO-08817`, `CTSO-11950`, `CTSO-09323`), and it can open tickets and reset passwords. Nothing is real, but tickets and resets are stored in Cosmos DB. It acts as the signed-in user: you can see your own tickets and reset your own password. Service Desk staff, with the `Helpdesk.Agent` app role, can see every ticket (`INC0010001`–`INC0010005` belong to fake users) and reset anyone's password. To try that, give yourself the role and get a fresh token:

```bash
set -a; eval "$(azd env get-values)"; set +a
python -m scripts.agent_role add       # or: remove
TOKEN=$(az account get-access-token --scope "api://$CID/.default" --query accessToken -o tsv)
```

Messages and the text tools return are checked with Azure AI Content Safety: prompt attacks are blocked, injected instructions in tickets and documents are withheld from the model, and actions waiting for approval come with warnings (made-up ticket details, password resets for accounts you never mentioned). See [v8](docs/iterations/v8.md), including the injection that gets through Prompt Shields. Set `SAFETY_CHECKS=off` locally to see the attacks work.

Every response includes `usage` (tokens and estimated cost) and a `trace_id`. Each request is traced in Application Insights: model calls with token counts, tools, searches, safety checks. See [v9](docs/iterations/v9.md) for queries.

Evaluations check the agent against a golden dataset of 19 cases (answers, permissions, approvals, attacks), with deterministic checks and LLM judges. Run them with the azd values exported:

```bash
pip install -r evals/requirements.txt
python -m evals.run          # about 4 minutes; exit code 1 if the gate fails
```

GitHub Actions runs them on pull requests once `python -m scripts.setup_ci <owner>/<repo>` has set up access. See [v10](docs/iterations/v10.md).

After changing a policy, update the index without redeploying (with the azd values exported as above): `python -m scripts.ingest`.

Conversations are stored in Cosmos DB for 7 days after their last message. Only the newest messages that fit in `HISTORY_TOKEN_BUDGET` tokens (default 2000) are sent to the model.

The system prompt lives in [`prompts/`](prompts/). Set `PROMPT_NAME` to use a different prompt file (default `helpdesk-v7`).

To run the API locally against the same Azure resources (it uses your `azd`/`az` login, no keys):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
azd env get-values > .env
uvicorn app.main:app --reload --env-file .env    # then call http://localhost:8000 with the same token
```

When you're done, remove everything. AI Search bills by the hour even when idle, so don't leave it running. `azd down` also deletes the app registration:

```bash
azd down --purge
```

## Roadmap

| Tag | Iteration | Status |
|---|---|---|
| v0 | [Hello LLM](docs/iterations/v0.md) | ✅ done |
| v1 | [Real service: FastAPI, Container Apps, managed identity, `azd up`](docs/iterations/v1.md) | ✅ done |
| v2 | [Prompting done right: prompt files, structured outputs, streaming](docs/iterations/v2.md) | ✅ done |
| v3 | [Memory: conversation state in Cosmos DB, token-window trimming](docs/iterations/v3.md) | ✅ done |
| v4 | [Grounding: RAG with Azure AI Search and citations](docs/iterations/v4.md) | ✅ done |
| v5 | [First tools: hand-written agent loop with read-only tools](docs/iterations/v5.md) | ✅ done |
| v6 | [Agent framework + write actions](docs/iterations/v6.md) | ✅ done |
| v7 | [Human-in-the-loop approval + least privilege (signed-in user identity)](docs/iterations/v7.md) | ✅ done |
| v8 | [Safety: Prompt Shields for direct and indirect injection, groundedness](docs/iterations/v8.md) | ✅ done |
| v9 | [Observability: tracing, tokens and cost per request](docs/iterations/v9.md) | ✅ done |
| v10 | [Evaluations as a CI gate](docs/iterations/v10.md) | ✅ current |
| v11 | Resilience and cost control: AI gateway, failover, caching, agent step limits | planned |
| v12 | Continuous improvement: feedback loop, red teaming | planned |
