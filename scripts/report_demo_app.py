"""Local report harness. External APIs are replaced; never posts to GitHub.

Run from the repository root with HITL_DB_PATH=scratch/report-evidence.db:
python -m streamlit run scripts/report_demo_app.py --server.port 8502
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import streamlit as st

from app import main
from common.schemas import PRAnalysis
from exercises import exercise_4_audit as ex4


CAPTURE = json.loads(Path("artifacts/connection_check.json").read_text(encoding="utf-8"))


def fixture_pr(url):
    number = int(url.rstrip("/").split("/")[-1])
    scenario = {1: "approval", 2: "escalation", 3: "auto"}.get(number, "approval")
    diff = {
        "approval": "diff --git a/tasktrack/models.py b/tasktrack/models.py\n@@ -1,2 +1,3 @@\n class Task:\n     title: str\n+    priority: int = 0\n",
        "escalation": "diff --git a/tasktrack/auth.py b/tasktrack/auth.py\n@@ -1,0 +1,4 @@\n+import hashlib\n+def hash_password(password):\n+    return hashlib.md5(password.encode()).hexdigest()\n+# Security-sensitive fixture; deployment context is missing.\n",
        "auto": "diff --git a/README.md b/README.md\n@@ -1 +1 @@\n-Instalation\n+Installation\n",
    }[scenario]
    return SimpleNamespace(title=f"Evidence scenario: {scenario}", diff=diff,
                           files_changed=[{"auto": "README.md", "approval": "tasktrack/models.py",
                                           "escalation": "tasktrack/auth.py"}[scenario]],
                           head_sha="demo000000000000000000000000000000000000", author="fixture")


class FixtureLLM:
    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        prompt = messages[-1]["content"]
        if "Evidence scenario: auto" in prompt:
            return PRAnalysis(summary="Correct a documentation typo.", confidence=0.90,
                              confidence_reasoning="Isolated documentation fixture with no behavior change.")
        key = "pr_2" if "Evidence scenario: escalation" in prompt else "pr_1"
        result = PRAnalysis.model_validate(CAPTURE[key]["analysis"])
        result.confidence = 0.40 if key == "pr_2" else 0.65
        if "Reviewer Q&A:" in prompt:
            result.confidence = 0.70
            result.summary = "Refined review after reviewer answers. " + result.summary
            result.confidence_reasoning = "The reviewer provided deployment context; unresolved security findings are retained."
        return result


def record_post(url, body):
    Path("scratch").mkdir(exist_ok=True)
    with Path("scratch/report-posts.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"url": url, "body": body}, ensure_ascii=False) + "\n")


with patch.object(ex4, "fetch_pr", side_effect=fixture_pr), \
     patch.object(ex4, "get_llm", return_value=FixtureLLM()), \
     patch.object(ex4, "post_review_comment", side_effect=record_post):
    main()
    st.caption("REPORT EVIDENCE: deterministic fixtures; LLM/GitHub calls are mocked. No remote comment is posted.")
