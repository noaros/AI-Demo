"""Turns thumbs-down feedback into draft eval cases, in evals/candidates.yaml.

    python -m evals.from_feedback                                   # with the azd values exported
    python -m evals.run --dataset evals/candidates.yaml             # do the drafts reproduce the problem?

A complaint says what was wrong, not what's right. So the judge model drafts the rule a good answer must meet, from
the user's comment, and a person decides: fix the rule, add deterministic checks, then move the case into
golden.yaml. A case that passes on the current version doesn't reproduce the complaint: find out why before
promoting it. Each complaint is drafted once (marked triaged in Cosmos DB).
"""

import os
from pathlib import Path

import yaml
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI
from pydantic import BaseModel, Field

from app.feedback import FeedbackStore

CANDIDATES = Path(__file__).parent / "candidates.yaml"


class Draft(BaseModel):
    rules: list[str] = Field(description="1 or 2 yes/no rules a good answer meets, each checkable from the answer "
                                         "alone, phrased like 'Says that ...' or 'Does not ...'.")


def draft_rules(client: OpenAI, deployment: str, item: dict) -> list[str]:
    completion = client.chat.completions.parse(
        model=deployment,
        response_format=Draft,
        reasoning_effort="low",
        messages=[
            {"role": "system", "content": "A user of Contoso's IT helpdesk assistant marked an answer as unhelpful. "
                                          "Write the rules a good answer to the same question must meet, based on "
                                          "what the user said was wrong. Don't invent facts the comment doesn't give."},
            {"role": "user", "content": f"Question: {item['question']}\n\nAnswer:\n{item['answer']}\n\n"
                                        f"User's comment: {item['comment'] or '(none)'}"},
        ],
    )
    return completion.choices[0].message.parsed.rules


def append_candidates(cases: list[dict]) -> None:
    """Adds cases to candidates.yaml, which evals.run reads with --dataset. Shared with evals/red_team.py."""
    existing = yaml.safe_load(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else None
    with CANDIDATES.open("a", encoding="utf-8") as f:
        if existing is None:
            f.write("# Draft eval cases from user feedback and red teaming. Review each, then move it into golden.yaml.\n")
        f.write(yaml.safe_dump(cases, sort_keys=False, allow_unicode=True, width=120))


def main() -> None:
    credential = DefaultAzureCredential()
    store = FeedbackStore(os.environ["AZURE_COSMOS_ENDPOINT"], credential)
    token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    client = OpenAI(base_url=f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/v1/", api_key=token)
    deployment = os.environ.get("AZURE_OPENAI_JUDGE_DEPLOYMENT") or os.environ["AZURE_OPENAI_DEPLOYMENT"]

    complaints = store.untriaged_complaints()
    cases = [{
        "id": f"feedback-{item['trace_id'][:8]}",
        "question": item["question"],
        "user": "agent" if item.get("is_agent") else "user",
        "rubric": draft_rules(client, deployment, item),
        # Where it came from, for the reviewer. evals.run ignores these.
        "source": {"trace_id": item["trace_id"], "prompt": item.get("prompt"), "comment": item["comment"],
                   "bad_answer": item["answer"]},
    } for item in complaints]
    if cases:
        append_candidates(cases)
    for item in complaints:
        store.mark_triaged(item["trace_id"])
    print(f"{len(cases)} complaint(s) drafted into {CANDIDATES.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
