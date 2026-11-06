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

**Current iteration: [v0: Hello LLM](docs/iterations/v0.md)**

At this stage the Azure resources are created by hand:

```bash
az group create -n rg-ai-demo -l eastus2

az cognitiveservices account create -n contoso-aoai -g rg-ai-demo \
  --kind OpenAI --sku S0 -l eastus2 --custom-domain contoso-aoai

# Find an available model version for your region:
#   az cognitiveservices account list-models -n contoso-aoai -g rg-ai-demo
az cognitiveservices account deployment create -n contoso-aoai -g rg-ai-demo \
  --deployment-name chat --model-name gpt-5-mini --model-format OpenAI \
  --model-version <version> --sku-name GlobalStandard --sku-capacity 10

az cognitiveservices account keys list -n contoso-aoai -g rg-ai-demo
```

Then copy `.env.example` to `.env`, fill in the endpoint, key and deployment name, and run:

```bash
pip install -r requirements.txt
python hello.py
```

## Roadmap

| Tag | Iteration | Status |
|---|---|---|
| v0 | [Hello LLM](docs/iterations/v0.md) | ✅ current |
| v1 | Real service: FastAPI, Container Apps, managed identity, `azd up` | planned |
| v2 | Prompting done right: prompt files, structured outputs, streaming | planned |
| v3 | Memory: conversation state, token-window trimming | planned |
| v4 | Grounding: RAG with Azure AI Search and citations | planned |
| v5 | First tools: hand-written agent loop with read-only tools | planned |
| v6 | Agent framework + write actions | planned |
| v7 | Human-in-the-loop approval + least privilege (on-behalf-of user identity) | planned |
| v8 | Safety: Prompt Shields for direct and indirect injection, groundedness | planned |
| v9 | Observability: tracing, tokens and cost per request | planned |
| v10 | Evaluations as a CI gate | planned |
| v11 | Resilience and cost control: AI gateway, failover, caching, agent step limits | planned |
| v12 | Continuous improvement: feedback loop, red teaming | planned |
