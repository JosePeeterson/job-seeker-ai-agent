import json
import subprocess
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from tqdm import tqdm
import time

# Load keywords from preferences and roles
DATA_DIR = Path(__file__).parent.parent / "data"

import re

_browsers_installed = False


def _ensure_playwright_browsers() -> None:
    """
    Download Playwright's Chromium binary if it isn't already cached.
    On Streamlit Cloud the Python package is installed but the browser
    executable is not — this runs `playwright install chromium` once.
    """
    global _browsers_installed
    if _browsers_installed:
        return
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            print(f"[WARN] playwright install output: {result.stdout} {result.stderr}")
        else:
            print("[INFO] Playwright Chromium ready.")
    except Exception as e:
        print(f"[WARN] Could not run playwright install: {e}")
    _browsers_installed = True


def clean_keyword(kw):
    # Only allow keywords with letters, numbers, spaces, and hyphens, and max 3 words
    kw = kw.strip()
    if len(kw.split()) > 3:
        return None
    if not re.match(r'^[\w\s\-]+$', kw):
        return None
    return kw.lower()

def load_keywords():
    keywords = set()
    skipped = set()
    # Load academic roles
    with open(DATA_DIR / "tina_academia_roles.json") as f:
        academia = json.load(f)
        for area in academia:
            for k in [area["area"]] + area["roles"]:
                cleaned = clean_keyword(k)
                if cleaned:
                    keywords.add(cleaned)
                else:
                    skipped.add(k)
    # Load industry roles
    with open(DATA_DIR / "tina_industry_roles.json") as f:
        industry = json.load(f)
        for area in industry:
            for k in [area["area"]] + area["roles"]:
                cleaned = clean_keyword(k)
                if cleaned:
                    keywords.add(cleaned)
                else:
                    skipped.add(k)
    # Fallback defaults if too few keywords
    if len(keywords) < 5:
        fallback = ["English lecturer", "English editor","English teacher","Literature teacher","Poetry teacher","English writer", "English literature", "Content writer", "English research assistant"]
        print(f"[WARN] Too few keywords found, using fallback: {fallback}")
        keywords.update(fallback)
    print(f"[INFO] Skipped {len(skipped)} keywords due to complexity or special characters: {sorted(skipped)}")
    print(f"[INFO] Using {len(keywords)} cleaned keywords: {sorted(keywords)}")
    return list(sorted(keywords))

BASE_URL = "https://sg.jobstreet.com"
LINKEDIN_BASE = "https://www.linkedin.com"
# LinkedIn geoId for Singapore — ensures location filter is exact, not text-matched
LINKEDIN_SG_GEO_ID = "102454443"


