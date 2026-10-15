"""Streamlit interaction tests with real graph/checkpoint/audit storage."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from streamlit.testing.v1 import AppTest

from exercises import exercise_4_audit as ex4
from test_exercises import analysis, fake_pr

APP_FILE = str(Path(__file__).resolve().parents[1] / "app.py")


class UITests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"HITL_DB_PATH": str(Path(tmp.name) / "ui.db"), "GITHUB_USER": "tester"})
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
        self.at = AppTest.from_file(APP_FILE, default_timeout=15).run()

    def click(self, label, at=None):
        at = at or self.at
        button = next(b for b in at.button if b.label == label)
        button.click().run()
        self.assertEqual(len(at.exception), 0)

    def start_review(self):
        self.at.text_input(key="pr_url_input").set_value("https://github.com/o/r/pull/1")
        self.click("Run review")

    def test_approve_and_restore_after_refresh(self):
        self.start_review()
        self.assertEqual(self.at.session_state["interrupt_payload"]["kind"], "approval_request")
        self.post.assert_not_called()
        thread = self.at.session_state["thread_id"]
        fresh = AppTest.from_file(APP_FILE, default_timeout=15)
        fresh.query_params["thread"] = thread
        fresh.run()
        self.assertEqual(len(fresh.exception), 0)
        self.assertEqual(fresh.session_state["interrupt_payload"]["kind"], "approval_request")
        self.assertEqual(self.fetch.call_count, 1)
        self.click("Approve", fresh)
        self.assertEqual(fresh.session_state["final"]["final_action"], "committed")
        self.assertEqual(self.post.call_count, 1)

    def test_edit_and_empty_edit_validation(self):
        self.start_review()
        self.click("Edit")
        self.post.assert_not_called()
        self.assertTrue(self.at.error)
        self.at.text_area(key="approval_feedback").set_value("My complete edited review")
        self.click("Edit")
        self.assertEqual(self.post.call_args.args[1], "My complete edited review")

    def test_escalation_then_confirm(self):
        self.llm.ainvoke.side_effect = [analysis(0.4, escalation_questions=["Migration plan?"]), analysis(0.7)]
        self.start_review()
        self.click("Submit answers")
        self.post.assert_not_called()
        self.assertTrue(self.at.error)
        self.at.text_area(key="escalation_0").set_value("Backfill before deployment")
        self.click("Submit answers")
        self.assertEqual(self.at.session_state["interrupt_payload"]["kind"], "approval_request")
        self.post.assert_not_called()
        self.click("Approve")
        self.assertEqual(self.post.call_count, 1)

    def test_auto_post_failure_has_no_success_banner(self):
        self.llm.ainvoke.return_value = analysis(0.9)
        self.post.side_effect = RuntimeError("Forbidden")
        self.start_review()
        self.assertFalse(self.at.success)
        self.assertTrue(self.at.error)

    def test_invalid_url_does_not_fetch(self):
        self.at.text_input(key="pr_url_input").set_value("https://example.com/pull/1")
        self.click("Run review")
        self.fetch.assert_not_called()
        self.assertTrue(self.at.error)


if __name__ == "__main__":
    unittest.main()
