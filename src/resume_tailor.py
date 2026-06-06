"""
Resume tailoring module.

Takes a ranked job dict + the user's full CV text and produces a tailored
resume (plain text) that highlights the most relevant experience and
maps their background to the specific job requirements.

Usage:
    from resume_tailor import tailor_resume

    tailored = tailor_resume(job, cv_text, model="llama3.1:8b")
    # returns a plain-text tailored resume string
    # also saves to data/resumes/<slug>.txt
"""

from pathlib import Path
import sys
import re

sys.path.insert(0, str(Path(__file__).parent))
from llm import chat as _llm_chat

DATA_DIR = Path(__file__).parent.parent / "data"
RESUMES_DIR = DATA_DIR / "resumes"
CV_PATH = DATA_DIR / "user_cv" / "user_cv.txt"

DEFAULT_MODEL = "llama3.1:8b"


def _slug(title: str, company: str) -> str:
    raw = f"{title}_{company}".lower()
    return re.sub(r"[^a-z0-9]+", "_", raw)[:80].strip("_")


def tailor_resume(job: dict, cv_text: str = None, model: str = DEFAULT_MODEL) -> str:
    """
    Generate a tailored resume for the given job.

    Returns the tailored resume as a plain-text string and saves it
    to data/resumes/<slug>.txt.
    """
    if cv_text is None:
        cv_text = CV_PATH.read_text()

    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    description = job.get("description", "")
    requirements = job.get("requirements", {})
    req_text = ""
    if requirements:
        import json
        req_text = json.dumps(requirements, indent=2)
    else:
        req_text = description[:3000]

    prompt = f"""You are a professional CV writer helping a candidate apply for a job.

Target role: {title} at {company}

Job requirements:
{req_text}

Candidate's original CV:
{cv_text}

Task: Rewrite the CV to be tailored for this specific role.
- Keep all factual information accurate — do NOT invent personal details,experience or qualifications
- The first few lines in the original CV contains personal details like name and contact info. Keep these in the tailored CV but do not add any heading like "Professional Summary" or "Personal Details" to them.
- The very top should have personal details (name, contact info) and not a heading like "Professional Summary"
- Do not include any Professional Summary that is customized for this role
- Must create relevant sections such as education, experience, skills, projects etc. in the tailored CV.
- seperate the sections with horizontal lines made of dashes (e.g. "──────") to improve readability, but do not add any section heading or title for the personal details at the top.
- Reframe sections to emphasise what is most relevant to this role
- Use the job's keywords naturally where they apply
- Create a concise, compelling narrative that connects the candidate's background to the job requirements
- Use a structured format that is easy to read for recruiters and ATS systems
- Output plain text only, no markdown, no JSON
- Try to cover 2 pages if possible, but do not add fluff just to increase length. 
- DO not go beyond 2 pages and not less than 1 page. 
- Do not add any note, commentary or explanations in the tailored resume.  

Write the complete tailored CV now:"""

    tailored = _llm_chat(prompt, model, temperature=0.3)

    # Save to file
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(title, company)
    out_path = RESUMES_DIR / f"{slug}.txt"
    out_path.write_text(tailored)
    print(f"[Resume] Saved tailored resume → {out_path.relative_to(DATA_DIR.parent)}")

    return tailored


if __name__ == "__main__":
    import json

    ranked_path = DATA_DIR / "ranked_jobs" / "ranked_jobs.json"
    if not ranked_path.exists():
        print("No ranked_jobs.json found. Run job_ranker.py first.")
        sys.exit(1)

    with open(ranked_path) as f:
        ranked = json.load(f)

    cv_text = CV_PATH.read_text()
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL

    apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    print(f"Tailoring resumes for {len(apply_jobs)} apply-decision jobs...")

    for job in apply_jobs:
        print(f"\n[Resume] Tailoring for: {job['title']} @ {job['company']}")
        tailor_resume(job, cv_text, model=model)

    print("\nDone.")
