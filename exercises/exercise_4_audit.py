"""Exercise 4 — Structured SQLite audit trail + durable checkpointer.

Zero setup — SQLite stores everything in a single file (`./hitl_audit.db`).
The audit_events schema is created automatically on first connection.

Goals:

1. Use AsyncSqliteSaver so the graph can resume after a crash.
2. Define and emit an `AuditEntry` (common/schemas.py) for every meaningful step,
   so the full session can be replayed.
3. Verify with `uv run python -m audit.replay --thread <id>`.

Request events are written in separate nodes before the interrupt nodes.
LangGraph re-executes interrupted nodes on resume; this keeps pending audit
rows from being duplicated. The UI additionally confirms refined reviews.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from rich.console import Console

from common.db import db_path, write_audit_event
from common.github import fetch_pr, post_review_comment
from common.llm import get_llm
from common.schemas import (
    AUTO_APPROVE_THRESHOLD,
    ESCALATE_THRESHOLD,
    AuditEntry,
    PRAnalysis,
    ReviewState,
    risk_level_for,
)
from exercises.exercise_1_confidence import review_messages
from exercises.exercise_2_hitl import approval_payload, review_serializer, validate_approval
from exercises.exercise_3_escalation import (
    _render_comment_body, handle_interrupt, synthesis_messages, validate_answers,
)


console = Console()
AGENT_ID = "pr-review-agent@v0.1"


async def audit(state, entry: AuditEntry) -> None:
    """Write one structured AuditEntry row to the `audit_events` table.

    `thread_id` and `pr_url` are taken from `state` so callers only build
    the entry itself.
    """
    await write_audit_event(thread_id=state["thread_id"], pr_url=state["pr_url"], entry=entry)


def event(state, action, t0, decision, reason, *, reviewer_id=None) -> AuditEntry:
    confidence = state["analysis"].confidence
    return AuditEntry(
        agent_id=AGENT_ID, action=action, confidence=confidence,
        risk_level=risk_level_for(confidence), reviewer_id=reviewer_id,
        decision=decision, reason=reason,
        execution_time_ms=int((time.monotonic() - t0) * 1000),
    )


# ─── Reference example — read this carefully ───────────────────────────────
async def node_fetch_pr(state):
    console.print("[cyan]→ fetch_pr[/cyan]")
    t0 = time.monotonic()
    with console.status("[dim]Fetching PR from GitHub...[/dim]"):
        pr = fetch_pr(state["pr_url"])
    console.print(f"  [green]✓[/green] {len(pr.files_changed)} files, head {pr.head_sha[:7]}")
    # We've only fetched the diff, not analyzed it. So:
    #   - confidence is unknown → 0.0
    #   - risk_level can't be derived from confidence yet → "med" as neutral default
    #   - decision is "pending" — nothing has been decided
    #   - reviewer_id is None — no human is involved at this stage
    #   - reason is a short human-readable summary of what happened
    await audit(state, AuditEntry(
        agent_id=AGENT_ID,
        action="fetch_pr",
        confidence=0.0,
        risk_level="med",
        decision="pending",
        reason=f"Fetched {len(pr.files_changed)} files, head={pr.head_sha[:7]}",
        execution_time_ms=int((time.monotonic() - t0) * 1000),
    ))
    return {
        "pr_title": pr.title,
        "pr_diff": pr.diff,
        "pr_files": pr.files_changed,
        "pr_head_sha": pr.head_sha,
    }
# ───────────────────────────────────────────────────────────────────────────


async def node_analyze(state):
    console.print("[cyan]→ analyze[/cyan]")
    t0 = time.monotonic()
    llm = get_llm().with_structured_output(PRAnalysis)
    with console.status("[dim]LLM reviewing the diff...[/dim]"):
        a: PRAnalysis = await llm.ainvoke(review_messages(state))
    console.print(f"  [green]✓[/green] confidence={a.confidence:.0%}, {len(a.comments)} comment(s)")
    await audit(state, event({**state, "analysis": a}, "analyze", t0, "pending", a.confidence_reasoning))
    return {"analysis": a}


async def node_route(state):
    console.print("[cyan]→ route[/cyan]")
    t0 = time.monotonic()
    c = state["analysis"].confidence
    if c >= AUTO_APPROVE_THRESHOLD:
        decision = "auto_approve"
    elif c < ESCALATE_THRESHOLD:
        decision = "escalate"
    else:
        decision = "human_approval"
    console.print(f"  [green]✓[/green] decision=[bold]{decision}[/bold] (confidence={c:.0%})")
    audit_decision = {"auto_approve": "auto", "human_approval": "pending", "escalate": "escalate"}[decision]
    await audit(state, event(state, "route", t0, audit_decision, f"Routed to {decision}"))
    return {"decision": decision}


async def node_request_approval(state):
    t0 = time.monotonic()
    await audit(state, event(state, "human_approval", t0, "pending", "Waiting for reviewer approval"))
    return {}


async def node_human_approval(state):
    t0 = time.monotonic()
    resp = validate_approval(interrupt(approval_payload(state)))
    await audit(state, event(state, "human_approval", t0, resp["choice"], resp["feedback"],
                             reviewer_id=os.environ.get("GITHUB_USER") or None))
    return {"human_choice": resp.get("choice"), "human_feedback": resp.get("feedback")}


def _post(state) -> str:
    try:
        post_review_comment(state["pr_url"], _render_comment_body(state))
        console.print(f"  [green]✓[/green] posted comment to {state['pr_url']}")
        return "committed"
    except Exception as e:
        console.print(f"  [red]✗[/red] post failed: {e}")
        return "commit_failed"


async def node_commit(state):
    console.print("[cyan]→ commit[/cyan]")
    t0 = time.monotonic()
    # An explicit rejection always wins, including after escalation in the UI.
    choice = state.get("human_choice")
    if choice in {"approve", "edit"} or (state.get("escalation_answers") and choice is None):
        action = _post(state)
    else:
        console.print(f"  [yellow]·[/yellow] skipping comment (choice={state.get('human_choice')})")
        action = "rejected"
    decision = choice or ("escalate" if state.get("escalation_answers") else "reject")
    await audit(state, event(state, "commit", t0, decision,
                             f"Comment {action}", reviewer_id=os.environ.get("GITHUB_USER") or None))
    return {"final_action": action,
            "posted_comment_body": _render_comment_body(state) if action == "committed" else None}


async def node_auto_approve(state):
    console.print("[cyan]→ auto_approve[/cyan]  [dim]high confidence — posting directly[/dim]")
    t0 = time.monotonic()
    action = _post(state)
    await audit(state, event(state, "auto_approve", t0, "auto", f"Comment {action}"))
    return {"final_action": f"auto_{action}",
            "posted_comment_body": _render_comment_body(state) if action == "committed" else None}


async def node_request_escalation(state):
    t0 = time.monotonic()
    questions = state["analysis"].escalation_questions or ["What is the intent of this PR?"]
    await audit(state, event(state, "escalate", t0, "pending", "Questions: " + " | ".join(questions)))
    return {}


async def node_escalate(state):
    t0 = time.monotonic()
    a = state["analysis"]
    questions = a.escalation_questions or ["What is the intent of this PR?"]

    answers = interrupt({
        "kind": "escalation",
        "pr_url": state["pr_url"],
        "confidence": a.confidence,
        "confidence_reasoning": a.confidence_reasoning,
        "summary": a.summary,
        "risk_factors": a.risk_factors,
        "questions": questions,
        "diff_preview": state["pr_diff"],
    })

    answers = validate_answers(questions, answers)
    reason = "\n".join(f"Q: {q}\nA: {answer}" for q, answer in answers.items())
    await audit(state, event(state, "escalate", t0, "escalate", reason,
                             reviewer_id=os.environ.get("GITHUB_USER") or None))
    return {"escalation_answers": answers}


async def node_synthesize(state):
    console.print("[cyan]→ synthesize[/cyan]")
    t0 = time.monotonic()
    llm = get_llm().with_structured_output(PRAnalysis)
    with console.status("[dim]LLM refining review with reviewer answers...[/dim]"):
        refined: PRAnalysis = await llm.ainvoke(synthesis_messages(state))
    console.print(f"  [green]✓[/green] refined confidence={refined.confidence:.0%}")
    await audit(state, event({**state, "analysis": refined}, "synthesize", t0, "pending", refined.confidence_reasoning))
    return {"analysis": refined}


def build_graph(checkpointer, *, confirm_after_escalation=False):
    checkpointer.serde = review_serializer()
    g = StateGraph(ReviewState)
    for name, fn in [
        ("fetch_pr", node_fetch_pr), ("analyze", node_analyze), ("route", node_route),
        ("auto_approve", node_auto_approve), ("human_approval", node_human_approval),
        ("commit", node_commit), ("escalate", node_escalate), ("synthesize", node_synthesize),
        ("request_approval", node_request_approval), ("request_escalation", node_request_escalation),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "fetch_pr")
    g.add_edge("fetch_pr", "analyze")
    g.add_edge("analyze", "route")
    g.add_conditional_edges(
        "route", lambda s: s["decision"],
        {"auto_approve": "auto_approve", "human_approval": "request_approval", "escalate": "request_escalation"},
    )
    g.add_edge("auto_approve", END)
    g.add_edge("request_approval", "human_approval")
    g.add_edge("request_escalation", "escalate")
    g.add_edge("human_approval", "commit")
    g.add_edge("commit", END)
    g.add_edge("escalate", "synthesize")
    g.add_edge("synthesize", "request_approval" if confirm_after_escalation else "commit")
    return g.compile(checkpointer=checkpointer)


def snapshot_result(snapshot) -> dict:
    result = dict(snapshot.values)
    interrupts = tuple(i for task in snapshot.tasks for i in task.interrupts)
    if interrupts:
        result["__interrupt__"] = interrupts
    return result


async def invoke_session(graph, pr_url, thread_id, resume_value=None):
    """Start a new session, restore a paused/completed one, or resume pending work."""
    cfg = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(cfg)
    if snapshot.values:
        if pr_url and snapshot.values["pr_url"] != pr_url:
            raise ValueError("This thread belongs to a different PR. Start a new session.")
        if resume_value is not None:
            if not snapshot.next:
                raise ValueError("This session is already complete.")
            return await graph.ainvoke(Command(resume=resume_value), cfg)
        result = snapshot_result(snapshot)
        if result.get("__interrupt__") or not snapshot.next:
            return result
        return await graph.ainvoke(None, cfg)
    if resume_value is not None or not pr_url:
        raise ValueError("No checkpoint found. Start with a PR URL.")
    return await graph.ainvoke({"pr_url": pr_url, "thread_id": thread_id}, cfg)


async def run(pr_url: str | None, thread_id: str | None):
    thread_id = thread_id or str(uuid.uuid4())
    console.rule("[bold]Exercise 4 — SQLite audit trail[/bold]")
    console.print(f"[dim]PR: {pr_url}[/dim]")
    console.print(f"[dim]thread_id = {thread_id}[/dim]\n")

    async with AsyncSqliteSaver.from_conn_string(db_path()) as cp:
        await cp.setup()
        app = build_graph(cp)
        result = await invoke_session(app, pr_url, thread_id)
        while result.get("__interrupt__"):
            payload = result["__interrupt__"][0].value
            result = await invoke_session(app, pr_url, thread_id, handle_interrupt(payload))

        console.rule("Final")
        console.print(f"final_action = {result.get('final_action')}")
        console.print(f"\n[dim]Replay:[/dim] uv run python -m audit.replay --thread {thread_id}")


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--pr", help="PR URL (optional when resuming --thread)")
    p.add_argument("--thread", help="Resume an existing thread")
    args = p.parse_args()
    if not args.pr and not args.thread:
        p.error("provide --pr or --thread")
    asyncio.run(run(args.pr, args.thread))


if __name__ == "__main__":
    main()
