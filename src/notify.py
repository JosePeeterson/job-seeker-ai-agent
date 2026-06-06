"""
Notification / digest module.

Generates a formatted digest of the day's job ranking results and
optionally sends it as an HTML email.

Digest includes:
  - Apply candidates (score, title, company, link, summary)
  - Maybe candidates (quick list)
  - Reject count

Configuration (environment variables or pass kwargs):
  NOTIFY_EMAIL_TO      Recipient address (default: user@example.com)
  NOTIFY_EMAIL_FROM    Sender address
  NOTIFY_SMTP_HOST     SMTP host (default: smtp.gmail.com)
  NOTIFY_SMTP_PORT     SMTP port (default: 587)
  NOTIFY_SMTP_USER     SMTP login username
  NOTIFY_SMTP_PASS     SMTP app password

Usage:
    python3 src/notify.py                     # save digest to data/digest.html (no email)
    python3 src/notify.py --email             # save + send email
    python3 src/notify.py --email --to me@example.com

From code:
    from notify import send_digest
    send_digest(ranked_jobs, send_email=True)
"""

import argparse
import json
import os
import smtplib
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
RANKED_PATH = DATA_DIR / "ranked_jobs" / "ranked_jobs.json"
DIGEST_PATH = DATA_DIR / "digest" / "digest.html"

DEFAULT_TO = "user@example.com"


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _score_colour(score: int) -> str:
    if score >= 70:
        return "#2e7d32"   # green
    if score >= 45:
        return "#f57f17"   # amber
    return "#c62828"       # red


def _decision_label(decision: str) -> str:
    if decision == "apply":
        return "✓ APPLY"
    if decision == "maybe":
        return "~ MAYBE"
    return "✗ REJECT"


