"""
Job ranking pipeline.

Flow per job:
  job description
    → extract_requirements()   (Ollama LLM)
    → query cv_chunks + preferred_roles (ChromaDB)
    → score_fit()              (Ollama LLM)
    → decide()                 apply / maybe / reject
    → store job in job_descriptions collection (ChromaDB)

Usage:
    from job_ranker import rank_job, rank_all_jobs

    ranked = rank_all_jobs(jobs)          # jobs = list of dicts from job_scraper
    for j in ranked:
        print(j["score"], j["decision"], j["title"])
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vector_store import query_collection, ingest_document, collection_count
from llm import chat as _llm_chat

DEFAULT_MODEL = "llama3.1:8b"


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _chat(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Call the configured LLM (OpenAI on cloud, Ollama locally) and return the response text."""
    return _llm_chat(prompt, model, temperature=0.0)


def _parse_json(raw: str) -> dict:
    """Extract and parse the first JSON object found in raw text."""
    import re
    # Try to find a JSON object anywhere in the response (handles prose + fences)
    # First try: extract between outermost { }
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # Fallback: strip leading prose and markdown fences then parse
    text = raw.strip()
    for fence in ("```json", "```"):
        idx = text.find(fence)
        if idx != -1:
            text = text[idx + len(fence):]
            end = text.find("```")
            if end != -1:
                text = text[:end]
            break
    return json.loads(text.strip())


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def extract_requirements(job_description: str, model: str = DEFAULT_MODEL) -> dict:
    """
    Use LLM to pull structured requirements from a job description.

    Returns dict with keys:
      must_have, nice_to_have, responsibilities, keywords
    """
    prompt = f"""Extract the job requirements from the job description below.
Return ONLY a valid JSON object with these exact keys:
- "must_have": list of non-negotiable qualifications, skills, or experience
- "nice_to_have": list of preferred but optional requirements
- "responsibilities": list of key duties
- "keywords": list of domain/skill keywords (5-10 words)

Job description:
\"\"\"
{job_description[:5000]}
\"\"\"
Return only the JSON object. No explanation."""
    
    raw = _chat(prompt, model)
    try:
        return _parse_json(raw)
    except json.JSONDecodeError:
        # Fallback: return empty structure so pipeline continues
        print(f"[WARN] Could not parse requirements JSON. Raw: {raw[:200]}")
        return {
            "must_have": [],
            "nice_to_have": [],
            "responsibilities": [],
            "keywords": [],
        }


