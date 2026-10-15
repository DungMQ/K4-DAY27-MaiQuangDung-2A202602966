"""Capture real browser screenshots against the local, mocked report harness."""
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright


BASE = "http://127.0.0.1:8502"
OUT = Path("artifacts/screenshots")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"mode": "real browser + real LangGraph/SQLite; mocked LLM/GitHub",
               "captured_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
               "sessions": {}, "screenshots": [], "console_errors": [], "page_errors": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        context.route("**/*", lambda route: route.continue_() if
                      urlparse(route.request.url).hostname in {"127.0.0.1", "localhost"} else route.abort())
        page = context.new_page()
        page.on("pageerror", lambda exc: summary["page_errors"].append(str(exc)))
        page.on("console", lambda msg: summary["console_errors"].append(msg.text) if msg.type == "error" else None)
        page.goto(BASE)
        expect(page.get_by_role("heading", name="HITL PR Review Agent", exact=True)).to_be_visible(timeout=30000)

        def capture(name):
            page.screenshot(path=str(OUT / name), full_page=True, animations="disabled")
            summary["screenshots"].append("artifacts/screenshots/" + name)

        def start(number, label, expected):
            previous = parse_qs(urlparse(page.url).query).get("thread", [None])[0]
            page.get_by_role("textbox", name="PR URL", exact=True).fill(f"https://github.com/evidence/fixture/pull/{number}")
            page.get_by_role("button", name="Run review", exact=True).click()
            page.wait_for_function("previous => new URL(location.href).searchParams.get('thread') !== previous", arg=previous)
            thread = parse_qs(urlparse(page.url).query)["thread"][0]
            expect(page.get_by_text("Session: " + thread, exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_text(expected, exact=False).first).to_be_visible(timeout=30000)
            page.get_by_role("heading", name="HITL PR Review Agent", exact=True).scroll_into_view_if_needed()
            summary["sessions"][label] = thread

        capture("01_home.png")
        start(1, "approve", "Approval requested")
        page.get_by_text("Diff", exact=True).click()
        capture("02_approval.png")
        page.get_by_role("button", name="Approve", exact=True).scroll_into_view_if_needed()
        capture("02b_approval_controls.png")
        page.get_by_role("button", name="Approve", exact=True).click()
        expect(page.get_by_text("Review comment posted.", exact=True)).to_be_visible(timeout=30000)
        capture("03_approved.png")

        start(2, "escalation", "Strong escalation")
        capture("04_escalation.png")
        page.get_by_role("button", name="Submit answers", exact=True).scroll_into_view_if_needed()
        capture("04b_escalation_questions.png")
        for field in page.locator('textarea').all():
            field.fill("Fixture context: internal demonstration only; fix the security issue before production and add regression tests.")
        page.get_by_role("button", name="Submit answers", exact=True).click()
        expect(page.get_by_text("Approval requested", exact=False)).to_be_visible(timeout=30000)
        expect(page.get_by_text("Refined review after reviewer answers.", exact=False)).to_be_visible()
        page.get_by_role("heading", name="Approval requested — confidence 70%", exact=True).scroll_into_view_if_needed()
        capture("05_refined.png")
        page.get_by_role("button", name="Approve", exact=True).click()
        expect(page.get_by_text("Review comment posted.", exact=True)).to_be_visible(timeout=30000)

        start(1, "reject", "Approval requested")
        page.get_by_role("button", name="Reject", exact=True).click()
        expect(page.get_by_text("Rejected — no comment posted.", exact=True)).to_be_visible(timeout=30000)
        capture("06_rejected.png")

        start(1, "edit", "Approval requested")
        page.get_by_role("textbox", name="Feedback or complete edited review").fill(
            "### Reviewer-edited review\nAdd a database migration and validate priority values 0, 1 and 2.")
        page.get_by_role("button", name="Edit", exact=True).click()
        expect(page.get_by_text("Review comment posted.", exact=True)).to_be_visible(timeout=30000)

        start(3, "auto", "Review comment posted.")
        capture("07_auto_and_sessions.png")
        summary["browser"] = browser.version
        context.close()
        browser.close()

    with sqlite3.connect("scratch/report-evidence.db") as conn:
        conn.row_factory = sqlite3.Row
        ids = list(summary["sessions"].values())
        assert len(set(ids)) == 5, "Each browser scenario must have its own thread."
        placeholders = ",".join("?" for _ in ids)
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM audit_events WHERE thread_id IN ({placeholders}) ORDER BY id", ids)]
        summary["audit_rows"] = len(rows)
        summary["high_risk_auto_count"] = sum(row["risk_level"] == "high" and row["decision"] == "auto" for row in rows)
        summary["approval_confidence_average"] = conn.execute(
            f"SELECT AVG(confidence) FROM audit_events WHERE decision='approve' AND thread_id IN ({placeholders})", ids
        ).fetchone()[0]
    with Path("artifacts/audit_events.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for label, thread in summary["sessions"].items():
        events = [r for r in rows if r["thread_id"] == thread]
        summary["sessions"][label] = {"thread_id": thread, "events": len(events),
                                        "last_action": events[-1]["action"], "last_reason": events[-1]["reason"]}
    assert summary["sessions"]["auto"]["last_action"] == "auto_approve"
    assert summary["sessions"]["reject"]["last_reason"] == "Comment rejected"
    Path("artifacts/browser_evidence.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["page_errors"] or summary["console_errors"] or summary["high_risk_auto_count"]:
        raise SystemExit("Browser/audit evidence contains errors; inspect the JSON before publishing.")


if __name__ == "__main__":
    main()
