"""SQLite persistence and audit tests, without network calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from common.db import replay_events
from audit.replay import list_threads
from exercises import exercise_4_audit as ex4
from test_exercises import analysis, fake_pr


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "audit.db")
        env = patch.dict(os.environ, {"HITL_DB_PATH": self.db, "GITHUB_USER": "reviewer"})
        env.start()
        self.addCleanup(env.stop)
        fetch = patch.object(ex4, "fetch_pr", return_value=fake_pr())
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        llm = patch.object(ex4, "get_llm")
        self.llm = llm.start().return_value.with_structured_output.return_value
        self.llm.ainvoke = AsyncMock(return_value=analysis())
        self.addCleanup(llm.stop)
        post = patch.object(ex4, "post_review_comment")
        self.post = post.start()
        self.addCleanup(post.stop)

    async def invoke(self, thread, value, confirm=False):
        async with AsyncSqliteSaver.from_conn_string(self.db) as cp:
            await cp.setup()
            graph = ex4.build_graph(cp, confirm_after_escalation=confirm)
            return await graph.ainvoke(value, {"configurable": {"thread_id": thread}})

    async def test_reopen_and_resume_without_duplicate_requests(self):
        start = {"pr_url": "https://github.com/o/r/pull/1", "thread_id": "durable"}
        first = await self.invoke("durable", start)
        self.assertEqual(first["__interrupt__"][0].value["kind"], "approval_request")
        final = await self.invoke("durable", Command(resume={"choice": "approve", "feedback": "Reviewed"}))
        self.assertEqual(final["final_action"], "committed")
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.fetch.call_count, 1)
        rows = await replay_events("durable")
        self.assertEqual([r["action"] for r in rows],
                         ["fetch_pr", "analyze", "route", "human_approval", "human_approval", "commit"])
        self.assertEqual([r["decision"] for r in rows if r["action"] == "human_approval"], ["pending", "approve"])
        self.assertEqual(rows[-2]["reviewer_id"], "reviewer")
        self.assertTrue(all(r["execution_time_ms"] >= 0 for r in rows))

    async def test_reject_and_edit(self):
        for choice in ("reject", "edit"):
            with self.subTest(choice=choice):
                await self.invoke(choice, {"pr_url": "https://github.com/o/r/pull/1", "thread_id": choice})
                before = self.post.call_count
                final = await self.invoke(choice, Command(resume={"choice": choice, "feedback": "Edited body"}))
                self.assertEqual(final["final_action"], "rejected" if choice == "reject" else "committed")
                self.assertEqual(self.post.call_count - before, 0 if choice == "reject" else 1)
                if choice == "edit":
                    self.assertEqual(final["posted_comment_body"], "Edited body")

    async def test_ui_escalation_requires_final_confirmation(self):
        self.llm.ainvoke.side_effect = [analysis(0.4, escalation_questions=["Migration?"]), analysis(0.7)]
        await self.invoke("qa", {"pr_url": "https://github.com/o/r/pull/2", "thread_id": "qa"}, True)
        refined = await self.invoke("qa", Command(resume={"Migration?": "Backfill first"}), True)
        self.assertEqual(refined["__interrupt__"][0].value["kind"], "approval_request")
        self.post.assert_not_called()
        final = await self.invoke("qa", Command(resume={"choice": "reject"}), True)
        self.assertEqual(final["final_action"], "rejected")
        self.post.assert_not_called()
        rows = await replay_events("qa")
        self.assertEqual(len([r for r in rows if r["action"] == "escalate"]), 2)
        self.assertEqual(next(r["confidence"] for r in rows if r["action"] == "synthesize"), 0.7)

    async def test_cli_escalation_posts_refined_review(self):
        self.llm.ainvoke.side_effect = [analysis(0.4, escalation_questions=["Migration?"]), analysis(0.7)]
        await self.invoke("cli", {"pr_url": "https://github.com/o/r/pull/2", "thread_id": "cli"})
        final = await self.invoke("cli", Command(resume={"Migration?": "Backfill first"}))
        self.assertEqual(final["final_action"], "committed")
        self.assertIn("Backfill first", final["posted_comment_body"])

    async def test_failed_auto_post_is_not_success(self):
        self.llm.ainvoke.return_value = analysis(0.9)
        self.post.side_effect = RuntimeError("No permission")
        final = await self.invoke("fail", {"pr_url": "https://github.com/o/r/pull/1", "thread_id": "fail"})
        self.assertEqual(final["final_action"], "auto_commit_failed")
        self.assertIsNone(final.get("posted_comment_body"))
        rows = await replay_events("fail")
        self.assertIn("failed", rows[-1]["reason"])

    async def test_list_sessions_reports_highest_risk(self):
        self.llm.ainvoke.return_value = analysis(0.4)
        await self.invoke("risk", {"pr_url": "https://github.com/o/r/pull/2", "thread_id": "risk"})
        rows = await list_threads(display=False)
        self.assertEqual(rows[0]["worst_risk"], "high")

    async def test_session_helper_restores_without_restarting(self):
        url = "https://github.com/o/r/pull/1"
        await self.invoke("helper", {"pr_url": url, "thread_id": "helper"})
        async with AsyncSqliteSaver.from_conn_string(self.db) as cp:
            graph = ex4.build_graph(cp)
            restored = await ex4.invoke_session(graph, None, "helper")
            self.assertEqual(restored["__interrupt__"][0].value["kind"], "approval_request")
            self.assertEqual(self.fetch.call_count, 1)
            with self.assertRaisesRegex(ValueError, "different PR"):
                await ex4.invoke_session(graph, "https://github.com/o/r/pull/99", "helper")
            final = await ex4.invoke_session(graph, None, "helper", {"choice": "approve"})
            self.assertEqual(final["final_action"], "committed")
            restored_final = await ex4.invoke_session(graph, None, "helper")
            self.assertEqual(restored_final["final_action"], "committed")
            self.assertEqual(self.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
