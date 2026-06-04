"""
One-time ingestion script — populates ChromaDB with Tina's profile data.

Run once (or re-run to refresh):
    python3 src/ingest.py

Expected files (create these in data/ before running):
    data/tina_cv.txt          — Tina's full CV as plain text  (REQUIRED)
    data/writing_samples/     — Optional .txt writing samples
    data/resumes/             — Optional .txt previous tailored resumes
"""

import sys
from pathlib import Path

# Allow running as a script from the repo root
sys.path.insert(0, str(Path(__file__).parent))

from vector_store import (
    ingest_cv,
    ingest_preferred_roles,
    ingest_file,
    collection_count,
)

DATA_DIR = Path(__file__).parent.parent / "data"


def ingest_all(force: bool = False) -> None:
    # ------------------------------------------------------------------ CV --
    cv_path = DATA_DIR / "tina_cv.txt"
    if not cv_path.exists():
        print(
            "[WARN] data/tina_cv.txt not found.\n"
            "       Please save Tina's CV as plain text to that path and re-run."
        )
    else:
        if force or collection_count("cv_chunks") == 0:

            try:
                # delete the existing collection to avoid duplicates ids within cv_chunks
                from vector_store import get_client
                client = get_client()
                print("[INFO] Deleting existing cv_chunks collection (if it exists) to avoid duplicates...")
                if "cv_chunks" in client.list_collections():
                    client.delete_collection("cv_chunks")
            except Exception as e:
                print(f"[ERROR] Failed to delete existing cv_chunks collection: {e}")
                print("[ERROR] Please check the ChromaDB directory and try again.")
                return

            cv_text = cv_path.read_text(encoding="utf-8")
            ingest_cv(cv_text)
        else:
            print(f"[INFO] cv_chunks already has {collection_count('cv_chunks')} chunks. Use --force to re-ingest.")

    # -------------------------------------------------------- Preferred roles --
    if force or collection_count("preferred_roles") == 0:

        try:
            # delete the existing collection to avoid duplicates ids within preferred_roles
            from vector_store import get_client
            client = get_client()
            print("[INFO] Deleting existing preferred_roles collection (if it exists) to avoid duplicates...")
            if "preferred_roles" in client.list_collections():
                client.delete_collection("preferred_roles")
        except Exception as e:
            print(f"[ERROR] Failed to delete existing preferred_roles collection: {e}")
            print("[ERROR] Please check the ChromaDB directory and try again.")
            return


        ingest_preferred_roles()
    else:
        print(f"[INFO] preferred_roles already has {collection_count('preferred_roles')} entries. Use --force to re-ingest.")

    # ------------------------------------------------------- Writing samples --
    samples_dir = DATA_DIR / "writing_samples"
    if samples_dir.exists():
        txt_files = list(samples_dir.glob("*.txt"))
        if txt_files:
            for fp in txt_files:
                ingest_file(
                    "writing_samples",
                    fp,
                    doc_id=f"sample_{fp.stem}",
                    metadata={"filename": fp.name},
                )
        else:
            print("[INFO] No .txt writing samples found in data/writing_samples/")
    else:
        print("[INFO] data/writing_samples/ folder not found — skipping.")

    # ------------------------------------------------------- Tailored resumes --
    resumes_dir = DATA_DIR / "resumes"
    if resumes_dir.exists():
        txt_files = list(resumes_dir.glob("*.txt"))
        if txt_files:
            for fp in txt_files:
                ingest_file(
                    "resumes",
                    fp,
                    doc_id=f"resume_{fp.stem}",
                    metadata={"filename": fp.name},
                )
        else:
            print("[INFO] No .txt resumes found in data/resumes/ — skipping.")
    else:
        print("[INFO] data/resumes/ folder not found — skipping.")

    # ---------------------------------------------------------------- Summary --
    print("\n[INFO] ChromaDB collection sizes:")
    from vector_store import COLLECTIONS
    for name in COLLECTIONS:
        print(f"  {name:25s} → {collection_count(name)} documents")


if __name__ == "__main__":
    force = "--force" in sys.argv
    ingest_all(force=force)
