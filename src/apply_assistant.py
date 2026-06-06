"""
Interactive application assistant.

Walks through each ranked job (apply first, then maybe), shows
the score / strengths / gaps, and asks whether to proceed.

On confirmation:
  - Generates tailored resume + cover letter (if not already on disk)

Usage:
    python3 src/apply_assistant.py                  # review all apply + maybe
    python3 src/apply_assistant.py --apply-only     # skip maybe candidates
    python3 src/apply_assistant.py --model mistral:7b
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from resume_tailor import tailor_resume, _slug
from cover_letter import generate_cover_letter

DATA_DIR = Path(__file__).parent.parent / "data"
RANKED_PATH = DATA_DIR / "ranked_jobs" / "ranked_jobs.json"
CV_PATH = DATA_DIR / "user_cv" / "user_cv.txt"
RESUMES_DIR = DATA_DIR / "resumes"
COVER_LETTERS_DIR = DATA_DIR / "cover_letters"

DEFAULT_MODEL = "llama3.1:8b"

# ANSI colours (degrade gracefully if terminal doesn't support them)
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[96m"
DIM = "\033[2m"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _decision_badge(decision: str) -> str:
    if decision == "apply":
        return f"{GREEN}{BOLD}✓ APPLY{RESET}"
    if decision == "maybe":
        return f"{YELLOW}{BOLD}~ MAYBE{RESET}"
    return f"{RED}✗ REJECT{RESET}"


def _print_job(idx: int, total: int, job: dict):
    title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    score = job.get("score", 0)
    decision = job.get("decision", "reject")
    details = job.get("ranking_details", {})
    strengths = details.get("strengths", [])
    gaps = details.get("gaps", [])
    explanation = details.get("explanation", "")
    url = job.get("url", "")

    print(f"\n{'─'*68}")
    print(f"{BOLD}[{idx}/{total}] {title}{RESET}")
    print(f"  Company  : {company}")
    print(f"  Score    : {BOLD}{score}/100{RESET}  {_decision_badge(decision)}")
    if url:
        print(f"  Link     : {DIM}{url}{RESET}")
    if explanation:
        print(f"\n  {CYAN}Summary:{RESET} {explanation}")
    if strengths:
        print(f"\n  {GREEN}Strengths:{RESET}")
        for s in strengths[:3]:
            print(f"    + {s}")
    if gaps:
        print(f"\n  {YELLOW}Gaps:{RESET}")
        for g in gaps[:3]:
            print(f"    - {g}")
    print()


def _docs_exist(job: dict) -> tuple:
    """Return (resume_path, cl_path) as strings, or empty string if not on disk."""
    slug = _slug(job.get("title", ""), job.get("company", ""))
    resume = RESUMES_DIR / f"{slug}.txt"
    cl = COVER_LETTERS_DIR / f"{slug}.txt"
    return (str(resume) if resume.exists() else "", str(cl) if cl.exists() else "")


def _prompt(question: str, options: str = "[y/n/s/?]") -> str:
    """Prompt user and return lowercased single-char answer."""
    while True:
        try:
            ans = input(f"  {BOLD}{question}{RESET} {DIM}{options}{RESET} → ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n[Interrupted]")
            sys.exit(0)
        if ans in ("y", "n", "s", "q", "?", "m"):
            return ans
        print("  Please enter one of: y / n / s / q  (? for help)")


# ---------------------------------------------------------------------------
# Main review loop
# ---------------------------------------------------------------------------

def review(jobs: list, model: str, cv_text: str):
    applied, skipped = [], []

    for idx, job in enumerate(jobs, 1):
        _print_job(idx, len(jobs), job)

        resume_path, cl_path = _docs_exist(job)
        docs_ready = bool(resume_path and cl_path)
        if docs_ready:
            print(f"  {DIM}Tailored resume + cover letter already generated.{RESET}")
        else:
            print(f"  {DIM}Tailored resume and cover letter not yet generated.{RESET}")

        ans = _prompt("Proceed with this application?", "[y=yes  n=skip  s=snooze  q=quit  ?=help]")

        if ans == "?":
            print("""
  y  — Generate docs (if needed) and record as applied
  n  — Skip this job entirely (mark skipped in DB)
  s  — Skip for now but keep as pending
  q  — Quit the assistant
""")
            ans = _prompt("Proceed with this application?", "[y/n/s/q]")

        if ans == "q":
            print("\n[Assistant] Exiting early.")
            break

        elif ans == "y":
            # Generate missing docs
            if not resume_path:
                print(f"\n  [Assistant] Generating tailored resume…")
                try:
                    tailored = tailor_resume(job, cv_text=cv_text, model=model)
                    slug = _slug(job.get("title", ""), job.get("company", ""))
                    resume_path = str(RESUMES_DIR / f"{slug}.txt")
                except Exception as e:
                    print(f"  [WARN] Resume generation failed: {e}")
                    tailored = ""
            else:
                tailored = (RESUMES_DIR / Path(resume_path).name).read_text()

            if not cl_path:
                print(f"  [Assistant] Generating cover letter…")
                try:
                    generate_cover_letter(job, tailored_resume=tailored or None, model=model)
                    slug = _slug(job.get("title", ""), job.get("company", ""))
                    cl_path = str(COVER_LETTERS_DIR / f"{slug}.txt")
                except Exception as e:
                    print(f"  [WARN] Cover letter generation failed: {e}")

            applied.append(job)

            # Show file locations
            if resume_path:
                print(f"  {DIM}Resume     : {resume_path}{RESET}")
            if cl_path:
                print(f"  {DIM}Cover letter: {cl_path}{RESET}")

        elif ans == "n":
            skipped.append(job)
            print(f"  {RED}✗ Skipped.{RESET}")

        else:  # "s" = snooze — do nothing, leave as pending
            print(f"  {YELLOW}~ Snoozed (not recorded yet).{RESET}")

    return applied, skipped


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI Job Seeker — interactive apply assistant")
    parser.add_argument("--apply-only", action="store_true", help="Only show apply-decision jobs (skip maybe)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    if not RANKED_PATH.exists():
        print("[Assistant] No ranked_jobs.json found. Run job_ranker.py or workflow.py first.")
        sys.exit(1)

    with open(RANKED_PATH) as f:
        ranked = json.load(f)

    # Sort: apply first (highest score), then maybe
    apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    maybe_jobs = [] if args.apply_only else [j for j in ranked if j["decision"] == "maybe"]
    queue = apply_jobs + maybe_jobs

    if not queue:
        print("[Assistant] No apply or maybe candidates found. Re-run ranking first.")
        sys.exit(0)

    print(f"\n{BOLD}{'='*68}")
    print(f"  AI Job Seeker — Apply Assistant")
    print(f"  {len(apply_jobs)} apply  |  {len(maybe_jobs)} maybe  |  reviewing {len(queue)} total")
    print(f"{'='*68}{RESET}")

    cv_text = CV_PATH.read_text()

    applied, skipped = review(queue, model=args.model, cv_text=cv_text)

    # Final summary
    print(f"\n{'='*68}")
    print(f"{BOLD}Session summary{RESET}")
    print(f"  Applied  : {GREEN}{len(applied)}{RESET}")
    print(f"  Skipped  : {RED}{len(skipped)}{RESET}")
    if applied:
        print(f"\n  Applied jobs:")
        for j in applied:
            print(f"    [{j['score']:3d}] {j['title']} @ {j['company']}")
    print()


if __name__ == "__main__":
    main()
