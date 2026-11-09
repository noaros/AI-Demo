import tiktoken
from azure.core.credentials import TokenCredential
from azure.cosmos import CosmosClient, exceptions

# gpt-5 models use the o200k_base tokenizer. Counting real tokens, not characters, is what the
# model's context window and the bill are measured in.
encoding = tiktoken.get_encoding("o200k_base")
PER_MESSAGE_OVERHEAD = 4  # role and separators the chat format adds around each message


class NotYours(Exception):
    """The session belongs to someone else (or to nobody: sessions from before sign-in existed)."""


class SessionStore:
    """One Cosmos DB document per conversation:
    {"id": <session id>, "owner": <user id>, "messages": [...], "pending": <turn waiting for approval> | null}

    The session id is also the partition key, so every read and write is a cheap point operation.
    """

    def __init__(self, endpoint: str, credential: TokenCredential):
        database = CosmosClient(endpoint, credential=credential).get_database_client("helpdesk")
        self.container = database.get_container_client("sessions")

    def load(self, session_id: str, owner: str) -> dict:
        """The session, or a new empty one. Raises NotYours if another user owns it."""
        try:
            doc = self.container.read_item(item=session_id, partition_key=session_id)
        except exceptions.CosmosResourceNotFoundError:
            return {"id": session_id, "owner": owner, "messages": [], "pending": None}  # a new conversation
        if doc.get("owner") != owner:
            raise NotYours(session_id)
        return doc

    def _update(self, session_id: str, owner: str, operations: list[dict], new_doc: dict) -> None:
        try:
            # Patch changes the document on the server: two requests to the same session can't overwrite
            # each other's messages. The filter makes Cosmos DB itself refuse to change someone else's session.
            self.container.patch_item(
                item=session_id,
                partition_key=session_id,
                patch_operations=operations,
                filter_predicate=f"FROM c WHERE c.owner = '{owner}'",  # owner is the id from a verified token
            )
        except exceptions.CosmosResourceNotFoundError:
            self.container.create_item({"id": session_id, "owner": owner, "messages": [], "pending": None} | new_doc)

    def append(self, session_id: str, owner: str, *messages: dict) -> None:
        self._update(session_id, owner, [{"op": "add", "path": "/messages/-", "value": m} for m in messages],
                     {"messages": list(messages)})

    def set_pending(self, session_id: str, owner: str, pending: dict | None) -> None:
        self._update(session_id, owner, [{"op": "set", "path": "/pending", "value": pending}], {"pending": pending})


def count_tokens(message: dict) -> int:
    return len(encoding.encode(message["content"])) + PER_MESSAGE_OVERHEAD


def trim_to_budget(history: list[dict], budget: int) -> list[dict]:
    """Keep the newest messages that fit in `budget` tokens. Older ones stay stored, just not sent."""
    kept, used = [], 0
    for message in reversed(history):
        used += count_tokens(message)
        if used > budget:
            break
        kept.append(message)
    kept.reverse()
    if kept and kept[0]["role"] == "assistant":
        kept = kept[1:]  # don't start with an answer whose question was cut off
    return kept
