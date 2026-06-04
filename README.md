# 🎓 AI Job Seeker Agent — Tina

An end-to-end AI-powered job search pipeline with a Streamlit UI. Scrapes job listings from JobStreet and LinkedIn, ranks them against a candidate CV, then auto-generates tailored resumes and cover letters using an LLM.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [How Streamlit Interfaces with This Repo](#how-streamlit-interfaces-with-this-repo)
- [Where the Compute Comes From](#where-the-compute-comes-from)
- [LLM Routing — Ollama vs OpenAI](#llm-routing--ollama-vs-openai)
- [Deployment on Streamlit Community Cloud](#deployment-on-streamlit-community-cloud)
- [Limitations & Rate Limits](#limitations--rate-limits)
- [Local Setup](#local-setup)
- [Project Structure](#project-structure)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      YOUR MACHINE (Local Dev)                       │
│                                                                     │
│  Browser :8501 ──HTTP──► streamlit run app.py                       │
│                                │                                    │
│                         src/workflow.py  (orchestrator)             │
│                        ┌───────┴────────┐                           │
│              job_scraper.py        job_ranker.py                    │
│              (Playwright /         resume_tailor.py                 │
│               Chromium)            cover_letter.py                  │
│                  │                       │                          │
│           JobStreet.com            src/llm.py (router)              │
│           LinkedIn.com            ┌──────┴──────┐                   │
│                                   │             │                   │
│                             Ollama          OpenAI API              │
│                          localhost:11434   api.openai.com           │
│                         llama3.1 / mistral  gpt-4o-mini / gpt-4o   │
│                                                                     │
│                         ChromaDB  ──► data/chroma_db/ (disk)        │
│                         SQLite    ──► data/jobs.db    (disk)        │
│                         Files     ──► data/resumes/                 │
│                                       data/cover_letters/           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                   STREAMLIT COMMUNITY CLOUD (Deployed)              │
│                                                                     │
│  Browser (any URL) ──HTTPS──► streamlit run app.py                  │
│                                  (on Streamlit's Linux container)   │
│                                       │                             │
│                               src/workflow.py                       │
│                              ┌────────┴────────┐                    │
│                    job_scraper.py          src/llm.py               │
│                    (Playwright on Linux)        │                    │
│                          │               OpenAI API ◄── st.secrets  │
│                   JobStreet / LinkedIn    gpt-4o-mini               │
│                                                                     │
│                   ⚠️  Ollama NOT available on cloud                  │
│                   ⚠️  data/ files are EPHEMERAL (wiped on restart)  │
│                   ⚠️  ChromaDB re-ingested on every cold start      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## How Streamlit Interfaces with This Repo

Streamlit is a **Python web framework**, not a cloud AI platform. Running `streamlit run app.py` starts a local Python web server (default port `8501`) that serves a browser UI. Every button click or form submission calls back into your Python code synchronously.

`app.py` imports directly from `src/`:

```
app.py
 ├── src/workflow.py       — pipeline orchestrator
 ├── src/job_scraper.py    — Playwright web scraping
 ├── src/job_ranker.py     — LLM-based scoring
 ├── src/resume_tailor.py  — LLM-based resume generation
 ├── src/cover_letter.py   — LLM-based cover letter generation
 ├── src/llm.py            — LLM routing layer (Ollama / OpenAI)
 ├── src/vector_store.py   — ChromaDB wrapper
 ├── src/ingest.py         — CV ingestion into ChromaDB
 ├── src/notify.py         — Email digest builder
 └── src/db_schema.py      — SQLite schema initialisation
```

There is no API layer between the UI and the pipeline — Streamlit calls Python functions directly in the same process.

---

## Where the Compute Comes From

| Resource | Local (`streamlit run app.py`) | Streamlit Community Cloud |
|---|---|---|
| Python runtime | Your Mac's CPU / RAM | Streamlit's Linux container |
| Chromium / Playwright | Your Mac | Container (packages.txt installs browser deps) |
| ChromaDB | Persists on disk (`data/chroma_db/`) | **Ephemeral** — wiped on restart |
| `data/` files | Persists on disk | **Ephemeral** — wiped on restart |
| SQLite `jobs.db` | Persists on disk | **Ephemeral** — wiped on restart |
| LLM (Ollama) | localhost:11434 | ❌ Not available |
| LLM (OpenAI) | Your OpenAI subscription | Your OpenAI subscription (via `st.secrets`) |

Streamlit Community Cloud provides **1 vCPU + 1 GB RAM** on the free tier. The Playwright scrape is the heaviest step — it spawns a full Chromium browser process that alone consumes ~300–500 MB RAM.

---

## LLM Routing — Ollama vs OpenAI

`src/llm.py` contains an explicit routing layer:

```
Is OPENAI_API_KEY present?
    ├── YES → use OpenAI API (your subscription, your quota)
    │         • gpt-4o-mini / gpt-4o if selected
    │         • Ollama model selected but no Ollama server? → silently
    │           falls back to gpt-4o-mini
    └── NO  → use local Ollama (localhost:11434)
```

**Key point:** On Streamlit Community Cloud, Ollama is completely unavailable. Even if you select `llama3.1:8b` in the UI dropdown, `llm.py` detects the OpenAI key in `st.secrets` and routes to `gpt-4o-mini` instead. **All LLM calls on Streamlit Cloud use your OpenAI subscription.**

API key lookup order (from `src/llm.py`):
1. `OPENAI_API_KEY` environment variable
2. `st.secrets["OPENAI_API_KEY"]` (Streamlit Cloud secrets)

---

## Deployment on Streamlit Community Cloud

1. Push this repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Select your repo, branch, and `app.py` as the entry point.
4. Under **Advanced settings → Secrets**, add:
   ```toml
   OPENAI_API_KEY = "sk-..."
   ```
5. Streamlit installs `requirements.txt` (Python packages) and `packages.txt` (Linux system packages — Playwright browser dependencies).
6. On first load, `_ensure_playwright_browsers()` in `job_scraper.py` runs `playwright install chromium` to download the browser binary into the container.
7. The auto-ingest block in the sidebar re-indexes CV data into ChromaDB on every cold start (since disk is ephemeral).

> **Note:** The free tier app sleeps after ~7 days of inactivity. The next visitor will experience a cold-start delay of 30–60 seconds.

---

## Limitations & Rate Limits

### Job Scraping (Playwright)

| Limitation | Detail |
|---|---|
| **Anti-bot detection** | JobStreet and LinkedIn actively detect headless Chromium. The scraper uses a spoofed user-agent and removes `navigator.webdriver` — but this can break when sites update their detection logic. |
| **LinkedIn aggressiveness** | LinkedIn is particularly aggressive. Repeated scraping can trigger CAPTCHAs, temporary IP bans, or account flags if logged in. |
| **Cloud IP reputation** | Streamlit Cloud container IPs are shared and well-known. Job boards block or CAPTCHA-challenge them more aggressively than residential IPs. |
| **No pagination** | `max_pages=1` — only the first results page per keyword is scraped. |
| **Hard cap** | `scrape_jobstreet()` returns at most 40 jobs regardless of how many are found. |
| **RAM on free tier** | Scraping 50+ keywords × 2 sources with full description fetches can take 10–30 minutes and may hit the 1 GB RAM container limit, killing the process. |
| **`time.sleep()` delays** | 0.5–2 second polite delays are built in per job. These are necessary to avoid triggering rate limits but significantly increase total runtime. |

### OpenAI Rate Limits

| Tier | Requests/min | Tokens/min |
|---|---|---|
| Free / Tier 1 | 500 RPM | 200K TPM |
| Tier 2 | 5,000 RPM | 2M TPM |

The pipeline calls OpenAI once per job for **ranking**, then once each for **resume tailoring** and **cover letter generation** for every `apply` decision. For 100 jobs with 30 `apply` decisions that is ~160 API calls in quick succession. On Tier 1 you will hit RPM limits mid-pipeline. The current code does not implement retry/backoff for `RateLimitError`.

### Streamlit Community Cloud

| Limitation | Detail |
|---|---|
| **RAM** | 1 GB free tier — Playwright + ChromaDB ONNX embedder together can exceed this |
| **Ephemeral filesystem** | All files in `data/` (scraped jobs, SQLite DB, resumes, cover letters) are deleted on every restart or redeploy |
| **No persistent background tasks** | The app only runs while a browser session is active; long scraping jobs killed if session times out |
| **App sleep** | Free apps sleep after ~7 days of no traffic |
| **No Ollama** | Cannot run local models — everything routes to OpenAI |
| **No persistent DB** | For production use, replace `data/jobs.db` and `data/*.json` with an external store (Supabase, S3, Postgres) |

---

## Local Setup

```bash
# Clone and enter the repo
git clone <your-repo-url>
cd ai_job_seeker_agent

# Create and activate a virtual environment
python3 -m venv tina-agent
source tina-agent/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# (Optional) Install and start Ollama for local LLM inference
# https://ollama.com — then pull a model:
ollama pull llama3.1:8b

# Run the app
streamlit run app.py
```

To use OpenAI locally, set the environment variable before running:

```bash
export OPENAI_API_KEY="sk-..."
streamlit run app.py
```

To run the pipeline headlessly (without the UI):

```bash
python3 src/workflow.py                    # full run
python3 src/workflow.py --skip-scrape      # rank + generate from existing scraped_jobs.json
python3 src/workflow.py --apply-only       # only generate docs for existing apply decisions
python3 src/workflow.py --model mistral:7b # use a different Ollama model
```

---

## Project Structure

```
ai_job_seeker_agent/
├── app.py                         # Streamlit UI entry point
├── requirements.txt               # Python dependencies
├── packages.txt                   # Linux system packages (Playwright browser deps)
├── src/
│   ├── workflow.py                # End-to-end pipeline orchestrator
│   ├── job_scraper.py             # Playwright scraper — JobStreet + LinkedIn
│   ├── job_ranker.py              # LLM-based job scoring and ranking
│   ├── resume_tailor.py           # LLM-based resume tailoring
│   ├── cover_letter.py            # LLM-based cover letter generation
│   ├── llm.py                     # LLM routing layer (Ollama / OpenAI)
│   ├── vector_store.py            # ChromaDB wrapper
│   ├── ingest.py                  # CV ingestion into ChromaDB
│   ├── notify.py                  # Email digest builder
│   ├── apply_assistant.py         # Application helper
│   └── db_schema.py               # SQLite schema initialisation
└── data/
    ├── tina_cv.txt                # Candidate CV (plain text)
    ├── tina_job_preferences.json  # Job preferences config
    ├── tina_academia_roles.json   # Academic role keywords
    ├── tina_industry_roles.json   # Industry role keywords
    ├── tina_sg_work_visa_note.txt # Work visa context for LLM prompts
    ├── scraped_jobs.json          # Raw scrape output
    ├── ranked_jobs.json           # Scored and ranked jobs
    ├── jobs.db                    # SQLite application tracker
    ├── resumes/                   # Tailored resumes (per job)
    ├── cover_letters/             # Generated cover letters (per job)
    ├── applications/              # Application records
    └── chroma_db/                 # ChromaDB vector store (CV embeddings)
```
