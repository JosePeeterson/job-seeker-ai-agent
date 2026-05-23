"""
AI Job Seeker Agent — Streamlit UI
Run with:  streamlit run app.py
"""

import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from typing import Tuple

import streamlit as st

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from db_schema import init_db
from cover_letter import generate_cover_letter
from notify import build_html, send_digest
from resume_tailor import tailor_resume, _slug

DATA_DIR = ROOT / "data"
RANKED_PATH = DATA_DIR / "ranked_jobs.json"
SCRAPED_PATH = DATA_DIR / "scraped_jobs.json"
CV_PATH = DATA_DIR / "tina_cv.txt"
DB_PATH = DATA_DIR / "jobs.db"
RESUMES_DIR = DATA_DIR / "resumes"
COVER_LETTERS_DIR = DATA_DIR / "cover_letters"

MODELS = ["gpt-4o-mini", "gpt-4o", "llama3.1:8b", "mistral:7b", "qwen3:8b"]

# ── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Job Seeker — Tina",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Helpers ─────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_ranked() -> list:
    if RANKED_PATH.exists():
        with open(RANKED_PATH) as f:
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


def _get_db_status(url: str) -> str:
    """Look up application status for a job URL from SQLite."""
    if not DB_PATH.exists():
        return "pending"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT status FROM jobs WHERE url = ?", (url,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else "pending"
    except Exception:
        return "pending"


