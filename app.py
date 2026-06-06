"""
AI Job Seeker Agent — Streamlit UI
Run with:  streamlit run app.py
"""

import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple

import streamlit as st

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from cover_letter import generate_cover_letter
from notify import build_html, send_digest
from resume_tailor import tailor_resume, _slug
from resume_to_pdf import convert_resume as _convert_resume_to_pdf

DATA_DIR = ROOT / "data"
RANKED_PATH = DATA_DIR / "ranked_jobs" / "ranked_jobs.json"
SCRAPED_PATH = DATA_DIR / "scraped_jobs" / "scraped_jobs.json"
ARCHIVED_JOBS_PATH = DATA_DIR / "archived_jobs" / "archived_jobs.json"
ROLES_PATH = DATA_DIR / "roles" / "roles.json"
USER_CV_PATH = DATA_DIR / "user_cv" / "user_cv.txt"
CV_PATH = USER_CV_PATH  # populated via Setup Profile page
RESUMES_DIR = DATA_DIR / "resumes"
COVER_LETTERS_DIR = DATA_DIR / "cover_letters"

MODELS = ["llama3.1:8b", "mistral:7b", "qwen3:8b","gpt-4o-mini", "gpt-4o"]
OLLAMA_MODELS = {m for m in MODELS if not m.startswith("gpt-")}


def _ensure_ollama_running() -> bool:
    """Start `ollama serve` in the background if it isn't already responsive.
    Returns True if Ollama was already running, False if we just launched it.
    """
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, timeout=3)
        if result.returncode == 0:
            return True  # already up
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    # Not running — launch it and redirect output to /tmp/ollama.log
    with open("/tmp/ollama.log", "w") as _log:
        subprocess.Popen(["ollama", "serve"], stdout=_log, stderr=_log)
    return False

# ── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Job Seeker agent",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Password gate ───────────────────────────────────────────────────────────
# Set APP_PASSWORD_HASH in your environment or .streamlit/secrets.toml:
#   python3 -c "import hashlib; print(hashlib.sha256(b'yourpassword').hexdigest())"
#   export APP_PASSWORD_HASH="<hash>"   # or add to .streamlit/secrets.toml
# If APP_PASSWORD_HASH is not set, the gate is disabled (local dev convenience).

def _get_expected_hash() -> str:
    h = os.environ.get("APP_PASSWORD_HASH", "")
    if not h:
        try:
            h = st.secrets.get("APP_PASSWORD_HASH", "")
        except Exception:
            pass
    return h


def _check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    expected = _get_expected_hash()
    if not expected:
        return True  # gate disabled — no hash configured
    with st.form("login_form"):
        st.title("🔒 Access required")
        st.caption("Enter the app password to continue.")
        pwd = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Enter")
        if submitted:
            entered = hashlib.sha256(pwd.encode()).hexdigest()
            if hmac.compare_digest(entered, expected):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


if not _check_password():
    st.stop()


# ── Helpers ─────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_ranked() -> list:
    if RANKED_PATH.exists():
        with open(RANKED_PATH) as f:
            return json.load(f)
    return []


@st.cache_data(show_spinner=False)
def _load_archived() -> list:
    if ARCHIVED_JOBS_PATH.exists():
        with open(ARCHIVED_JOBS_PATH) as f:
            return json.load(f)
    return []


