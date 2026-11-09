import tiktoken
from azure.core.credentials import TokenCredential
from azure.cosmos import CosmosClient, exceptions

# gpt-5 models use the o200k_base tokenizer. Counting real tokens, not characters, is what the
# model's context window and the bill are measured in.
encoding = tiktoken.get_encoding("o200k_base")
PER_MESSAGE_OVERHEAD = 4  # role and separators the chat format adds around each message


class SessionStore:
    """One Cosmos DB document per conversation: {"id": <session id>, "messages": [...]}.

    The session id is also the partition key, so every read and write is a cheap point operation.
    """

    def __init__(self, endpoint: str, credential: TokenCredential):
        database = CosmosClient(endpoint, credential=credential).get_database_client("helpdesk")
        self.container = database.get_container_client("sessions")

    def history(self, session_id: str) -> list[dict]:
        try:
            return self.container.read_item(item=session_id, partition_key=session_id)["messages"]
        except exceptions.CosmosResourceNotFoundError:
            return []  # a new conversation

    def append(self, session_id: str, *messages: dict) -> None:
        try:
            # Patch appends on the server: two requests to the same session can't overwrite each other.
            self.container.patch_item(
                item=session_id,
                partition_key=session_id,
                patch_operations=[{"op": "add", "path": "/messages/-", "value": m} for m in messages],
            )
        except exceptions.CosmosResourceNotFoundError:
            self.container.create_item({"id": session_id, "messages": list(messages)})


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
