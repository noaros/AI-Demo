from dataclasses import dataclass

from azure.core.credentials import TokenCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import OpenAI

INDEX_NAME = "policies"
# The semantic ranker scores each passage from 0 (unrelated) to 4 (answers the question).
# Below this, a passage is noise: better to send the model nothing than something off-topic.
# Measured on our policies: real matches score 2.2–2.7, an off-topic question's best ~1.7.
MIN_RERANKER_SCORE = 2.0


@dataclass(frozen=True)
class Passage:
    policy_id: str
    title: str
    section: str
    content: str
    score: float  # semantic ranker score, 0–4


def embed(openai: OpenAI, deployment: str, texts: list[str]) -> list[list[float]]:
    """Turns text into vectors: texts with similar meaning get vectors that are close together."""
    return [item.embedding for item in openai.embeddings.create(model=deployment, input=texts).data]


class PolicySearch:
    def __init__(self, endpoint: str, credential: TokenCredential, openai: OpenAI, embedding_deployment: str):
        self.client = SearchClient(endpoint, INDEX_NAME, credential)
        self.openai = openai
        self.embedding_deployment = embedding_deployment

    def find(self, query: str, top: int = 5, min_score: float = MIN_RERANKER_SCORE) -> list[Passage]:
        [vector] = embed(self.openai, self.embedding_deployment, [query])
        results = self.client.search(
            search_text=query,  # keyword search: exact terms like "IT-POL-011" or "GlobalProtect"
            vector_queries=[  # vector search: same meaning in other words ("my laptop got nicked")
                VectorizedQuery(vector=vector, k_nearest_neighbors=50, fields="content_vector")
            ],
            query_type="semantic",  # then a ranking model re-reads the merged top 50 against the query
            semantic_configuration_name="default",
            top=top,
            select=["policy_id", "title", "section", "content"],
        )
        return [
            Passage(r["policy_id"], r["title"], r["section"], r["content"], r["@search.reranker_score"])
            for r in results
            if r["@search.reranker_score"] >= min_score
        ]