def score_fit(
    job_title: str,
    requirements: dict,
    cv_chunks: list,
    preferred_role_chunks: list,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Score Tina's fit (0-100) against extracted requirements.

    Returns dict with keys:
      score, strengths, gaps, explanation
    """
    cv_context = "\n---\n".join(cv_chunks) if cv_chunks else "(no CV data)"
    role_context = "\n---\n".join(preferred_role_chunks) if preferred_role_chunks else ""
    req_text = json.dumps(requirements, indent=2)

    prompt = f"""You are a career counsellor evaluating whether an academic candidate fits a job.

IMPORTANT CONTEXT about the candidate:
- She has a PhD (submitted) and MA in English Literature — this counts as a teaching qualification for subjects she actually knows
- Her domain expertise is STRICTLY: English Literature, Digital Humanities, Posthumanism, Cyberpunk/Speculative Fiction, Media Studies, Communications, Academic Writing, Critical Theory, Content Writing, Narrative/Textual Analysis
- PhD-level research and publications count as teaching readiness ONLY for subjects within her domain
- She is based in Singapore on a Dependent Pass (spouse of a Singapore Permanent Resident (PR)) and eligible to work
- Do NOT penalise her for lacking formal years of employment if her PhD research fills that role
- She can only speak, read, and write in English and Tamil but not other languages

HARD DOMAIN RULES — apply these strictly before scoring:
- Score 0-25 if the role requires teaching or expertise in a technical field completely outside her background: Programming/Computer Science (Python, C++, Java, algorithms), Electrical/Mechanical/Aerospace Engineering, Finance/Investment/Accounting, Clinical Psychology/Medicine/Healthcare — she has NO background in these
- Score 25-45 if the role is primarily Business/Management/Economics with no media, communication, or humanities component
- Score 45-70 for roles with a mix of business/management AND communication/media/digital elements where her research is partially transferable
- Score 70-100 for roles squarely in her domain: English/Humanities/Literature, Media & Communications, Content Writing/Editorial, Digital Humanities, interdisciplinary cultural/social studies, general educator roles (no fixed subject), or any role where strong academic writing and research are the primary requirements

Job title: {job_title}

Job requirements:
{req_text}

Candidate CV (relevant excerpts):
{cv_context}

Candidate preferred role profile:
{role_context}

Score the fit from 0 to 100 using these bands:
- 70-100: Strong match — clearly qualified with direct or highly transferable background, apply immediately
- 45-69: Partial match — meaningful transferable skills and background, worth applying with a tailored CV
- 25-44: Weak match — noticeable gaps but some relevance, apply only if desperate
- 0-24: Poor match — domain mismatch (e.g. engineering, finance, CS with no connection to humanities)

Return ONLY a valid JSON object with these exact keys:
- "score": integer 0-100
- "strengths": list of 3-5 specific matching points (be specific, cite CV evidence)
- "gaps": list of specific missing requirements or genuine weaknesses
- "explanation": 2-3 sentence human-readable summary for Tina

No explanation outside the JSON."""
    
    raw = _chat(prompt, model)
    try:
        result = _parse_json(raw)
        result["score"] = int(result.get("score", 0))
        return result
    except (json.JSONDecodeError, ValueError):
        print(f"[WARN] Could not parse score JSON. Raw: {raw[:200]}")
        return {
            "score": 0,
            "strengths": [],
            "gaps": ["Could not evaluate — LLM parse error"],
            "explanation": "Scoring failed due to a parsing error.",
        }


def decide(score: int) -> str:
    """Convert a numeric score to a decision label."""
    if score >= 70:
        return "apply"
    if score >= 45:
        return "maybe"
    return "reject"


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def rank_job(job: dict, model: str = DEFAULT_MODEL) -> dict:
    """
    Run the full ranking pipeline for a single job dict.

    Input job dict (from job_scraper):
      title, company, url, description, matched_keyword

    Returns the job dict enriched with:
      score, decision, requirements, ranking_details
    """
    title = job.get("title", "Unknown")
    company = job.get("company", "")
    job_desc = job.get("description", "")

    print(f"\n[Ranker] ── {title} @ {company}")

    # Guard: warn if CV is not ingested
    if collection_count("cv_chunks") == 0:
        print("[WARN] cv_chunks collection is empty. Run: python3 src/ingest.py")
        return {
            **job,
            "score": 0,
            "decision": "reject",
            "requirements": {},
            "ranking_details": {"explanation": "CV not ingested yet."},
        }

    # Step 1 — Extract requirements from the job description
    try:
        requirements = extract_requirements(job_desc, model)
    except Exception as exc:
        print(f"[ERROR] extract_requirements failed for '{title}': {exc}")
        print(f"if connection error then run below command to start local LLM server:")
        print(f"RUN 'ollama serve &>/tmp/ollama.log & sleep 3 && ollama list'\
               to start the local LLM server if not already running.")
        return {
            **job,
            "score": 0,
            "decision": "reject",
            "requirements": {},
            "ranking_details": {"explanation": f"LLM error during requirement extraction: {exc}"},
        }
    keywords = requirements.get("keywords", [])
    print(f"  Keywords extracted: {keywords}")

    # Step 2 — Semantic search: find relevant CV excerpts
    cv_query = f"{title} {' '.join(keywords)}"
    cv_chunks = query_collection("cv_chunks", cv_query, n_results=5)

    # Step 3 — Semantic search: find matching preferred roles
    role_chunks = query_collection("preferred_roles", cv_query, n_results=3)

    # Step 4 — Score fit using LLM
    try:
        details = score_fit(title, requirements, cv_chunks, role_chunks, model)
    except Exception as exc:
        print(f"[ERROR] score_fit failed for '{title}': {exc}")
        return {
            **job,
            "score": 0,
            "decision": "reject",
            "requirements": requirements,
            "ranking_details": {"explanation": f"LLM error during scoring: {exc}"},
        }
    score = details["score"]
    decision = decide(score)

    print(f"  Score: {score}/100  →  Decision: {decision.upper()}")
    if details.get("gaps"):
        print(f"  Gaps:  {'; '.join(details['gaps'][:2])}")

    # Step 5 — Store job description in ChromaDB for future reference
    safe_id = f"jd_{job.get('url', title)}"[:200].replace(" ", "_").replace("/", "_")
    ingest_document(
        "job_descriptions",
        job_desc,
        doc_id=safe_id,
        metadata={
            "title": title,
            "company": company,
            "score": score,
            "decision": decision,
        },
    )

    return {
        **job,
        "score": score,
        "decision": decision,
        "requirements": requirements,
        "ranking_details": details,
    }


def rank_all_jobs(jobs: list, model: str = DEFAULT_MODEL) -> list:
    """
    Rank a list of jobs. Returns the list sorted by score descending.
    Jobs with decision='reject' are included but appear last.
    """
    ranked = []
    for job in jobs:
        try:
            ranked.append(rank_job(job, model=model))
        except Exception as exc:
            title = job.get("title", "Unknown")
            print(f"[ERROR] rank_job failed for '{title}': {exc}")
            ranked.append({
                **job,
                "score": 0,
                "decision": "reject",
                "requirements": {},
                "ranking_details": {"explanation": f"Ranking error: {exc}"},
            })
    ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
    return ranked


def print_summary(ranked_jobs: list) -> None:
    """Print a human-readable table of ranked results."""
    print("\n" + "=" * 70)
    print(f"{'#':>3}  {'Score':>5}  {'Decision':<8}  {'Title':<40}  Company")
    print("-" * 70)
    for i, job in enumerate(ranked_jobs, 1):
        decision = job.get("decision", "?")
        score = job.get("score", 0)
        title = job.get("title", "")[:40]
        company = job.get("company", "")[:40]
        flag = {"apply": "✓", "maybe": "~", "reject": "✗"}.get(decision, "?")
        print(f"{i:>3}  {score:>5}  {flag} {decision:<6}  {title:<40}  {company}")
    print("=" * 70)


if __name__ == "__main__":
    import json
    from pathlib import Path

    DATA_DIR = Path(__file__).parent.parent / "data"
    jobs_path = DATA_DIR / "scraped_jobs" / "scraped_jobs.json"

    if not jobs_path.exists():
        print(f"[ERROR] {jobs_path} not found. Run job_scraper.py first.")
        sys.exit(1)

    with open(jobs_path) as f:
        jobs = json.load(f)

    if not jobs:
        print("[ERROR] scraped_jobs.json is empty.")
        sys.exit(1)

    print(f"[INFO] Ranking {len(jobs)} jobs...")
    model = DEFAULT_MODEL
    if len(sys.argv) > 1:
        model = sys.argv[1]
    print(f"[INFO] Using model: {model}")

    ranked = rank_all_jobs(jobs, model=model)
    print_summary(ranked)

    out_path = DATA_DIR / "ranked_jobs" / "ranked_jobs.json"
    with open(out_path, "w") as f:
        json.dump(ranked, f, indent=2)
    print(f"\n[INFO] Results saved to {out_path}")
