"""
ChromaDB vector store for the AI Job Seeker Agent.

Collections:
  cv_chunks         — Tina's CV content, chunked for semantic search
  writing_samples   — Writing samples (essays, articles, etc.)
  resumes           — Previous tailored resumes
  job_descriptions  — Scraped job descriptions
  preferred_roles   — Preferred role descriptions from Tina's profile JSONs
"""

import json
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import (
    OpenAIEmbeddingFunction,
    DefaultEmbeddingFunction,
)

DATA_DIR = Path(__file__).parent.parent / "data"
CHROMA_DIR = DATA_DIR / "chroma_db"

COLLECTIONS = [
    "cv_chunks",
    "writing_samples",
    "resumes",
    "job_descriptions",
    "preferred_roles",
]

_client = None
_embedding_fn = None


def get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def get_embedding_fn():
    """
    Return the embedding function.

    Uses OpenAI text-embedding-3-small when an API key is available
    (Streamlit Cloud / any env with OPENAI_API_KEY set).
    Falls back to ChromaDB's built-in ONNX-based embedder for local
    development without an OpenAI key — this avoids pulling in PyTorch.
    """
    global _embedding_fn
    if _embedding_fn is None:
        import os
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            try:
                import streamlit as st
                api_key = st.secrets.get("OPENAI_API_KEY")
            except Exception:
                pass
        if api_key:
            _embedding_fn = OpenAIEmbeddingFunction(
                api_key=api_key,
                model_name="text-embedding-3-small",
            )
        else:
            # Lightweight ONNX-based fallback — no PyTorch required
            _embedding_fn = DefaultEmbeddingFunction()
    return _embedding_fn


def get_collection(name: str) -> chromadb.Collection:
    if name not in COLLECTIONS:
        raise ValueError(f"Unknown collection '{name}'. Valid: {COLLECTIONS}")
    return get_client().get_or_create_collection(
        name=name,
        embedding_function=get_embedding_fn(),
    )


# ---------------------------------------------------------------------------
# Ingestion helpers
# ---------------------------------------------------------------------------

def ingest_cv(cv_text: str, chunk_size: int = 60, overlap: int = 15) -> int:
    """Chunk and upsert Tina's CV into the cv_chunks collection."""
    collection = get_collection("cv_chunks")
    chunks = _chunk_text(cv_text, chunk_size, overlap)
    collection.upsert(
        documents=chunks,
        ids=[f"cv_chunk_{i}" for i in range(len(chunks))],
    )
    print(f"[VectorStore] Ingested {len(chunks)} CV chunks.")
    return len(chunks)


def ingest_preferred_roles() -> int:
    """Ingest preferred roles from tina_academia_roles.json and tina_industry_roles.json."""
    collection = get_collection("preferred_roles")
    docs, ids, metas = [], [], []
    for fname in ["tina_academia_roles.json", "tina_industry_roles.json"]:
        with open(DATA_DIR / "preffered_roles" / fname) as f:
            role_areas = json.load(f)
        for area_obj in role_areas[0]:
            area = area_obj["area"]
            fit = area_obj.get("fit", "")
            seen_roles: set = set()
            for role in [area] + area_obj.get("roles", []):
                if role in seen_roles:
                    continue
                seen_roles.add(role)
                text = f"Role: {role}\nArea: {area}\nFit: {fit}"
                doc_id = f"role_{fname}_{area}_{role}"[:120].replace(" ", "_")
                docs.append(text)
                ids.append(doc_id)
                metas.append({"area": area, "role": role, "source": fname})
    collection.upsert(documents=docs, ids=ids, metadatas=metas)
    print(f"[VectorStore] Ingested {len(docs)} preferred role entries.")
    return len(docs)


def ingest_document(
    collection_name: str,
    text: str,
    doc_id: str,
    metadata: dict = None,
) -> None:
    """Upsert a single document into a named collection."""
    collection = get_collection(collection_name)
    collection.upsert(
        documents=[text],
        ids=[doc_id],
        metadatas=[metadata or {}],
    )


def ingest_file(
    collection_name: str,
    file_path: Path,
    doc_id: str = None,
    metadata: dict = None,
) -> None:
    """Read a text file and upsert it into a named collection."""
    text = Path(file_path).read_text(encoding="utf-8")
    doc_id = doc_id or Path(file_path).name
    ingest_document(collection_name, text, doc_id, metadata)
    print(f"[VectorStore] Ingested '{file_path}' into '{collection_name}'.")


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def query_collection(
    collection_name: str,
    query: str,
    n_results: int = 5,
) -> list:
    """Return the top-n most relevant document chunks for a query."""
    collection = get_collection(collection_name)
    count = collection.count()
    if count == 0:
        return []
    n_results = min(n_results, count)
    results = collection.query(query_texts=[query], n_results=n_results)
    return results["documents"][0] if results["documents"] else []


def collection_count(collection_name: str) -> int:
    return get_collection(collection_name).count()


# ---------------------------------------------------------------------------
# Chunking utility
# ---------------------------------------------------------------------------

def _chunk_text(text: str, chunk_size: int = 120, overlap: int = 20) -> list:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk:
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks
