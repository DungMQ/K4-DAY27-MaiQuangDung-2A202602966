"""Offline tests: real LangGraph execution, stubbed external APIs only."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langgraph.types import Command

from common.schemas import PRAnalysis
from exercises import exercise_1_confidence as ex1
from exercises import exercise_2_hitl as ex2
from exercises import exercise_3_escalation as ex3


def analysis(confidence=0.65, **kwargs):
    return PRAnalysis(summary="Add task priority", confidence=confidence,
                      confidence_reasoning="Migration needs confirmation", **kwargs)


def fake_pr():
    return SimpleNamespace(title="Task priority", diff="+ priority: int = 0",
                           files_changed=["tasks.py"], head_sha="abc1234", author="student")


class ConfidenceTests(unittest.TestCase):
    def test_all_boundaries(self):
        for confidence, expected in [(0, "escalate"), (0.5799, "escalate"),
                                     (0.58, "human_approval"), (0.72, "human_approval"),
                                     (0.7299, "human_approval"), (0.73, "auto_approve"),
                                     (1, "auto_approve")]:
            with self.subTest(confidence=confidence):
                self.assertEqual(ex1.node_route({"analysis": analysis(confidence)})["decision"], expected)

    def test_full_graph_routes(self):
        for confidence, expected in [(0.9, "auto_approved"),
                                     (0.65, "pending_human_approval"),
                                     (0.4, "pending_escalation")]:
            with self.subTest(confidence=confidence), patch.object(ex1, "fetch_pr", return_value=fake_pr()), \
                    patch.object(ex1, "get_llm") as llm:
                llm.return_value.with_structured_output.return_value.invoke.return_value = analysis(confidence)
                self.assertEqual(ex1.build_graph().invoke({"pr_url": "https://github.com/o/r/pull/1"})["final_action"], expected)


class HumanTests(unittest.TestCase):
    def test_approval_rejection_and_edit(self):
        for choice in ("approve", "reject", "edit"):
            with self.subTest(choice=choice), patch.object(ex2, "fetch_pr", return_value=fake_pr()), \
                    patch.object(ex2, "get_llm") as llm, patch.object(ex2, "post_review_comment") as post:
                llm.return_value.with_structured_output.return_value.invoke.return_value = analysis()
                graph = ex2.build_graph()
                cfg = {"configurable": {"thread_id": choice}}
                paused = graph.invoke({"pr_url": "https://github.com/o/r/pull/1"}, cfg)
                self.assertEqual(paused["__interrupt__"][0].value["kind"], "approval_request")
                post.assert_not_called()
                final = graph.invoke(Command(resume={"choice": choice, "feedback": "Edited review"}), cfg)
                self.assertEqual(final["final_action"], "rejected" if choice == "reject" else "committed")
                self.assertEqual(post.call_count, 0 if choice == "reject" else 1)
                if choice == "edit":
                    self.assertIn("Edited review", post.call_args.args[1])

    def test_escalation_synthesizes_with_context(self):
        initial = analysis(0.4, escalation_questions=["tasks.py: migration plan?"])
        refined = analysis(0.7)
        with patch.object(ex3, "fetch_pr", return_value=fake_pr()), patch.object(ex3, "get_llm") as llm, \
                patch.object(ex3, "post_review_comment") as post:
            llm.return_value.with_structured_output.return_value.invoke.side_effect = [initial, refined]
            graph = ex3.build_graph()
            cfg = {"configurable": {"thread_id": "escalation"}}
            paused = graph.invoke({"pr_url": "https://github.com/o/r/pull/2"}, cfg)
            self.assertEqual(paused["__interrupt__"][0].value["questions"], initial.escalation_questions)
            final = graph.invoke(Command(resume={initial.escalation_questions[0]: "Backfill before deploy"}), cfg)
            self.assertEqual(final["analysis"], refined)
            self.assertEqual(post.call_count, 1)
            prompts = str(llm.return_value.with_structured_output.return_value.invoke.call_args)
            self.assertIn("Backfill before deploy", prompts)
            self.assertIn(initial.summary, prompts)


if __name__ == "__main__":
    unittest.main()
