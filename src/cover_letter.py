"""
Cover letter generation module.

Takes a ranked job dict + tailored resume text and produces a
professional cover letter addressed to the hiring company.

Usage:
    from cover_letter import generate_cover_letter

    letter = generate_cover_letter(job, tailored_resume, model="llama3.1:8b")
    # returns cover letter as a plain-text string
    # also saves to data/cover_letters/<slug>.txt
"""

from pathlib import Path
import sys
import re

sys.path.insert(0, str(Path(__file__).parent))
from llm import chat as _llm_chat

DATA_DIR = Path(__file__).parent.parent / "data"
COVER_LETTERS_DIR = DATA_DIR / "cover_letters"
CV_PATH = DATA_DIR / "tina_cv.txt"
RESUMES_DIR = DATA_DIR / "resumes"

DEFAULT_MODEL = "llama3.1:8b"


def _slug(title: str, company: str) -> str:
    raw = f"{title}_{company}".lower()
    return re.sub(r"[^a-z0-9]+", "_", raw)[:80].strip("_")


def generate_cover_letter(
    job: dict,
    tailored_resume: str = None,
    model: str = DEFAULT_MODEL,
) -> str:
    """
    Generate a cover letter for the given job.

    If tailored_resume is None, reads from data/resumes/<slug>.txt if it
    exists, otherwise falls back to the original CV.

    Returns the cover letter as plain text and saves to
    data/cover_letters/<slug>.txt.
    """
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    description = job.get("description", "")
    requirements = job.get("requirements", {})
    strengths = job.get("ranking_details", {}).get("strengths", [])
    gaps = job.get("ranking_details", {}).get("gaps", [])

    if tailored_resume is None:
        slug = _slug(title, company)
        resume_path = RESUMES_DIR / f"{slug}.txt"
        if resume_path.exists():
            tailored_resume = resume_path.read_text()
        else:
            tailored_resume = CV_PATH.read_text()

    import json
    req_text = json.dumps(requirements, indent=2) if requirements else description[:1500]
    strengths_text = "\n".join(f"- {s}" for s in strengths) if strengths else "(see resume)"

    prompt = f"""You are a professional cover letter writer helping an academic candidate apply for a job.

Candidate: R G Arshad Tina Raghi
Contact: tinaarshara@gmail.com | +65 97156051 | Singapore (Dependent Pass, eligible to work)

Target role: {title} at {company}

Job requirements:
{req_text}

Key strengths identified for this role:
{strengths_text}

Candidate's tailored resume:
{tailored_resume[:3000]}

Write a professional cover letter for this application. Guidelines:
- Address it to "The Hiring Manager" at {company}
- 3-4 paragraphs: opening (role + why interested), fit (map her background to requirements), value (what she brings), closing (call to action)
- Tone: confident but not arrogant; academic but accessible
- Do NOT mention or apologise for gaps — focus only on strengths and fit
- Do NOT invent experience or qualifications not in the resume
- Keep to one page (approx 300-350 words)
- Output the letter as plain text, ready to send

Write the cover letter now:"""

    letter = _llm_chat(prompt, model, temperature=0.4)

    # Save to file
    COVER_LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(title, company)
    out_path = COVER_LETTERS_DIR / f"{slug}.txt"
    out_path.write_text(letter)
    print(f"[CoverLetter] Saved → {out_path.relative_to(DATA_DIR.parent)}")

    return letter


if __name__ == "__main__":
    import json

    ranked_path = DATA_DIR / "ranked_jobs.json"
    if not ranked_path.exists():
        print("No ranked_jobs.json found. Run job_ranker.py first.")
        sys.exit(1)

    with open(ranked_path) as f:
        ranked = json.load(f)

    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    print(f"Generating cover letters for {len(apply_jobs)} apply-decision jobs...")

    for job in apply_jobs:
        print(f"\n[CoverLetter] Generating for: {job['title']} @ {job['company']}")
        generate_cover_letter(job, model=model)

    print("\nDone.")
