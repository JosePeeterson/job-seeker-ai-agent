import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tqdm import tqdm
import time

# Load keywords from preferences and roles
DATA_DIR = Path(__file__).parent.parent / "data"

import re
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

def main():
    print("[INFO] Loading keywords from data files...")
    keywords = load_keywords()
    print(f"[INFO] Loaded {len(keywords)} keywords.")
    jobs = scrape_jobstreet(keywords, max_pages=1)
    # Save results (overwrite each run to avoid duplicates)
    out_path = DATA_DIR / "scraped_jobs.json"
    with open(out_path, "w") as f:
        json.dump(jobs, f, indent=2)
    print(f"[INFO] Scraped {len(jobs)} jobs. Results saved to {out_path}")

if __name__ == "__main__":
    main()