def _score_colour(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 45:
        return "orange"
    return "red"


def _badge(decision: str) -> str:
    if decision == "apply":
        return "🟢 APPLY"
    if decision == "maybe":
        return "🟡 MAYBE"
    return "🔴 REJECT"


def _docs_exist(job: dict) -> Tuple[str, str]:
    slug = _slug(job.get("title", ""), job.get("company", ""))
    resume = RESUMES_DIR / f"{slug}.txt"
    cl = COVER_LETTERS_DIR / f"{slug}.txt"
    return (str(resume) if resume.exists() else "", str(cl) if cl.exists() else "")


def _tailor_and_convert(job: dict, cv_text: str, model: str) -> str:
    """Tailor a resume and auto-convert the saved .txt to a PDF."""
    tailored = tailor_resume(job, cv_text=cv_text, model=model)
    try:
        slug = _slug(job.get("title", ""), job.get("company", ""))
        txt_path = RESUMES_DIR / f"{slug}.txt"
        if txt_path.exists():
            _convert_resume_to_pdf(txt_path)
    except Exception:
        pass  # PDF conversion is best-effort; never block the main flow
    return tailored


# ── PDF → text helper ────────────────────────────────────────────────────────

def _pdf_bytes_to_text(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF file supplied as bytes using pypdf."""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


# ── Roles generation via LLM ─────────────────────────────────────────────────

_ROLES_SYSTEM_PROMPT = """You are a career advisor helping build a job search keyword profile.
Given a user's preferred industries and job titles, generate a comprehensive list of job search areas not exceeding 20 keywords.
Return ONLY a valid JSON array with no markdown or extra text."""

_ROLES_USER_TEMPLATE = """The user is looking for work. Their preferences:

Preferred industries/areas:
{industries}

Preferred job titles/roles:
{job_titles}

Generate a JSON array of role-area objects. Each object must have:
- "area": a short label for the job category (3-6 words)
- "roles": a list of specific job titles to search for on job boards (1-5 words each, realistic postings)
- "fit": one sentence explaining why this area matches the user's preferences

Requirements:
- Include ALL of the user's stated roles, grouped sensibly into areas
- Add similar/adjacent roles the user did not mention (e.g. senior/junior variants, related titles)
- Produce enough entries that the total distinct keywords (area + all roles) is at least 15
- Keep titles short and realistic — exactly as they would appear on JobStreet or LinkedIn
- Do NOT include special characters except hyphens

Return only the JSON array, nothing else."""


def _generate_roles_json(industries: str, job_titles: str, model: str) -> list:
    """Call the LLM to generate a roles.json list from the user's preferences."""
    from llm import chat
    prompt = _ROLES_USER_TEMPLATE.format(industries=industries.strip(), job_titles=job_titles.strip())
    full_prompt = _ROLES_SYSTEM_PROMPT + "\n\n" + prompt
    raw = chat(full_prompt, model=model, temperature=0.3)
    # Strip any accidental markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("```").strip()
    return json.loads(raw)


# ── Session cleanup on browser close ────────────────────────────────────────

def _archive_ranked_jobs() -> None:
    """Append current ranked_jobs.json into archived_jobs.json, then delete it.
    Also prunes archived jobs older than 30 days.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    def _is_recent(job: dict) -> bool:
        scraped_at = job.get("scraped_at", "")
        if not scraped_at:
            return True  # keep jobs with no date
        try:
            dt = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except Exception:
            return True  # keep if date is unparseable

    existing: list = []
    if ARCHIVED_JOBS_PATH.exists():
        try:
            with open(ARCHIVED_JOBS_PATH) as f:
                existing = json.load(f)
        except Exception:
            existing = []
    # Prune stale archived jobs first
    existing = [j for j in existing if _is_recent(j)]

    if RANKED_PATH.exists():
        try:
            with open(RANKED_PATH) as f:
                current = json.load(f)
        except Exception:
            current = []
        # Deduplicate by URL before appending
        existing_urls = {j.get("url", "") for j in existing}
        new_jobs = [j for j in current if j.get("url", "") not in existing_urls]
        existing = existing + new_jobs
        RANKED_PATH.unlink(missing_ok=True)

    ARCHIVED_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVED_JOBS_PATH, "w") as f:
        json.dump(existing, f, indent=2)


def _cleanup_profile_files() -> None:
    """Archive ranked jobs, delete scraped jobs, profile files, and wipe ChromaDB."""
    # Archive ranked jobs before removing them
    try:
        _archive_ranked_jobs()
    except Exception:
        pass
    # Delete scraped jobs file
    try:
        SCRAPED_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    for path in (USER_CV_PATH, ROLES_PATH):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    # Delete all ChromaDB collections
    try:
        from vector_store import get_client, COLLECTIONS
        client = get_client()
        for name in COLLECTIONS:
            try:
                client.delete_collection(name)
            except Exception:
                pass
    except Exception:
        pass


def _watch_session_and_cleanup(session_id: str) -> None:
    """Daemon thread: polls until the session is gone, then removes profile files."""
    try:
        import streamlit.runtime as _st_runtime
        while True:
            time.sleep(5)
            try:
                rt = _st_runtime.get_instance()
                if rt is None or not rt.is_active_session(session_id):
                    _cleanup_profile_files()
                    break
            except Exception:
                break
    except Exception:
        pass


# ── Session state defaults ───────────────────────────────────────────────────
if "model" not in st.session_state:
    st.session_state.model = MODELS[0]
if "pipeline_log" not in st.session_state:
    st.session_state.pipeline_log = ""
if "job_overrides" not in st.session_state:
    # URL -> "applied" | "skipped" | "pending" (in-session overrides)
    st.session_state.job_overrides = {}
if "scraping_in_progress" not in st.session_state:
    st.session_state.scraping_in_progress = False
if "scrape_lock_started_at" not in st.session_state:
    st.session_state.scrape_lock_started_at = 0.0

# Safety valve: recover from stale lock state (e.g. browser refresh mid-run).
if st.session_state.get("scraping_in_progress"):
    started_at = float(st.session_state.get("scrape_lock_started_at", 0.0) or 0.0)
    if started_at and (time.time() - started_at) > 1800:  # 30 minutes
        st.session_state.scraping_in_progress = False
        st.session_state.scrape_lock_started_at = 0.0

# Archive any ranked_jobs.json left over from a previous session (e.g. if
# the cleanup thread didn't run — server crash, forced kill, etc.).
if "session_initialized" not in st.session_state:
    try:
        _archive_ranked_jobs()
        _load_ranked.clear()
        _load_archived.clear()
    except Exception:
        pass
    st.session_state["session_initialized"] = True

# Register a one-per-session background cleanup thread
if "_cleanup_thread_started" not in st.session_state:
    st.session_state["_cleanup_thread_started"] = True
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_ctx
        _ctx = _get_ctx()
        if _ctx is not None:
            _t = threading.Thread(
                target=_watch_session_and_cleanup,
                args=(_ctx.session_id,),
                daemon=True,
            )
            _t.start()
    except Exception:
        pass


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("AI Job Seeker agent")
    st.caption("Powered by OpenAI / Ollama + ChromaDB")
    st.divider()

    _scraping = st.session_state.get("scraping_in_progress", False)
    if _scraping:
        st.warning("⏳ Scrape & Rank is running — navigation locked until complete.", icon="🔒")
        if st.button("🔓 Force unlock", width="stretch"):
            st.session_state.scraping_in_progress = False
            st.session_state.scrape_lock_started_at = 0.0
            st.rerun()

    page = st.radio(
        "Navigate",
        ["1. 👤 Setup Profile", "2. ⚙️ New Job Search", "3. 📊 New Jobs Dashboard", "4. 🔍 Review New Jobs", "5. 📦 Archived Jobs"],
        label_visibility="collapsed",
        disabled=_scraping,
    )
    # Force the page back to the pipeline page while scraping so that even if
    # Streamlit queues a navigation event it doesn't abandon an in-progress run.
    if _scraping:
        page = "2. ⚙️ New Job Search"
    st.divider()

    st.session_state.model = st.selectbox("LLM model", MODELS, index=MODELS.index(st.session_state.model))

    # ── Auto-start Ollama when a local model is selected ─────────────────
    if st.session_state.model in OLLAMA_MODELS:
        _ollama_key = f"ollama_started_{st.session_state.model}"
        if _ollama_key not in st.session_state:
            with st.spinner("Starting Ollama…"):
                _already_running = _ensure_ollama_running()
                if not _already_running:
                    time.sleep(3)  # give the server a moment to become responsive
            st.session_state[_ollama_key] = True
            if _already_running:
                st.toast("Ollama already running ✓", icon="✅")
            else:
                st.toast("Ollama started ✓", icon="🦙")

    # ── Auto-ingest CV data if ChromaDB is empty (e.g. fresh Streamlit Cloud deploy) ──
    if "cv_ingested" not in st.session_state:
        try:
            from vector_store import collection_count
            from ingest import ingest_all
            if collection_count("cv_chunks") == 0:
                with st.spinner("Indexing CV data…"):
                    ingest_all()
        except Exception as _ingest_err:
            st.warning(f"Auto-ingest skipped: {_ingest_err}")
        st.session_state.cv_ingested = True

    ranked = _load_ranked()
    if ranked:
        n_apply = sum(1 for j in ranked if j["decision"] == "apply")
        n_maybe = sum(1 for j in ranked if j["decision"] == "maybe")
        n_reject = sum(1 for j in ranked if j["decision"] == "reject")
        st.metric("Apply", n_apply, delta=None)
        st.metric("Maybe", n_maybe, delta=None)
        st.metric("Total", len(ranked), delta=None)
    else:
        st.info("No ranked jobs yet.")


# ── Job list renderer (shared by Review New Jobs and Archived Jobs) ──────────

def _render_job_list(jobs: list, ns: str = ""):
    if not jobs:
        st.info("No jobs in this category.")
        return

    cv_text = CV_PATH.read_text() if CV_PATH.exists() else ""

    for job in jobs:
        url = job.get("url", "")
        # Unique key prefix per tab so the same job URL doesn't clash across tabs
        k = f"{ns}_{url}"
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        score = job.get("score", 0)
        decision = job.get("decision", "reject")
        details = job.get("ranking_details", {})
        explanation = details.get("explanation", "")
        strengths = details.get("strengths", [])
        gaps = details.get("gaps", [])

        resume_path, cl_path = _docs_exist(job)
        db_status = st.session_state.job_overrides.get(url, "pending")

        colour = _score_colour(score)
        status_icon = {"applied": "✅", "skipped": "⏭️"}.get(db_status, "")

        with st.expander(
            f"{status_icon} **{title}** — {company}  |  :{colour}[**{score}/100**]  {_badge(decision)}",
            expanded=(decision == "apply" and db_status == "pending"),
        ):
            if url:
                st.markdown(f"[🔗 View job posting]({url})")

            if explanation:
                st.info(explanation)

            col_s, col_g = st.columns(2)
            with col_s:
                if strengths:
                    st.markdown("**✅ Strengths**")
                    for s in strengths[:4]:
                        st.markdown(f"- {s}")
            with col_g:
                if gaps:
                    st.markdown("**⚠️ Gaps**")
                    for g in gaps[:4]:
                        st.markdown(f"- {g}")

            st.divider()

            # Document generation / download
            doc_col, action_col = st.columns([3, 2])

            with doc_col:
                if resume_path and cl_path:
                    st.success("Tailored resume + cover letter ready")
                    dl1, dl2, dl3 = st.columns(3)
                    pdf_path = Path(resume_path).with_suffix(".pdf")
                    if pdf_path.exists():
                        dl1.download_button(
                            "📄 Resume (PDF)",
                            data=pdf_path.read_bytes(),
                            file_name=pdf_path.name,
                            mime="application/pdf",
                            key=f"dl_resume_pdf_{k}",
                        )
                        dl2.download_button(
                            "📄 Resume (txt)",
                            data=Path(resume_path).read_text(),
                            file_name=Path(resume_path).name,
                            mime="text/plain",
                            key=f"dl_resume_txt_{k}",
                        )
                    else:
                        dl1.download_button(
                            "📄 Download Resume",
                            data=Path(resume_path).read_text(),
                            file_name=Path(resume_path).name,
                            mime="text/plain",
                            key=f"dl_resume_{k}",
                        )
                    # dl3.download_button(
                    #     "📧 Download Cover Letter",
                    #     data=Path(cl_path).read_text(),
                    #     file_name=Path(cl_path).name,
                    #     mime="text/plain",
                    #     key=f"dl_cl_{k}",
                    # )
                else:
                    missing = []
                    if not resume_path:
                        missing.append("resume")
                    if not cl_path:
                        missing.append("cover letter")
                    st.warning(f"No {' or '.join(missing)} yet.")
                    if st.button("⚡ Create custom Resume", key=f"gen_{k}"):
                        with st.spinner("Generating tailored resume..."):
                            try:
                                tailored = _tailor_and_convert(job, cv_text=cv_text, model=st.session_state.model)
                                generate_cover_letter(job, tailored_resume=tailored, model=st.session_state.model)
                                st.success("Documents generated!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")

            # with action_col:
            #     if db_status == "applied":
            #         st.success("✅ Marked as Applied")
            #     elif db_status == "skipped":
            #         st.error("⏭️ Skipped")
            #     else:
            #         b1, b2 = st.columns(2)
            #         if b1.button("✅ Applied", key=f"apply_{k}", type="primary"):
            #             st.session_state.job_overrides[url] = "applied"
            #             st.rerun()
            #         if b2.button("⏭️ Skip", key=f"skip_{k}"):
            #             st.session_state.job_overrides[url] = "skipped"
            #             st.rerun()


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Setup Profile
# ═══════════════════════════════════════════════════════════════════════════
if page == "1. 👤 Setup Profile":
    st.header("👤 Setup Your Job-Seeker Profile")
    st.caption(
        "Upload your resume and set your job preferences. "
        "The app will generate search keywords and index your profile into ChromaDB."
    )

    # ── Section 1: Resume Upload ─────────────────────────────────────────
    st.subheader("1. Upload your resume (PDF)")

    if USER_CV_PATH.exists():
        st.info(f"Current resume in use: `{USER_CV_PATH.name}` ({USER_CV_PATH.parent.name}/)")    

    uploaded_pdf = st.file_uploader(
        "Upload a PDF resume",
        type=["pdf"],
        help="Your resume will be extracted to plain text and saved as data/user_cv/user_cv.txt",
    )

    if uploaded_pdf is not None:
        pdf_bytes = uploaded_pdf.read()
        with st.spinner("Extracting text from PDF…"):
            try:
                cv_text = _pdf_bytes_to_text(pdf_bytes)
                if len(cv_text.strip()) < 100:
                    st.error("Could not extract enough text from the PDF. Please ensure the resume file is text-based (not a scanned image).")
                else:
                    USER_CV_PATH.parent.mkdir(parents=True, exist_ok=True)
                    USER_CV_PATH.write_text(cv_text, encoding="utf-8")
                    st.success(f"Resume extracted and saved ({len(cv_text)} characters). Preview:")
                    st.text_area("Extracted CV text (preview)", cv_text[:1500] + ("…" if len(cv_text) > 1500 else ""), height=200, disabled=True)
            except Exception as e:
                st.error(f"Failed to extract PDF: {e}")

    st.divider()

    # ── Section 2: Job Preferences ───────────────────────────────────────
    st.subheader("2. State your job preferences")

    col_ind, col_roles = st.columns(2)
    with col_ind:
        industries_input = st.text_area(
            "Preferred industries / areas",
            placeholder="e.g.\nConsulting, Corporate,\n Finance, Healthcare",
            height=160,
            help="One per line. Be descriptive — the LLM will use this to generate keywords.",
        )
    with col_roles:
        roles_input = st.text_area(
            "Preferred job titles / occupations",
            placeholder="e.g.\nAccountant, Business Analyst,\n Data Scientist, Software Engineer",
            height=160,
            help="One per line. Include variations you'd accept.",
        )

    generate_btn = st.button(
        "✨ Generate search keywords",
        type="primary",
        disabled=not (industries_input.strip() and roles_input.strip()),
        help="Requires both fields to be filled in.",
    )

    if generate_btn:
        with st.spinner("Generating roles and keywords via LLM…"):
            try:
                roles = _generate_roles_json(industries_input, roles_input, st.session_state.model)
                ROLES_PATH.parent.mkdir(parents=True, exist_ok=True)
                ROLES_PATH.write_text(json.dumps(roles, indent=2), encoding="utf-8")
                st.session_state["roles_generated"] = True
                st.success(f"roles.json saved with {len(roles)} areas.")
            except json.JSONDecodeError as e:
                st.error(f"LLM returned invalid JSON: {e}. Try again or switch model.")
            except Exception as e:
                st.error(f"Error generating roles: {e}")

    # Show current roles.json if it exists
    if ROLES_PATH.exists():
        st.divider()
        st.subheader("3. Search keywords that will be used")

        with open(ROLES_PATH) as f:
            roles_data = json.load(f)

        # Collect all keywords the way job_scraper does
        import re as _re
        def _clean_kw(kw):
            kw = kw.strip()
            if len(kw.split()) > 6:
                return None
            if not _re.match(r'^[\w\s\-\,]+$', kw):
                return None
            return kw.lower()

        all_keywords = []
        for area_obj in roles_data:
            for kw in [area_obj["area"]] + area_obj.get("roles", []):
                cleaned = _clean_kw(kw)
                if cleaned and cleaned not in all_keywords:
                    all_keywords.append(cleaned)

        st.caption(f"**{len(all_keywords)} keywords** derived from {len(roles_data)} areas")

        # Display the areas expandably
        for area_obj in roles_data:
            with st.expander(f"**{area_obj['area']}**", expanded=False):
                st.caption(area_obj.get("fit", ""))
                st.markdown(", ".join(f"`{r}`" for r in area_obj.get("roles", [])))

        st.divider()
        st.subheader("All keywords (sorted)")
        # Show as a multi-column tag layout
        keyword_cols = st.columns(3)
        for i, kw in enumerate(sorted(all_keywords)):
            keyword_cols[i % 3].markdown(f"- {kw}")

        st.divider()

    # ── Section 3: Ingest into ChromaDB ──────────────────────────────────
    st.subheader("3. Auto-Ingested profile into ChromaDB")

    cv_ready = USER_CV_PATH.exists()
    roles_ready = ROLES_PATH.exists()

    c1, c2 = st.columns(2)
    c1.metric("Resume file", "✅ Ready" if cv_ready else "❌ Missing")
    c2.metric("roles.json", "✅ Ready" if roles_ready else "❌ Missing")


    from ingest import ingest_all
    with st.status("Ingesting profile into ChromaDB…", expanded=True) as ingest_status:
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                ingest_all(force=True)
            st.session_state.cv_ingested = True
            for line in buf.getvalue().splitlines():
                if line.strip():
                    ingest_status.write(line)
            ingest_status.update(label="✅ Profile ingested successfully!", state="complete")
        except Exception as e:
            ingest_status.update(label=f"❌ Ingestion failed: {e}", state="error")
            st.exception(e)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Review New Jobs
# ═══════════════════════════════════════════════════════════════════════════
elif page == "4. 🔍 Review New Jobs":
    st.header("🔍 Review & Apply New Jobs")

    ranked = _load_ranked()
    if not ranked:
        st.warning("No ranked jobs found. Setup Profile and run a New Job Search.")
        st.stop()

    tab_apply, tab_maybe, tab_all = st.tabs([
        f"🟢 Apply ({sum(1 for j in ranked if j['decision']=='apply')})",
        f"🟡 Maybe ({sum(1 for j in ranked if j['decision']=='maybe')})",
        f"📋 All ({len(ranked)})",
    ])

    with tab_apply:
        _render_job_list([j for j in ranked if j["decision"] == "apply"], ns="apply")
    with tab_maybe:
        _render_job_list([j for j in ranked if j["decision"] == "maybe"], ns="maybe")
    with tab_all:
        _render_job_list(ranked, ns="all")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: New Job Search
# ═══════════════════════════════════════════════════════════════════════════
elif page == "2. ⚙️ New Job Search":
    st.header("⚙️ New Job Search")

    col_opts, col_info = st.columns([2, 3])

    with col_opts:
        st.subheader("Options")
        scrape_sources = st.multiselect(
            "Job sources",
            ["JobStreet", "LinkedIn", "eFinancialCareers"],
            default=["JobStreet", "LinkedIn", "eFinancialCareers"],
            help="Which sites to scrape for job listings",
        )
        _source_map = {"JobStreet": "jobstreet", "LinkedIn": "linkedin", "eFinancialCareers": "efinancialcareers"}
        selected_sources = [_source_map[s] for s in scrape_sources] or ["jobstreet"]
        # skip_scrape = st.checkbox(
        #     "Skip scrape (use existing scraped_jobs.json)",
        #     value=SCRAPED_PATH.exists(),
        #     help="Uncheck to re-scrape (slower)",
        # )
        # skip_rank = st.checkbox(
        #     "Skip ranking (use existing ranked_jobs.json)",
        #     value=False,
        #     help="Only generate documents for already-ranked jobs",
        # )
        # gen_docs = st.checkbox("Generate docs for apply candidates", value=True)

    with col_info:
        st.subheader("Current data")
        scraped_n = len(json.load(open(SCRAPED_PATH))) if SCRAPED_PATH.exists() else 0
        ranked_n = len(_load_ranked())
        r_count = len(list(RESUMES_DIR.glob("*.txt"))) if RESUMES_DIR.exists() else 0
        cl_count = len(list(COVER_LETTERS_DIR.glob("*.txt"))) if COVER_LETTERS_DIR.exists() else 0

        st.metric("Scraped jobs", scraped_n)
        st.metric("Ranked jobs", ranked_n)
        st.metric("Resumes / Cover letters", f"{r_count} / {cl_count}")

    st.divider()

    # ── Individual step buttons ───────────────────────────────────────────
    st.subheader("Run steps")
    c1, c2, c3, c4 = st.columns(4)

    _profile_ready = USER_CV_PATH.exists() and ROLES_PATH.exists()
    if not _profile_ready:
        _missing = []
        if not USER_CV_PATH.exists():
            _missing.append("resume (step 1 on Setup Profile)")
        if not ROLES_PATH.exists():
            _missing.append("search keywords (step 2 on Setup Profile)")
        st.warning(f"⚠️ Complete your profile first — missing: {', '.join(_missing)}.")

    if c1.button("🕷️ Scrape and Rank", width="stretch", disabled=not _profile_ready):
        src_label = " + ".join(scrape_sources) or "JobStreet"
        st.session_state.scraping_in_progress = True
        st.session_state.scrape_lock_started_at = time.time()

        try:
            from job_scraper import (
                load_keywords,
                scrape_jobstreet,
                scrape_linkedin,
                scrape_efinancialcareers,
            )
            from job_ranker import rank_job

            keywords = load_keywords()
            _source_display = {
                "jobstreet": "JobStreet",
                "linkedin": "LinkedIn",
                "efinancialcareers": "eFinancialCareers",
            }
            sources_to_run = [s for s in ["jobstreet", "linkedin", "efinancialcareers"]
                              if s in selected_sources]
            n_sources = len(sources_to_run)

            # ── Phase 1: Scrape ──────────────────────────────────────────
            st.markdown(f"**🕷️ Scraping {src_label}…**")
            scrape_bar = st.progress(0, text=f"Starting scrape — {len(keywords)} keywords across {n_sources} source(s)")
            scraped_jobs_box = st.empty()

            all_jobs: list = []
            seen_urls: set = set()

            def _render_scraped_jobs_preview(jobs: list) -> None:
                with scraped_jobs_box.container():
                    st.caption(f"Scraped jobs preview: {len(jobs)} total")
                    if not jobs:
                        st.info("No jobs scraped yet.")
                        return
                    preview = []
                    for j in jobs[-100:]:
                        src = j.get("source", "")
                        preview.append({
                            "Title": j.get("title", ""),
                            "Company": j.get("company", ""),
                            "Source": _source_display.get(src, src),
                            "Location": j.get("location", ""),
                        })
                    st.dataframe(
                        preview,
                        width="stretch",
                        height=min(380, 60 + 28 * len(preview)),
                        hide_index=True,
                    )

            _render_scraped_jobs_preview(all_jobs)

            _scraper_fn = {
                "jobstreet": lambda kw: scrape_jobstreet(kw, fetch_full_descriptions=True),
                "linkedin": lambda kw: scrape_linkedin(kw, fetch_full_descriptions=True),
                "efinancialcareers": lambda kw: scrape_efinancialcareers(kw, fetch_full_descriptions=True),
            }

            for i, source in enumerate(sources_to_run):
                label = _source_display[source]
                scrape_bar.progress(i / n_sources, text=f"Scraping {label}… ({i + 1}/{n_sources})")
                sys.stdout = io.StringIO()
                sys.stderr = io.StringIO()
                try:
                    source_jobs = _scraper_fn[source](keywords)
                finally:
                    sys.stdout = sys.__stdout__
                    sys.stderr = sys.__stderr__
                new = 0
                for j in source_jobs:
                    key = j.get("url", "").split("?")[0]
                    if key not in seen_urls:
                        seen_urls.add(key)
                        j.setdefault("source", source)
                        all_jobs.append(j)
                        new += 1
                scrape_bar.progress(
                    (i + 1) / n_sources,
                    text=f"✅ {label}: {new} new jobs (total so far: {len(all_jobs)})",
                )
                _render_scraped_jobs_preview(all_jobs)

            scrape_bar.progress(1.0, text=f"✅ Scrape complete — {len(all_jobs)} unique jobs")
            SCRAPED_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(SCRAPED_PATH, "w") as f:
                json.dump(all_jobs, f, indent=2)

            # ── Phase 2: Rank ────────────────────────────────────────────
            st.markdown(f"**🏆 Ranking {len(all_jobs)} jobs…**")
            rank_bar = st.progress(0, text=f"Ranking 0 / {len(all_jobs)}")

            ranked: list = []
            for idx, job in enumerate(all_jobs):
                title = job.get("title", "Unknown")
                company = job.get("company", "")
                rank_bar.progress(
                    idx / max(len(all_jobs), 1),
                    text=f"Ranking {idx + 1} / {len(all_jobs)}: {title[:45]}…",
                )
                sys.stdout = io.StringIO()
                try:
                    ranked.append(rank_job(job, model=st.session_state.model))
                except Exception as exc:
                    ranked.append({
                        **job,
                        "score": 0,
                        "decision": "reject",
                        "requirements": {},
                        "ranking_details": {"explanation": f"Ranking error: {exc}"},
                    })
                finally:
                    sys.stdout = sys.__stdout__

            ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
            rank_bar.progress(1.0, text=f"✅ Ranking complete — {len(ranked)} jobs ranked")

            with open(RANKED_PATH, "w") as f:
                json.dump(ranked, f, indent=2)
            _load_ranked.clear()

            n_apply = sum(1 for j in ranked if j["decision"] == "apply")
            n_maybe = sum(1 for j in ranked if j["decision"] == "maybe")
            st.success(
                f"🎉 Done!  {len(all_jobs)} scraped · **{n_apply} to apply** · {n_maybe} maybe"
            )

        except Exception as e:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            st.error(f"❌ Error: {e}")
            st.exception(e)
        finally:
            st.session_state.scraping_in_progress = False
            st.session_state.scrape_lock_started_at = 0.0

    # if c3.button("📝 Generate docs", width="stretch"):
    #     ranked = _load_ranked()
    #     apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    #     if not apply_jobs:
    #         st.warning("No apply-decision jobs. Rank first.")
    #     else:
    #         cv_text = CV_PATH.read_text()
    #         with st.status(f"Generating docs for {len(apply_jobs)} jobs…", expanded=True) as status:
    #             try:
    #                 for job in apply_jobs:
    #                     status.write(f"Processing: {job['title']} @ {job['company']}")
    #                     r, c = _docs_exist(job)
    #                     if not r:
    #                         _tailor_and_convert(job, cv_text=cv_text, model=st.session_state.model)
    #                     if not c:
    #                         generate_cover_letter(job, model=st.session_state.model)
    #                 status.update(label="✅ Documents generated", state="complete")
    #             except Exception as e:
    #                 status.update(label=f"❌ Error: {e}", state="error")
    #                 st.exception(e)

    # if c4.button("🚀 Full pipeline", width="stretch", type="primary"):
    #     with st.status("Running full pipeline…", expanded=True) as status:
    #         try:
    #             # Step 1: Scrape
    #             status.write(f"**Step 1: Scraping {' + '.join(scrape_sources) or 'JobStreet'}…**")
    #             from job_scraper import scrape_all, load_keywords

    #             class _PipelineWriter(io.TextIOBase):
    #                 def write(self, s):
    #                     if s.strip():
    #                         status.write(s.strip())
    #                     return len(s)

    #             keywords = load_keywords()
    #             sys.stdout = _PipelineWriter()
    #             sys.stderr = io.StringIO()
    #             jobs = scrape_all(keywords, sources=selected_sources, fetch_full_descriptions=True)
    #             sys.stdout = sys.__stdout__
    #             sys.stderr = sys.__stderr__
    #             SCRAPED_PATH.parent.mkdir(parents=True, exist_ok=True)
    #             with open(SCRAPED_PATH, "w") as f:
    #                 json.dump(jobs, f, indent=2)
    #             status.write(f"✅ Scraped {len(jobs)} jobs")

    #             # Step 2: Rank
    #             status.write(f"**Step 2: Ranking {len(jobs)} jobs…**")
    #             from job_ranker import rank_all_jobs

    #             class _RankWriter(io.TextIOBase):
    #                 def write(self, s):
    #                     if s.strip():
    #                         status.write(s.strip())
    #                     return len(s)

    #             sys.stdout = _RankWriter()
    #             ranked = rank_all_jobs(jobs, model=st.session_state.model)
    #             sys.stdout = sys.__stdout__
    #             with open(RANKED_PATH, "w") as f:
    #                 json.dump(ranked, f, indent=2)
    #             _load_ranked.clear()
    #             n_apply = sum(1 for j in ranked if j["decision"] == "apply")
    #             status.write(f"✅ Ranked {len(ranked)} jobs — {n_apply} to apply")

    #             # Step 3: Generate docs
    #             apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    #             status.write(f"**Step 3: Generating docs for {len(apply_jobs)} apply jobs…**")
    #             cv_text = CV_PATH.read_text()
    #             for job in apply_jobs:
    #                 status.write(f"  {job['title']} @ {job['company']}")
    #                 r, c = _docs_exist(job)
    #                 if not r:
    #                     _tailor_and_convert(job, cv_text=cv_text, model=st.session_state.model)
    #                 if not c:
    #                     continue  # Skip cover letter generation in full pipeline for speed
    #             status.write("✅ Documents ready")

    #             status.update(label="🎉 Pipeline complete!", state="complete")
    #             st.balloons()

    #         except Exception as e:
    #             sys.stdout = sys.__stdout__
    #             status.update(label=f"❌ Failed: {e}", state="error")
    #             st.exception(e)

# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Archived Jobs
# ═══════════════════════════════════════════════════════════════════════════
elif page == "5. 📦 Archived Jobs":
    st.header("📦 Archived Jobs")
    st.caption(
        "All jobs from previous scrape & rank sessions. "
        "Archived automatically when a session ends."
    )

    archived = _load_archived()
    if not archived:
        st.info("No archived jobs yet. Jobs are archived automatically when your browser session ends.")
        st.stop()

    n_apply  = sum(1 for j in archived if j.get("decision") == "apply")
    n_maybe  = sum(1 for j in archived if j.get("decision") == "maybe")
    n_reject = sum(1 for j in archived if j.get("decision") == "reject")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Apply",  n_apply)
    c2.metric("🟡 Maybe",  n_maybe)
    c3.metric("🔴 Reject", n_reject)
    c4.metric("📋 Total",  len(archived))

    st.divider()

    tab_apply, tab_maybe, tab_all = st.tabs([
        f"🟢 Apply ({n_apply})",
        f"🟡 Maybe ({n_maybe})",
        f"📋 All ({len(archived)})",
    ])

    with tab_apply:
        _render_job_list([j for j in archived if j.get("decision") == "apply"], ns="arch_apply")
    with tab_maybe:
        _render_job_list([j for j in archived if j.get("decision") == "maybe"], ns="arch_maybe")
    with tab_all:
        _render_job_list(archived, ns="arch_all")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Dashboard
# ═══════════════════════════════════════════════════════════════════════════
elif page == "3. 📊 New Jobs Dashboard":
    st.header("📊 New Jobs Dashboard")

    ranked = _load_ranked()
    if not ranked:
        st.warning("No ranked jobs found. Setup Profile and do a New Job Search.")
        st.stop()

    n_apply  = sum(1 for j in ranked if j["decision"] == "apply")
    n_maybe  = sum(1 for j in ranked if j["decision"] == "maybe")
    n_reject = sum(1 for j in ranked if j["decision"] == "reject")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Apply",  n_apply)
    c2.metric("🟡 Maybe",  n_maybe)
    c3.metric("🔴 Reject", n_reject)
    c4.metric("📋 Total",  len(ranked))

    st.divider()

    # Filters
    col_f1, col_f2 = st.columns([2, 5])
    with col_f1:
        show = st.pills("Show", ["All", "Apply", "Maybe", "Reject"], default="All")
    with col_f2:
        search = st.text_input("Search title / company", placeholder="e.g. NUS, content writer…", label_visibility="collapsed")

    # Filter jobs
    filtered = ranked
    if show and show != "All":
        filtered = [j for j in filtered if j["decision"] == show.lower()]
    if search:
        q = search.lower()
        filtered = [j for j in filtered if q in j.get("title", "").lower() or q in j.get("company", "").lower()]

    st.caption(f"Showing {len(filtered)} of {len(ranked)} jobs")

    # Table
    rows = []
    for j in filtered:
        resume_path, cl_path = _docs_exist(j)
        db_status = st.session_state.job_overrides.get(j.get("url",""), "pending")
        rows.append({
            "Score": j.get("score", 0),
            "Decision": _badge(j["decision"]),
            "Title": j.get("title", ""),
            "Company": j.get("company", ""),
            "Docs": "✅" if (resume_path and cl_path) else "—",
            "Status": db_status,
            "URL": j.get("url", ""),
        })

    import pandas as pd
    df = pd.DataFrame(rows)
    st.dataframe(
        df.drop(columns=["URL"]),
        width="stretch",
        height=min(50 + 35 * len(rows), 600),
        hide_index=True,
    )


# # ═══════════════════════════════════════════════════════════════════════════
# # PAGE: Digest & Notify
# # ═══════════════════════════════════════════════════════════════════════════
# elif page == "📨 Digest & Notify":
#     st.header("📨 Digest & Notify")

#     ranked = _load_ranked()
#     if not ranked:
#         st.warning("No ranked jobs found. Run the pipeline first.")
#         st.stop()

#     # Summary stats
#     n_apply  = sum(1 for j in ranked if j["decision"] == "apply")
#     n_maybe  = sum(1 for j in ranked if j["decision"] == "maybe")
#     n_reject = sum(1 for j in ranked if j["decision"] == "reject")
#     c1, c2, c3, c4 = st.columns(4)
#     c1.metric("🟢 Apply",  n_apply)
#     c2.metric("🟡 Maybe",  n_maybe)
#     c3.metric("🔴 Reject", n_reject)
#     c4.metric("📋 Total",  len(ranked))

#     st.divider()

#     tab_preview, tab_email = st.tabs(["🖥️ Preview Digest", "✉️ Send Email"])

#     with tab_preview:
#         html = build_html(ranked)
#         st.download_button(
#             "⬇️ Download digest.html",
#             data=html,
#             file_name=f"digest_{date.today()}.html",
#             mime="text/html",
#         )
#         st.components.v1.html(html, height=700, scrolling=True)

#     with tab_email:
#         st.subheader("Email configuration")
#         st.caption("Uses Gmail SMTP by default. Create an App Password in your Google account settings.")

#         with st.form("email_form"):
#             to_addr   = st.text_input("To",        value="")
#             from_addr = st.text_input("From",      value="", placeholder="your.gmail@gmail.com")
#             smtp_user = st.text_input("SMTP user", value="", placeholder="your.gmail@gmail.com")
#             smtp_pass = st.text_input("App password", type="password")
#             smtp_host = st.text_input("SMTP host", value="smtp.gmail.com")
#             smtp_port = st.number_input("SMTP port", value=587, step=1)
#             submitted = st.form_submit_button("📨 Send digest email", type="primary")

#         if submitted:
#             if not smtp_user or not smtp_pass:
#                 st.error("SMTP user and app password are required.")
#             else:
#                 with st.spinner("Sending email…"):
#                     try:
#                         send_digest(
#                             ranked,
#                             send_email_flag=True,
#                             to=to_addr,
#                             smtp_host=smtp_host,
#                             smtp_port=int(smtp_port),
#                             smtp_user=smtp_user,
#                             smtp_pass=smtp_pass,
#                             from_addr=from_addr or smtp_user,
#                         )
#                         st.success(f"✅ Email sent to {to_addr}")
#                     except Exception as e:
#                         st.error(f"Failed to send: {e}")