def build_html(ranked: list, run_date: str = None) -> str:
    run_date = run_date or str(date.today())
    apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    maybe_jobs = [j for j in ranked if j["decision"] == "maybe"]
    reject_count = sum(1 for j in ranked if j["decision"] == "reject")

    def job_card(job: dict) -> str:
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        score = job.get("score", 0)
        url = job.get("url", "#")
        details = job.get("ranking_details", {})
        explanation = details.get("explanation", "")
        strengths = details.get("strengths", [])[:3]
        gaps = details.get("gaps", [])[:2]
        colour = _score_colour(score)

        strengths_html = "".join(
            f'<li style="color:#2e7d32">✓ {s}</li>' for s in strengths
        )
        gaps_html = "".join(
            f'<li style="color:#b71c1c">✗ {g}</li>' for g in gaps
        )

        return f"""
<div style="border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin-bottom:16px;background:#fff;">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
    <span style="background:{colour};color:#fff;font-weight:bold;padding:4px 10px;
                 border-radius:4px;font-size:14px;">{score}/100</span>
    <span style="font-size:16px;font-weight:bold;">{title}</span>
  </div>
  <div style="color:#555;margin-bottom:8px;">{company}</div>
  {f'<div style="margin-bottom:10px;color:#333;">{explanation}</div>' if explanation else ''}
  <div style="display:flex;gap:32px;">
    {f'<ul style="margin:0;padding-left:18px;">{strengths_html}</ul>' if strengths_html else ''}
    {f'<ul style="margin:0;padding-left:18px;">{gaps_html}</ul>' if gaps_html else ''}
  </div>
  {f'<div style="margin-top:10px;"><a href="{url}" style="color:#1565c0;">View job posting →</a></div>' if url and url != "#" else ''}
</div>"""

    apply_section = ""
    if apply_jobs:
        cards = "".join(job_card(j) for j in apply_jobs)
        apply_section = f"""
<h2 style="color:#2e7d32;border-bottom:2px solid #2e7d32;padding-bottom:4px;">
  ✓ Apply ({len(apply_jobs)})
</h2>
{cards}"""

    maybe_rows = "".join(
        f'<tr><td style="padding:6px 12px;color:#555;">{j["score"]}</td>'
        f'<td style="padding:6px 12px;">{j["title"]}</td>'
        f'<td style="padding:6px 12px;color:#555;">{j["company"]}</td>'
        f'{"<td style=padding:6px_12px;><a href=" + j["url"] + ">View</a></td>" if j.get("url") else "<td></td>"}'
        f'</tr>'
        for j in maybe_jobs
    )
    maybe_section = ""
    if maybe_jobs:
        maybe_section = f"""
<h2 style="color:#f57f17;border-bottom:2px solid #f57f17;padding-bottom:4px;">
  ~ Maybe ({len(maybe_jobs)})
</h2>
<table style="border-collapse:collapse;width:100%;margin-bottom:24px;">
  <thead><tr style="background:#fff8e1;">
    <th style="padding:6px 12px;text-align:left;color:#555;">Score</th>
    <th style="padding:6px 12px;text-align:left;">Title</th>
    <th style="padding:6px 12px;text-align:left;color:#555;">Company</th>
    <th style="padding:6px 12px;text-align:left;"></th>
  </tr></thead>
  <tbody>{maybe_rows}</tbody>
</table>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Job Digest — {run_date}</title>
</head>
<body style="font-family:Arial,sans-serif;max-width:720px;margin:32px auto;color:#212121;padding:0 16px;">
  <h1 style="color:#1a237e;">
    Job Search Digest
    <span style="font-size:14px;font-weight:normal;color:#666;margin-left:12px;">{run_date}</span>
  </h1>
  <div style="background:#f5f5f5;border-radius:6px;padding:12px 20px;margin-bottom:24px;
              display:flex;gap:32px;">
    <div><strong style="color:#2e7d32;font-size:20px;">{len(apply_jobs)}</strong><br>Apply</div>
    <div><strong style="color:#f57f17;font-size:20px;">{len(maybe_jobs)}</strong><br>Maybe</div>
    <div><strong style="color:#c62828;font-size:20px;">{reject_count}</strong><br>Reject</div>
    <div><strong style="color:#333;font-size:20px;">{len(ranked)}</strong><br>Total</div>
  </div>
  {apply_section}
  {maybe_section}
  <p style="color:#999;font-size:12px;margin-top:32px;">
    Generated by AI Job Seeker Agent · {run_date}
  </p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Plain-text fallback (for email plain part)
# ---------------------------------------------------------------------------

def build_text(ranked: list, run_date: str = None) -> str:
    run_date = run_date or str(date.today())
    apply_jobs = [j for j in ranked if j["decision"] == "apply"]
    maybe_jobs = [j for j in ranked if j["decision"] == "maybe"]
    reject_count = sum(1 for j in ranked if j["decision"] == "reject")

    lines = [
        f"Job Search Digest — {run_date}",
        f"{'='*50}",
        f"Apply: {len(apply_jobs)}  |  Maybe: {len(maybe_jobs)}  |  Reject: {reject_count}  |  Total: {len(ranked)}",
        "",
    ]

    if apply_jobs:
        lines.append("── APPLY ──────────────────────────────────────────")
        for j in apply_jobs:
            lines.append(f"\n[{j['score']}/100] {j['title']} @ {j['company']}")
            exp = j.get("ranking_details", {}).get("explanation", "")
            if exp:
                lines.append(f"  {exp}")
            url = j.get("url", "")
            if url:
                lines.append(f"  {url}")
        lines.append("")

    if maybe_jobs:
        lines.append("── MAYBE ──────────────────────────────────────────")
        for j in maybe_jobs:
            lines.append(f"  [{j['score']:3d}] {j['title']} @ {j['company']}")
        lines.append("")

    lines.append(f"Generated by AI Job Seeker Agent · {run_date}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(
    html: str,
    text: str,
    to: str,
    subject: str,
    smtp_host: str = None,
    smtp_port: int = None,
    smtp_user: str = None,
    smtp_pass: str = None,
    from_addr: str = None,
):
    smtp_host = smtp_host or os.environ.get("NOTIFY_SMTP_HOST", "smtp.gmail.com")
    smtp_port = smtp_port or int(os.environ.get("NOTIFY_SMTP_PORT", "587"))
    smtp_user = smtp_user or os.environ.get("NOTIFY_SMTP_USER", "")
    smtp_pass = smtp_pass or os.environ.get("NOTIFY_SMTP_PASS", "")
    from_addr = from_addr or os.environ.get("NOTIFY_EMAIL_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        raise ValueError(
            "SMTP credentials not set. Export NOTIFY_SMTP_USER and NOTIFY_SMTP_PASS "
            "(or NOTIFY_SMTP_HOST / NOTIFY_SMTP_PORT / NOTIFY_EMAIL_FROM as needed)."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, [to], msg.as_string())

    print(f"[Notify] Email sent to {to}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_digest(
    ranked: list,
    send_email_flag: bool = False,
    to: str = None,
    run_date: str = None,
    **smtp_kwargs,
) -> str:
    """
    Build and optionally email the digest.

    Returns the path to the saved HTML file.
    """
    run_date = run_date or str(date.today())
    to = to or os.environ.get("NOTIFY_EMAIL_TO", DEFAULT_TO)
    subject = f"Job Digest {run_date} — {sum(1 for j in ranked if j['decision'] == 'apply')} to apply"

    html = build_html(ranked, run_date)
    text = build_text(ranked, run_date)

    # Always save the HTML digest to disk
    DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_PATH.write_text(html, encoding="utf-8")
    print(f"[Notify] Digest saved → {DIGEST_PATH}")

    # Print plain-text summary to terminal
    print("\n" + text)

    if send_email_flag:
        try:
            send_email(html, text, to=to, subject=subject, **smtp_kwargs)
        except ValueError as e:
            print(f"[Notify] Cannot send email: {e}")
        except smtplib.SMTPException as e:
            print(f"[Notify] SMTP error: {e}")

    return str(DIGEST_PATH)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate job search digest")
    parser.add_argument("--email", action="store_true", help="Send digest by email")
    parser.add_argument("--to", default=None, help="Recipient email (overrides NOTIFY_EMAIL_TO)")
    parser.add_argument("--date", default=None, help="Date label for the digest (default: today)")
    args = parser.parse_args()

    if not RANKED_PATH.exists():
        print("[Notify] No ranked_jobs.json found. Run job_ranker.py or workflow.py first.")
        sys.exit(1)

    with open(RANKED_PATH) as f:
        ranked = json.load(f)

    send_digest(ranked, send_email_flag=args.email, to=args.to, run_date=args.date)


if __name__ == "__main__":
    main()
