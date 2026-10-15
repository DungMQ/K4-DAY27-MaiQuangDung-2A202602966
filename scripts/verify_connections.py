"""Read-only GitHub/OpenAI check. Never posts a comment or prints credentials."""
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from common.github import fetch_pr
from common.llm import get_llm
from exercises.exercise_1_confidence import node_route, review_messages
from common.schemas import PRAnalysis


def safe_error(exc):
    response = getattr(exc, "response", None)
    return {"error": type(exc).__name__, "status": getattr(response, "status_code", None),
            "code": getattr(exc, "code", None)}


def main():
    load_dotenv()
    results = {}
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get("https://api.github.com/user", headers={
                "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                "Accept": "application/vnd.github+json",
            })
            response.raise_for_status()
            results["github"] = {"ok": True, "user": response.json()["login"]}
    except Exception as exc:
        results["github"] = safe_error(exc)

    for number in (1, 2):
        url = f"https://github.com/VinUni-AI20k/PR-Demo/pull/{number}"
        try:
            pr = fetch_pr(url)
            state = {"pr_url": url, "pr_title": pr.title, "pr_diff": pr.diff}
            llm = get_llm().with_structured_output(PRAnalysis)
            analysis = llm.invoke(review_messages(state))
            results[f"pr_{number}"] = {
                "url": url, "files": len(pr.files_changed), "head_sha": pr.head_sha,
                "confidence": analysis.confidence, "branch": node_route({"analysis": analysis})["decision"],
                "analysis": analysis.model_dump(),
            }
        except Exception as exc:
            results[f"pr_{number}"] = safe_error(exc)

    report = Path("artifacts/connection_check.json")
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    # Summaries only; full PR analysis is in the report.
    print(json.dumps({k: {f: v for f, v in r.items() if f != "analysis"}
                      for k, r in results.items()}, indent=2, ensure_ascii=False))
    return 0 if all("error" not in r for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
