"""Embedding-based retrieval over the Chart of Accounts.

Backs the categorizer's RAG path (`Config.USE_RAG_COA_RETRIEVAL`): instead of
dumping all ~292 accounts into every categorization prompt, embed each line
item and retrieve the top-k candidate accounts from a persisted Chroma
collection built once from `coa_data.json`.
"""

import hashlib
import threading

import chromadb
from chromadb.errors import NotFoundError

from coa.chart_of_accounts import COA_ACCOUNTS, COA_DIR
from coa.embeddings import embed_texts

CHROMA_DIR = COA_DIR / ".chroma"
COLLECTION_NAME = "coa_accounts"

# categorizer.py builds batches in parallel via ThreadPoolExecutor; each call
# used to construct its own PersistentClient against the same directory,
# racing on first-time initialization. Cache one client per path instead.
_clients_lock = threading.Lock()
_clients: dict = {}


def _account_text(account) -> str:
    parts = [account.name, *account.aliases]
    if account.description:
        parts.append(account.description)
    return " — ".join(parts)


def _coa_data_hash() -> str:
    return hashlib.sha256((COA_DIR / "coa_data.json").read_bytes()).hexdigest()


def _get_client():
    path = str(CHROMA_DIR)
    client = _clients.get(path)
    if client is None:
        with _clients_lock:
            client = _clients.get(path)
            if client is None:
                client = chromadb.PersistentClient(path=path)
                _clients[path] = client
    return client


def build_index(force_rebuild: bool = False):
    """Embed each CoA account once and persist to a local Chroma collection.

    Skips re-embedding if the persisted collection already matches the
    current coa_data.json (tracked via a content hash in collection metadata).
    """
    client = _get_client()
    current_hash = _coa_data_hash()

    try:
        existing = client.get_collection(COLLECTION_NAME)
    except NotFoundError:
        existing = None

    if existing is not None:
        if not force_rebuild and existing.metadata and existing.metadata.get("coa_data_hash") == current_hash:
            return existing
        client.delete_collection(COLLECTION_NAME)

    collection = client.get_or_create_collection(
        COLLECTION_NAME,
        embedding_function=None,
        metadata={"coa_data_hash": current_hash},
    )

    codes = list(COA_ACCOUNTS.keys())
    texts = [_account_text(COA_ACCOUNTS[code]) for code in codes]
    embeddings = embed_texts(texts)

    collection.add(
        ids=codes,
        embeddings=embeddings,
        metadatas=[{"series": COA_ACCOUNTS[code].series} for code in codes],
        documents=texts,
    )
    return collection


def retrieve_candidates(items: list[dict], k: int = 10) -> dict[str, list[str]]:
    """Return the top-k candidate CoA account codes per line item.

    Args:
        items: dicts with at least a "label" key (and optionally "section").
        k: number of candidate accounts to retrieve per item.

    Returns:
        Mapping of item label -> ranked list of candidate account codes.
    """
    if not items:
        return {}

    collection = build_index()

    queries = [
        f"{item['label']} ({item['section']})" if item.get("section") else item["label"]
        for item in items
    ]
    query_embeddings = embed_texts(queries)

    results = collection.query(query_embeddings=query_embeddings, n_results=k)

    return {
        item["label"]: ids
        for item, ids in zip(items, results["ids"])
    }