def _fetch_full_description(page, job_url: str) -> str:
    """Visit a job detail page and return the full job description text."""
    try:
        page.goto(job_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        el = page.query_selector('[data-automation="jobAdDetails"]')
        return el.inner_text().strip() if el else ""
    except Exception as e:
        print(f"[WARN] Could not fetch full description for {job_url}: {e}")
        return ""


def scrape_jobstreet(keywords, max_pages=1, fetch_full_descriptions=True):
    _ensure_playwright_browsers()
    jobs = []
    seen_urls = set()
    print(f"[INFO] Starting scraping for {len(keywords)} keywords...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-SG",
            timezone_id="Asia/Singapore",
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        list_page = context.new_page()
        detail_page = context.new_page() if fetch_full_descriptions else None

        for keyword in tqdm(keywords, desc="Keywords", unit="kw"):
            url = f"{BASE_URL}/{keyword.replace(' ', '-').lower()}-jobs"
            print(f"[INFO] Searching for jobs with keyword: {keyword}")
            list_page.goto(url)
            list_page.wait_for_timeout(5000)
            job_cards = list_page.query_selector_all('article[data-testid="job-card"]')
            print(f"[INFO] Found {len(job_cards)} job cards for keyword '{keyword}'")
            for card in job_cards:
                title_el = card.query_selector("h3 a")
                title = title_el.inner_text() if title_el else card.get_attribute("aria-label") or ""
                company_el = card.query_selector('[data-automation="jobCompany"]')
                company = company_el.inner_text() if company_el else ""
                link_el = card.query_selector('a[data-automation="job-list-view-job-link"]')
                rel_url = link_el.get_attribute("href") if link_el else ""
                job_url = BASE_URL + rel_url if rel_url.startswith("/") else rel_url

                # De-duplicate by URL (strip query string for comparison)
                url_key = job_url.split("?")[0]
                if url_key in seen_urls:
                    continue
                seen_urls.add(url_key)

                card_snippet = card.inner_text()
                # Filter: Only keep jobs where keyword is in title or snippet
                if keyword.lower() not in title.lower() and keyword.lower() not in card_snippet.lower():
                    continue

                # Fetch full description from detail page
                full_desc = ""
                if fetch_full_descriptions and job_url:
                    full_desc = _fetch_full_description(detail_page, job_url)
                    time.sleep(0.5)

                jobs.append({
                    "title": title,
                    "company": company,
                    "url": job_url,
                    "description": full_desc or card_snippet,
                    "matched_keyword": keyword,
                })
            print(f"[INFO] Done with keyword: {keyword}. Total jobs collected so far: {len(jobs)}")
            time.sleep(1)  # Be polite to the server
        browser.close()
    print(f"[INFO] Scraping complete. Total jobs collected: {len(jobs)}")
    return jobs

def _linkedin_fetch_description(page, job_url: str) -> str:
    """Visit a LinkedIn job detail page and return the full description text."""
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        # Try the primary selector first, then fallbacks
        for selector in [
            ".description__text .show-more-less-html__markup",
            ".description__text",
            "[class*='description']",
        ]:
            el = page.query_selector(selector)
            if el:
                return el.inner_text().strip()
        return ""
    except Exception as e:
        print(f"[WARN] LinkedIn: could not fetch description for {job_url}: {e}")
        return ""


def scrape_linkedin(
    keywords: list,
    location: str = "Singapore",
    fetch_full_descriptions: bool = True,
    days_posted: int = 30,
) -> list:
    """
    Scrape LinkedIn Jobs public search pages (no login required).

    Always scopes results to Singapore via geoId=102454443, which is
    LinkedIn's canonical geo-identifier for Singapore. The `location`
    parameter is kept for display purposes only.

    Returns a list of job dicts matching the same schema as scrape_jobstreet():
      title, company, url, description, matched_keyword, source
    """
    jobs = []
    seen_urls: set = set()
    # LinkedIn time filter: r86400=24h, r604800=1w, r2592000=30d
    time_filter = f"r{days_posted * 86400}"

    _ensure_playwright_browsers()
    print(f"[LinkedIn] Starting scrape for {len(keywords)} keywords in {location}…")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-SG",
            timezone_id="Asia/Singapore",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        list_page = context.new_page()
        detail_page = context.new_page() if fetch_full_descriptions else None

        for keyword in tqdm(keywords, desc="LinkedIn keywords", unit="kw"):
            encoded_kw = keyword.replace(" ", "%20")
            search_url = (
                f"{LINKEDIN_BASE}/jobs/search/"
                f"?keywords={encoded_kw}"
                f"&location=Singapore&geoId={LINKEDIN_SG_GEO_ID}"
                f"&f_TPR={time_filter}&sortBy=DD"
            )
            print(f"[LinkedIn] Searching: {keyword}")
            try:
                list_page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                list_page.wait_for_timeout(3000)
            except Exception as e:
                print(f"[LinkedIn] WARN: could not load search page for '{keyword}': {e}")
                continue

            # Job cards on the public search page
            cards = list_page.query_selector_all("ul.jobs-search__results-list li")
            if not cards:
                # Fallback selector used on some LinkedIn page variants
                cards = list_page.query_selector_all(".base-card")
            print(f"[LinkedIn] Found {len(cards)} cards for '{keyword}'")

            for card in cards:
                # Title
                title_el = card.query_selector("h3.base-search-card__title")
                title = title_el.inner_text().strip() if title_el else ""
                if not title:
                    continue

                # Company
                company_el = card.query_selector("h4.base-search-card__subtitle")
                company = company_el.inner_text().strip() if company_el else ""

                # URL
                link_el = card.query_selector("a.base-card__full-link")
                job_url = link_el.get_attribute("href") if link_el else ""
                if not job_url:
                    continue
                # Strip tracking params — keep only the canonical path
                job_url = job_url.split("?")[0]

                if job_url in seen_urls:
                    continue
                seen_urls.add(job_url)

                # Relevance filter
                snippet = card.inner_text()
                kw_lower = keyword.lower()
                if kw_lower not in title.lower() and kw_lower not in snippet.lower():
                    continue

                # Full description
                description = ""
                if fetch_full_descriptions and detail_page:
                    description = _linkedin_fetch_description(detail_page, job_url)
                    time.sleep(1)  # polite delay

                jobs.append({
                    "title": title,
                    "company": company,
                    "url": job_url,
                    "description": description or snippet,
                    "matched_keyword": keyword,
                    "source": "linkedin",
                })

            print(f"[LinkedIn] Done '{keyword}'. Total so far: {len(jobs)}")
            time.sleep(2)

        browser.close()

    print(f"[LinkedIn] Scrape complete. {len(jobs)} jobs collected.")
    return jobs


def scrape_all(
    keywords: list,
    sources: list = None,
    fetch_full_descriptions: bool = True,
) -> list:
    """
    Scrape from one or more sources and merge results, de-duplicating by URL.

    sources: list containing any of "jobstreet", "linkedin"
             Defaults to ["jobstreet", "linkedin"] when None.
    """
    if sources is None:
        sources = ["jobstreet", "linkedin"]

    all_jobs: list = []
    seen: set = set()

    if "jobstreet" in sources:
        js_jobs = scrape_jobstreet(keywords, fetch_full_descriptions=fetch_full_descriptions)
        for j in js_jobs:
            key = j.get("url", "").split("?")[0]
            if key not in seen:
                seen.add(key)
                j.setdefault("source", "jobstreet")
                all_jobs.append(j)
        print(f"[scrape_all] JobStreet: {len(js_jobs)} jobs")

    if "linkedin" in sources:
        li_jobs = scrape_linkedin(keywords, fetch_full_descriptions=fetch_full_descriptions)
        for j in li_jobs:
            key = j.get("url", "").split("?")[0]
            if key not in seen:
                seen.add(key)
                all_jobs.append(j)
        print(f"[scrape_all] LinkedIn: {len(li_jobs)} jobs")

    print(f"[scrape_all] Total unique jobs: {len(all_jobs)}")
    return all_jobs


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape job listings")
    parser.add_argument(
        "--source",
        choices=["jobstreet", "linkedin", "all"],
        default="all",
        help="Which site(s) to scrape (default: all)",
    )
    args = parser.parse_args()

    print("[INFO] Loading keywords from data files...")
    keywords = load_keywords()
    print(f"[INFO] Loaded {len(keywords)} keywords.")

    if args.source == "jobstreet":
        jobs = scrape_jobstreet(keywords)
    elif args.source == "linkedin":
        jobs = scrape_linkedin(keywords)
    else:
        jobs = scrape_all(keywords)

    out_path = DATA_DIR / "scraped_jobs.json"
    with open(out_path, "w") as f:
        json.dump(jobs, f, indent=2)
    print(f"[INFO] Scraped {len(jobs)} jobs. Results saved to {out_path}")


if __name__ == "__main__":
    main()
