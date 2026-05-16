"""
End-to-end workflow orchestrator.

Steps:
  1. Scrape JobStreet for Tina's keywords
  2. Rank all scraped jobs (extract requirements → score fit → decide)
  3. For each "apply" decision: tailor resume + generate cover letter
  4. Save everything; print summary

Usage:
    python3 src/workflow.py                    # full run
    python3 src/workflow.py --skip-scrape      # rank + generate from existing scraped_jobs.json
    python3 src/workflow.py --apply-only       # only generate docs for existing apply decisions
    python3 src/workflow.py --model mistral:7b # use a different Ollama model
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from job_scraper import scrape_all, load_keywords
from job_ranker import rank_all_jobs, print_summary, DEFAULT_MODEL
from resume_tailor import tailor_resume
from cover_letter import generate_cover_letter
from notify import send_digest

DATA_DIR = Path(__file__).parent.parent / "data"
SCRAPED_PATH = DATA_DIR / "scraped_jobs.json"
RANKED_PATH = DATA_DIR / "ranked_jobs.json"
CV_PATH = DATA_DIR / "tina_cv.txt"


def run(skip_scrape: bool = False, apply_only: bool = False, model: str = DEFAULT_MODEL, notify: bool = False, sources: list = None):
    # ── 1. Scrape ──────────────────────────────────────────────────────────────
    if not apply_only:
        if skip_scrape and SCRAPED_PATH.exists():
            print("[Workflow] Skipping scrape — using existing scraped_jobs.json")
            with open(SCRAPED_PATH) as f:
                jobs = json.load(f)
            print(f"[Workflow] Loaded {len(jobs)} jobs from disk.")
        else:
            active_sources = sources or ["jobstreet", "linkedin"]
            src_label = " + ".join(active_sources)
            print(f"[Workflow] ── Step 1: Scraping {src_label} ──")
            keywords = load_keywords()
            print(f"[Workflow] Keywords: {keywords}")
            jobs = scrape_all(keywords, sources=active_sources, fetch_full_descriptions=True)
            print(f"[Workflow] Scraped {len(jobs)} jobs.")

        # ── 2. Rank ────────────────────────────────────────────────────────────
        print(f"\n[Workflow] ── Step 2: Ranking {len(jobs)} jobs (model: {model}) ──")
        ranked = rank_all_jobs(jobs, model=model)
        with open(RANKED_PATH, "w") as f:
            json.dump(ranked, f, indent=2)
        print(f"[Workflow] Rankings saved to {RANKED_PATH}")
        print_summary(ranked)
    else:
        print("[Workflow] --apply-only: loading existing ranked_jobs.json")
        if not RANKED_PATH.exists():
            print("[Workflow] ERROR: ranked_jobs.json not found. Run without --apply-only first.")
            sys.exit(1)
        with open(RANKED_PATH) as f:
            ranked = json.load(f)

    # ── 3. Generate documents for apply decisions ──────────────────────────────
    apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    maybe_jobs = [j for j in ranked if j["decision"] == "maybe"]

    print(f"\n[Workflow] ── Step 3: Generating documents for {len(apply_jobs)} apply job(s) ──")
    if not apply_jobs:
        print("[Workflow] No apply-decision jobs found. Adjust score thresholds or re-scrape.")
    else:
        cv_text = CV_PATH.read_text()
        for job in apply_jobs:
            title = job["title"]
            company = job["company"]
            print(f"\n[Workflow] Processing: {title} @ {company}  [score={job['score']}]")
            try:
                tailored = tailor_resume(job, cv_text=cv_text, model=model)
                generate_cover_letter(job, tailored_resume=tailored, model=model)
            except Exception as exc:
                print(f"[Workflow] ERROR generating docs for {title}: {exc}")

    # ── 4. Summary ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"[Workflow] Complete.")
    print(f"  Apply  : {len(apply_jobs)} jobs  → resumes + cover letters saved to data/")
    print(f"  Maybe  : {len(maybe_jobs)} jobs  → review manually")
    print(f"  Reject : {len(ranked) - len(apply_jobs) - len(maybe_jobs)} jobs")

    if maybe_jobs:
        print("\n  Maybe candidates (review & decide):")
        for j in maybe_jobs:
            print(f"    [{j['score']:3d}] {j['title']} @ {j['company']}")

    # ── 5. Digest ──────────────────────────────────────────────────────────────
    print("\n[Workflow] ── Step 5: Generating digest ──")
    send_digest(ranked, send_email_flag=notify)

    return ranked


def main():
    parser = argparse.ArgumentParser(description="AI Job Seeker Agent — full workflow")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping, use existing scraped_jobs.json")
    parser.add_argument("--apply-only", action="store_true", help="Only generate docs for existing apply decisions")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--notify", action="store_true", help="Send email digest after run (requires SMTP env vars)")
    parser.add_argument(
        "--source",
        choices=["jobstreet", "linkedin", "all"],
        default="all",
        help="Which job sites to scrape (default: all)",
    )
    args = parser.parse_args()

    sources = ["jobstreet", "linkedin"] if args.source == "all" else [args.source]
    run(skip_scrape=args.skip_scrape, apply_only=args.apply_only, model=args.model, notify=args.notify, sources=sources)


if __name__ == "__main__":
    main()