def _db_upsert_and_apply(job: dict, resume_path: str, cl_path: str):
    init_db(str(DB_PATH))
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id FROM jobs WHERE url = ?", (job.get("url", ""),))
    row = cur.fetchone()
    if row:
        job_id = row[0]
        cur.execute(
            "UPDATE jobs SET score=?, status=?, tailored_resume_path=?, cover_letter_path=? WHERE id=?",
            (job.get("score", 0), "applied", resume_path, cl_path, job_id),
        )
    else:
        cur.execute(
            """INSERT INTO jobs (title, company, url, description, score, status,
               tailored_resume_path, cover_letter_path, application_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.get("title", ""),
                job.get("company", ""),
                job.get("url", ""),
                job.get("description", "")[:2000],
                job.get("score", 0),
                "applied",
                resume_path,
                cl_path,
                str(date.today()),
            ),
        )
        job_id = cur.lastrowid
    cur.execute(
        "INSERT INTO applications (job_id, applied_on, status, notes) VALUES (?, ?, ?, ?)",
        (job_id, str(date.today()), "applied", ""),
    )
    conn.commit()
    conn.close()


def _db_mark_skipped(job: dict):
    init_db(str(DB_PATH))
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id FROM jobs WHERE url = ?", (job.get("url", ""),))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE jobs SET status=? WHERE id=?", ("skipped", row[0]))
    else:
        cur.execute(
            "INSERT INTO jobs (title, company, url, score, status, application_date) VALUES (?,?,?,?,?,?)",
            (job.get("title",""), job.get("company",""), job.get("url",""),
             job.get("score",0), "skipped", str(date.today())),
        )
    conn.commit()
    conn.close()


# ── Session state defaults ───────────────────────────────────────────────────
if "model" not in st.session_state:
    st.session_state.model = MODELS[0]
if "pipeline_log" not in st.session_state:
    st.session_state.pipeline_log = ""
if "job_overrides" not in st.session_state:
    # URL -> "applied" | "skipped" | "pending" (in-session overrides)
    st.session_state.job_overrides = {}


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎓Tina's AI Job Seeker agent")
    st.caption("Powered by OpenAI / Ollama + ChromaDB")
    st.divider()

    page = st.radio(
        "Navigate",
        ["📊 Dashboard", "🔍 Review Jobs", "⚙️ Run Pipeline", "📨 Digest & Notify"],
        label_visibility="collapsed",
    )
    st.divider()

    st.session_state.model = st.selectbox("LLM model", MODELS, index=MODELS.index(st.session_state.model))

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


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Dashboard
# ═══════════════════════════════════════════════════════════════════════════
if page == "📊 Dashboard":
    st.header("📊 Job Dashboard")

    ranked = _load_ranked()
    if not ranked:
        st.warning("No ranked jobs found. Run the pipeline first.")
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
        db_status = st.session_state.job_overrides.get(j.get("url",""), _get_db_status(j.get("url","")))
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


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Review Jobs
# ═══════════════════════════════════════════════════════════════════════════
elif page == "🔍 Review Jobs":
    st.header("🔍 Review & Apply")

    ranked = _load_ranked()
    if not ranked:
        st.warning("No ranked jobs found. Run the pipeline first.")
        st.stop()

    tab_apply, tab_maybe, tab_all = st.tabs([
        f"🟢 Apply ({sum(1 for j in ranked if j['decision']=='apply')})",
        f"🟡 Maybe ({sum(1 for j in ranked if j['decision']=='maybe')})",
        f"📋 All ({len(ranked)})",
    ])

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
            db_status = st.session_state.job_overrides.get(url, _get_db_status(url))

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
                        dl1, dl2 = st.columns(2)
                        dl1.download_button(
                            "📄 Download Resume",
                            data=Path(resume_path).read_text(),
                            file_name=Path(resume_path).name,
                            mime="text/plain",
                            key=f"dl_resume_{k}",
                        )
                        dl2.download_button(
                            "📧 Download Cover Letter",
                            data=Path(cl_path).read_text(),
                            file_name=Path(cl_path).name,
                            mime="text/plain",
                            key=f"dl_cl_{k}",
                        )
                    else:
                        missing = []
                        if not resume_path:
                            missing.append("resume")
                        if not cl_path:
                            missing.append("cover letter")
                        st.warning(f"No {' or '.join(missing)} yet.")
                        if st.button("⚡ Generate Docs", key=f"gen_{k}"):
                            with st.spinner("Generating tailored resume and cover letter…"):
                                try:
                                    tailored = tailor_resume(job, cv_text=cv_text, model=st.session_state.model)
                                    generate_cover_letter(job, tailored_resume=tailored, model=st.session_state.model)
                                    st.success("Documents generated!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")

                with action_col:
                    if db_status == "applied":
                        st.success("✅ Marked as Applied")
                    elif db_status == "skipped":
                        st.error("⏭️ Skipped")
                    else:
                        b1, b2 = st.columns(2)
                        if b1.button("✅ Applied", key=f"apply_{k}", type="primary"):
                            r_path, c_path = _docs_exist(job)
                            _db_upsert_and_apply(job, r_path, c_path)
                            st.session_state.job_overrides[url] = "applied"
                            st.rerun()
                        if b2.button("⏭️ Skip", key=f"skip_{k}"):
                            _db_mark_skipped(job)
                            st.session_state.job_overrides[url] = "skipped"
                            st.rerun()

    with tab_apply:
        _render_job_list([j for j in ranked if j["decision"] == "apply"], ns="apply")
    with tab_maybe:
        _render_job_list([j for j in ranked if j["decision"] == "maybe"], ns="maybe")
    with tab_all:
        _render_job_list(ranked, ns="all")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Run Pipeline
# ═══════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Run Pipeline":
    st.header("⚙️ Run Pipeline")

    col_opts, col_info = st.columns([2, 3])

    with col_opts:
        st.subheader("Options")
        scrape_sources = st.multiselect(
            "Job sources",
            ["JobStreet", "LinkedIn"],
            default=["JobStreet", "LinkedIn"],
            help="Which sites to scrape for job listings",
        )
        _source_map = {"JobStreet": "jobstreet", "LinkedIn": "linkedin"}
        selected_sources = [_source_map[s] for s in scrape_sources] or ["jobstreet"]
        skip_scrape = st.checkbox(
            "Skip scrape (use existing scraped_jobs.json)",
            value=SCRAPED_PATH.exists(),
            help="Uncheck to re-scrape (slower)",
        )
        skip_rank = st.checkbox(
            "Skip ranking (use existing ranked_jobs.json)",
            value=False,
            help="Only generate documents for already-ranked jobs",
        )
        gen_docs = st.checkbox("Generate docs for apply candidates", value=True)

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

    if c1.button("🕷️ Scrape only", width="stretch"):
        src_label = " + ".join(scrape_sources) or "JobStreet"
        with st.status(f"Scraping {src_label}…", expanded=True) as status:
            buf = io.StringIO()
            try:
                sys.stdout = buf
                from job_scraper import scrape_all, load_keywords
                keywords = load_keywords()
                st.write(f"Sources: {src_label}  |  Keywords: {len(keywords)}")
                jobs = scrape_all(keywords, sources=selected_sources, fetch_full_descriptions=True)
                with open(SCRAPED_PATH, "w") as f:
                    json.dump(jobs, f, indent=2)
                sys.stdout = sys.__stdout__
                status.update(label=f"✅ Scraped {len(jobs)} jobs", state="complete")
                st.write(f"Saved {len(jobs)} jobs to scraped_jobs.json")
            except Exception as e:
                sys.stdout = sys.__stdout__
                status.update(label=f"❌ Error: {e}", state="error")

    if c2.button("🏆 Rank only", width="stretch"):
        if not SCRAPED_PATH.exists():
            st.error("No scraped_jobs.json found. Run scrape first.")
        else:
            with open(SCRAPED_PATH) as f:
                jobs = json.load(f)
            with st.status(f"Ranking {len(jobs)} jobs…", expanded=True) as status:
                try:
                    from job_ranker import rank_all_jobs, print_summary

                    class _Writer(io.TextIOBase):
                        def write(self, s):
                            if s.strip():
                                status.write(s)
                            return len(s)

                    sys.stdout = _Writer()
                    ranked = rank_all_jobs(jobs, model=st.session_state.model)
                    sys.stdout = sys.__stdout__
                    with open(RANKED_PATH, "w") as f:
                        json.dump(ranked, f, indent=2)
                    _load_ranked.clear()
                    n_apply = sum(1 for j in ranked if j["decision"] == "apply")
                    status.update(label=f"✅ Ranked {len(ranked)} jobs — {n_apply} to apply", state="complete")
                except Exception as e:
                    sys.stdout = sys.__stdout__
                    status.update(label=f"❌ Error: {e}", state="error")
                    st.exception(e)

    if c3.button("📝 Generate docs", width="stretch"):
        ranked = _load_ranked()
        apply_jobs = [j for j in ranked if j["decision"] == "apply"]
        if not apply_jobs:
            st.warning("No apply-decision jobs. Rank first.")
        else:
            cv_text = CV_PATH.read_text()
            with st.status(f"Generating docs for {len(apply_jobs)} jobs…", expanded=True) as status:
                try:
                    for job in apply_jobs:
                        status.write(f"Processing: {job['title']} @ {job['company']}")
                        r, c = _docs_exist(job)
                        if not r:
                            tailor_resume(job, cv_text=cv_text, model=st.session_state.model)
                        if not c:
                            generate_cover_letter(job, model=st.session_state.model)
                    status.update(label="✅ Documents generated", state="complete")
                except Exception as e:
                    status.update(label=f"❌ Error: {e}", state="error")
                    st.exception(e)

    if c4.button("🚀 Full pipeline", width="stretch", type="primary"):
        with st.status("Running full pipeline…", expanded=True) as status:
            try:
                # Step 1: Scrape
                if not skip_scrape:
                    src_label = " + ".join(scrape_sources) or "JobStreet"
                    status.write(f"**Step 1: Scraping {src_label}…**")
                    from job_scraper import scrape_all, load_keywords
                    keywords = load_keywords()
                    jobs = scrape_all(keywords, sources=selected_sources, fetch_full_descriptions=True)
                    with open(SCRAPED_PATH, "w") as f:
                        json.dump(jobs, f, indent=2)
                    status.write(f"✅ Scraped {len(jobs)} jobs")
                else:
                    with open(SCRAPED_PATH) as f:
                        jobs = json.load(f)
                    status.write(f"⏭️ Using {len(jobs)} existing scraped jobs")

                # Step 2: Rank
                if not skip_rank:
                    status.write(f"**Step 2: Ranking {len(jobs)} jobs…**")
                    from job_ranker import rank_all_jobs

                    class _Writer(io.TextIOBase):
                        def write(self, s):
                            if s.strip():
                                status.write(s)
                            return len(s)

                    sys.stdout = _Writer()
                    ranked = rank_all_jobs(jobs, model=st.session_state.model)
                    sys.stdout = sys.__stdout__
                    with open(RANKED_PATH, "w") as f:
                        json.dump(ranked, f, indent=2)
                    _load_ranked.clear()
                    n_apply = sum(1 for j in ranked if j["decision"] == "apply")
                    status.write(f"✅ Ranked {len(ranked)} jobs — {n_apply} to apply")
                else:
                    ranked = _load_ranked()
                    status.write(f"⏭️ Using existing rankings ({len(ranked)} jobs)")

                # Step 3: Generate docs
                if gen_docs:
                    apply_jobs = [j for j in ranked if j["decision"] == "apply"]
                    status.write(f"**Step 3: Generating docs for {len(apply_jobs)} apply jobs…**")
                    cv_text = CV_PATH.read_text()
                    for job in apply_jobs:
                        status.write(f"  {job['title']} @ {job['company']}")
                        r, c = _docs_exist(job)
                        if not r:
                            tailor_resume(job, cv_text=cv_text, model=st.session_state.model)
                        if not c:
                            generate_cover_letter(job, model=st.session_state.model)
                    status.write("✅ Documents ready")

                status.update(label="🎉 Pipeline complete!", state="complete")
                st.balloons()

            except Exception as e:
                sys.stdout = sys.__stdout__
                status.update(label=f"❌ Failed: {e}", state="error")
                st.exception(e)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: Digest & Notify
# ═══════════════════════════════════════════════════════════════════════════
elif page == "📨 Digest & Notify":
    st.header("📨 Digest & Notify")

    ranked = _load_ranked()
    if not ranked:
        st.warning("No ranked jobs found. Run the pipeline first.")
        st.stop()

    # Summary stats
    n_apply  = sum(1 for j in ranked if j["decision"] == "apply")
    n_maybe  = sum(1 for j in ranked if j["decision"] == "maybe")
    n_reject = sum(1 for j in ranked if j["decision"] == "reject")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Apply",  n_apply)
    c2.metric("🟡 Maybe",  n_maybe)
    c3.metric("🔴 Reject", n_reject)
    c4.metric("📋 Total",  len(ranked))

    st.divider()

    tab_preview, tab_email = st.tabs(["🖥️ Preview Digest", "✉️ Send Email"])

    with tab_preview:
        html = build_html(ranked)
        st.download_button(
            "⬇️ Download digest.html",
            data=html,
            file_name=f"digest_{date.today()}.html",
            mime="text/html",
        )
        st.components.v1.html(html, height=700, scrolling=True)

    with tab_email:
        st.subheader("Email configuration")
        st.caption("Uses Gmail SMTP by default. Create an App Password in your Google account settings.")

        with st.form("email_form"):
            to_addr   = st.text_input("To",        value="tinaarshara@gmail.com")
            from_addr = st.text_input("From",      value="", placeholder="your.gmail@gmail.com")
            smtp_user = st.text_input("SMTP user", value="", placeholder="your.gmail@gmail.com")
            smtp_pass = st.text_input("App password", type="password")
            smtp_host = st.text_input("SMTP host", value="smtp.gmail.com")
            smtp_port = st.number_input("SMTP port", value=587, step=1)
            submitted = st.form_submit_button("📨 Send digest email", type="primary")

        if submitted:
            if not smtp_user or not smtp_pass:
                st.error("SMTP user and app password are required.")
            else:
                with st.spinner("Sending email…"):
                    try:
                        send_digest(
                            ranked,
                            send_email_flag=True,
                            to=to_addr,
                            smtp_host=smtp_host,
                            smtp_port=int(smtp_port),
                            smtp_user=smtp_user,
                            smtp_pass=smtp_pass,
                            from_addr=from_addr or smtp_user,
                        )
                        st.success(f"✅ Email sent to {to_addr}")
                    except Exception as e:
                        st.error(f"Failed to send: {e}")
