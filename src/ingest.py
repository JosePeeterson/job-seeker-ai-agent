"""
One-time ingestion script — populates ChromaDB with the user's profile data.

Run once (or re-run to refresh):
    python3 src/ingest.py

Expected files (create these via the Setup Profile page or manually):
    data/user_cv/user_cv.txt  — User's full CV as plain text  (REQUIRED)
    data/roles/roles.json     — Generated roles/keywords file (REQUIRED)
    data/resumes/             — Optional .txt previous tailored resumes
"""

import sys
from pathlib import Path

# Allow running as a script from the repo root
sys.path.insert(0, str(Path(__file__).parent))

from vector_store import (
    ingest_cv,
    ingest_preferred_roles_generic,
    ingest_file,
    collection_count,
)

DATA_DIR = Path(__file__).parent.parent / "data"
USER_CV_PATH = DATA_DIR / "user_cv" / "user_cv.txt"
ROLES_PATH = DATA_DIR / "roles" / "roles.json"


def ingest_all(force: bool = False, cv_path: Path = None, roles_path: Path = None) -> None:
    # ------------------------------------------------------------------ CV --
    if cv_path is None:
        cv_path = USER_CV_PATH

    if not cv_path.exists():
        print(
            f"[WARN] {cv_path} not found.\n"
            "       Please upload a resume via the Setup Profile page and re-run."
        )
    else:
        if force or collection_count("cv_chunks") == 0:
            try:
                from vector_store import get_client
                client = get_client()
                print("[INFO] Deleting existing cv_chunks collection (if it exists) to avoid duplicates...")
                if "cv_chunks" in client.list_collections():
                    client.delete_collection("cv_chunks")
            except Exception as e:
                print(f"[ERROR] Failed to delete existing cv_chunks collection: {e}")
                return

            cv_text = cv_path.read_text(encoding="utf-8")
            ingest_cv(cv_text)
            print(f"[INFO] CV ingested from: {cv_path}")
        else:
            print(f"[INFO] cv_chunks already has {collection_count('cv_chunks')} chunks. Use --force to re-ingest.")

    # -------------------------------------------------------- Preferred roles --
    if roles_path is None:
        roles_path = ROLES_PATH if ROLES_PATH.exists() else None

    if force or collection_count("preferred_roles") == 0:
        try:
            from vector_store import get_client
            client = get_client()
            print("[INFO] Deleting existing preferred_roles collection (if it exists) to avoid duplicates...")
            if "preferred_roles" in client.list_collections():
                client.delete_collection("preferred_roles")
        except Exception as e:
            print(f"[ERROR] Failed to delete existing preferred_roles collection: {e}")
            return

        if roles_path is not None:
            ingest_preferred_roles_generic(roles_path)
            print(f"[INFO] Roles ingested from: {roles_path}")
        else:
            print(
                "[WARN] data/roles/roles.json not found — skipping preferred_roles ingestion.\n"
                "       Generate keywords via the Setup Profile page first."
            )
    else:
        print(f"[INFO] preferred_roles already has {collection_count('preferred_roles')} entries. Use --force to re-ingest.")

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
