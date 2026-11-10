"""What users think of answers: thumbs up or down, and why.

Each piece of feedback is stored with the question and the answer it's about. Traces (v9) don't record prompts or
answers, and conversations expire after 7 days, but a bad answer has to be readable for as long as it takes someone
to look at it and turn it into an eval case (evals/from_feedback.py).
"""

from datetime import UTC, datetime

from azure.core.credentials import TokenCredential
from azure.cosmos import CosmosClient


class FeedbackStore:
    """One Cosmos DB document per answer, keyed by the answer's trace id: giving feedback again replaces it."""

    def __init__(self, endpoint: str, credential: TokenCredential):
        database = CosmosClient(endpoint, credential=credential).get_database_client("helpdesk")
        self.container = database.get_container_client("feedback")

    def save(self, trace_id: str, **fields) -> None:
        self.container.upsert_item({"id": trace_id, "trace_id": trace_id, "created": datetime.now(UTC).isoformat(),
                                    "triaged": False, **fields})

    def untriaged_complaints(self) -> list[dict]:
        return list(self.container.query_items(
            "SELECT * FROM c WHERE c.helpful = false AND c.triaged = false", enable_cross_partition_query=True))

    def mark_triaged(self, trace_id: str) -> None:
        self.container.patch_item(trace_id, partition_key=trace_id,
                                  patch_operations=[{"op": "set", "path": "/triaged", "value": True}])
