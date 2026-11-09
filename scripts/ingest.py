"""Loads data/policies/*.md into the AI Search index. `azd up` runs it after provisioning.

Run it again by hand after changing a policy: python -m scripts.ingest
The folder is the source of truth: the index is rebuilt from it every time.
"""

import os
import re
import time
from pathlib import Path

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from openai import OpenAI

from app.search import INDEX_NAME, embed

POLICIES_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"
DIMENSIONS = 1536  # length of a text-embedding-3-small vector


def chunks(path: Path):
    """One chunk per "## " section: small enough to be precise, big enough to make sense on its own."""
    heading, *sections = re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    title, policy_id = re.match(r"# (.+) \((IT-POL-\d+)\)", heading.strip()).groups()
    for i, section in enumerate(sections):
        name, _, body = section.partition("\n")
        yield {"id": f"{path.stem}-{i}", "policy_id": policy_id, "title": title,
               "section": name.strip(), "content": body.strip()}


def index_definition() -> SearchIndex:
    return SearchIndex(
        name=INDEX_NAME,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(name="policy_id", filterable=True),
            SearchableField(name="title"),
            SearchableField(name="section"),
            SearchableField(name="content"),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=DIMENSIONS,
                vector_search_profile_name="default",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw")],  # fast approximate nearest-neighbour search
            profiles=[VectorSearchProfile(name="default", algorithm_configuration_name="hnsw")],
        ),
        semantic_search=SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="default",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        content_fields=[SemanticField(field_name="section"), SemanticField(field_name="content")],
                        # The ranker reads only the fields listed here. Without this, a search for
                        # "IT-POL-011" ranks passages that mention it above the policy itself.
                        keywords_fields=[SemanticField(field_name="policy_id")],
                    ),
                )
            ]
        ),
    )


def wait_for_access(index_client: SearchIndexClient) -> None:
    """Role assignments made by `azd provision` can take a few minutes to apply."""
    for _ in range(20):
        try:
            list(index_client.list_index_names())
            return
        except HttpResponseError as e:
            if e.status_code != 403:
                raise
            print("Waiting for your AI Search role assignment to take effect...")
            time.sleep(15)
    raise SystemExit("Still no access to AI Search after 5 minutes. Run `python -m scripts.ingest` later.")


def main() -> None:
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    openai = OpenAI(base_url=f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/v1/", api_key=token_provider)
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]

    docs = [chunk for path in sorted(POLICIES_DIR.glob("*.md")) for chunk in chunks(path)]
    texts = [f"{d['title']}: {d['section']}\n{d['content']}" for d in docs]  # embed with context, not bare text
    for doc, vector in zip(docs, embed(openai, os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"], texts)):
        doc["content_vector"] = vector

    index_client = SearchIndexClient(endpoint, credential)
    wait_for_access(index_client)
    try:
        index_client.delete_index(INDEX_NAME)  # rebuild, so deleted policies and sections disappear too
    except ResourceNotFoundError:
        pass
    index_client.create_index(index_definition())
    SearchClient(endpoint, INDEX_NAME, credential).upload_documents(docs)
    print(f"Indexed {len(docs)} chunks from {len(list(POLICIES_DIR.glob('*.md')))} policies into '{INDEX_NAME}'.")


if __name__ == "__main__":
    main()
