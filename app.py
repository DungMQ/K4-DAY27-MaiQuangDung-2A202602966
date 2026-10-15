"""Exercise 5: Streamlit approval UI. Run: python -m streamlit run app.py."""
from __future__ import annotations

import asyncio
import re
import uuid

import streamlit as st
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from audit.replay import list_threads
from common.db import db_path
from exercises.exercise_4_audit import build_graph, invoke_session, snapshot_result


def render_approval_card(payload: dict) -> dict | None:
    st.subheader(f"Approval requested — confidence {payload['confidence']:.0%}")
    st.caption(payload["confidence_reasoning"])
    st.markdown(payload["summary"])
    for risk in payload.get("risk_factors", []):
        st.warning(risk)
    for c in payload.get("comments", []):
        st.markdown(f"- **[{c['severity']}]** `{c['file']}:{c.get('line') or '?'}` — {c['body']}")
    with st.expander("Diff"):
        st.code(payload.get("diff_preview", ""), language="diff")

    feedback = st.text_area(
        "Feedback or complete edited review",
        help="Approve posts the proposed review. Edit posts this text as the complete replacement review.",
        key="approval_feedback",
    )
    col1, col2, col3 = st.columns(3)
    if col1.button("Approve", type="primary"):
        return {"choice": "approve", "feedback": feedback}
    if col2.button("Reject"):
        return {"choice": "reject", "feedback": feedback}
    if col3.button("Edit"):
        if feedback.strip():
            return {"choice": "edit", "feedback": feedback}
        st.error("Enter the complete edited review before choosing Edit.")
    return None


def render_escalation_card(payload: dict) -> dict | None:
    st.subheader(f"Strong escalation — confidence {payload['confidence']:.0%}")
    st.caption(payload["confidence_reasoning"])
    for risk in payload.get("risk_factors", []):
        st.error(risk)
    st.markdown(payload["summary"])
    with st.expander("Diff"):
        st.code(payload.get("diff_preview", ""), language="diff")
    st.info("Answer the questions to refine the review. You will confirm the refined review before it posts.")
    with st.form("escalation"):
        answers = {q: st.text_area(q, key=f"escalation_{i}")
                   for i, q in enumerate(payload["questions"])}
        if st.form_submit_button("Submit answers"):
            if all(a.strip() for a in answers.values()):
                return answers
            st.error("Answer every question. If context is unavailable, say so explicitly.")
    return None


async def run_graph(pr_url: str | None, thread_id: str, resume_value=None, *, load_only=False):
    """Open a fresh connection for each Streamlit rerun/event loop."""
    async with AsyncSqliteSaver.from_conn_string(db_path()) as cp:
        await cp.setup()
        graph = build_graph(cp, confirm_after_escalation=True)
        if load_only:
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            if not snapshot.values:
                raise ValueError("No saved session found for this thread.")
            return snapshot_result(snapshot)
        return await invoke_session(graph, pr_url, thread_id, resume_value)


def save_result(result: dict) -> None:
    if result.get("pr_url"):
        st.session_state.pr_url = result["pr_url"]
    pending = result.get("__interrupt__")
    st.session_state.interrupt_payload = pending[0].value if pending else None
    st.session_state.final = result if result.get("final_action") else None
    st.query_params["thread"] = st.session_state.thread_id


def show_error(exc: Exception) -> None:
    # Provider error strings can include credential fragments; never render them.
    if isinstance(exc, ValueError):
        st.error(str(exc))
    else:
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        status = status or getattr(response, "status_code", None)
        suffix = f", HTTP {status}" if status else ""
        st.error(f"Review failed ({type(exc).__name__}{suffix}). Check API credentials, network access and repository permissions. Your session is saved.")


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Recent sessions")
        try:
            sessions = asyncio.run(list_threads(display=False))
        except Exception as exc:
            show_error(exc)
            return
        if not sessions:
            st.caption("No review sessions yet. Enter a PR URL to start.")
            return
        st.dataframe([
            {"Thread": r["thread_id"], "PR": r["pr_url"], "Risk": r["worst_risk"],
             "Last event (UTC)": r["last_event"], "Events": r["events"]}
            for r in sessions
        ], hide_index=True)
        options = {r["thread_id"]: r for r in sessions}
        selected = st.selectbox("Saved session", list(options),
                                format_func=lambda t: f"{t[:8]} · {options[t]['pr_url'].split('/')[-1]}")
        if st.button("Open session"):
            try:
                result = asyncio.run(run_graph(None, selected, load_only=True))
                st.session_state.thread_id = selected
                save_result(result)
                st.session_state.pop("approval_feedback", None)
                st.rerun()
            except Exception as exc:
                show_error(exc)


def render_final(final: dict) -> None:
    action = final.get("final_action")
    if action in {"committed", "auto_committed"}:
        st.success("Review comment posted.")
        st.link_button("View review on GitHub", st.session_state.pr_url)
    elif action == "rejected":
        st.warning("Rejected — no comment posted.")
    elif action in {"commit_failed", "auto_commit_failed"}:
        st.error("The review could not be posted. Check your GitHub token's repository permissions and network connection.")
    else:
        st.info(f"Session status: {action}")
    analysis = final.get("analysis")
    if analysis:
        st.metric("Review confidence", f"{analysis.confidence:.0%}")
        st.markdown(analysis.summary)
        st.caption(analysis.confidence_reasoning)
    if final.get("posted_comment_body"):
        with st.expander("Posted review"):
            st.markdown(final["posted_comment_body"])


def main():
    load_dotenv()
    st.set_page_config(page_title="HITL PR Review", layout="wide")
    for key, default in (("thread_id", None), ("pr_url", ""), ("interrupt_payload", None), ("final", None)):
        if key not in st.session_state:
            st.session_state[key] = default

    st.title("HITL PR Review Agent")
    st.caption("Review a pull request, resolve uncertainty and keep an audit trail of every decision.")
    saved_thread = st.query_params.get("thread")
    if saved_thread and st.session_state.thread_id is None:
        try:
            result = asyncio.run(run_graph(None, saved_thread, load_only=True))
            st.session_state.thread_id = saved_thread
            save_result(result)
        except Exception as exc:
            show_error(exc)
    render_sidebar()

    with st.form("start"):
        pr_url = st.text_input("PR URL", value=st.session_state.pr_url, key="pr_url_input",
                               placeholder="https://github.com/VinUni-AI20k/PR-Demo/pull/1")
        submitted = st.form_submit_button("Run review")
    if submitted:
        if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/pull/[1-9]\d*/?", pr_url.strip()):
            st.error("Enter a GitHub pull request URL: https://github.com/owner/repo/pull/123")
        else:
            st.session_state.pr_url = pr_url.strip().rstrip("/")
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.interrupt_payload = None
            st.session_state.final = None
            for key in list(st.session_state):
                if key == "approval_feedback" or key.startswith("escalation_"):
                    del st.session_state[key]
            st.query_params["thread"] = st.session_state.thread_id
            try:
                with st.spinner("Fetching PR and analyzing the diff..."):
                    save_result(asyncio.run(run_graph(st.session_state.pr_url, st.session_state.thread_id)))
                st.rerun()
            except Exception as exc:
                show_error(exc)

    payload = st.session_state.interrupt_payload
    if payload:
        if payload["kind"] == "approval_request":
            answer = render_approval_card(payload)
        elif payload["kind"] == "escalation":
            answer = render_escalation_card(payload)
        else:
            st.error("Unknown approval request type.")
            answer = None
        if answer is not None:
            try:
                with st.spinner("Continuing review..."):
                    result = asyncio.run(run_graph(st.session_state.pr_url, st.session_state.thread_id, answer))
                    save_result(result)
                st.rerun()
            except Exception as exc:
                show_error(exc)
    elif st.session_state.final is not None:
        render_final(st.session_state.final)
    elif st.session_state.thread_id and st.button("Continue saved session"):
        try:
            with st.spinner("Restoring session..."):
                save_result(asyncio.run(run_graph(st.session_state.pr_url, st.session_state.thread_id)))
            st.rerun()
        except Exception as exc:
            show_error(exc)

    if st.session_state.thread_id:
        st.caption(f"Session: {st.session_state.thread_id}")
        with st.expander("Audit replay command"):
            st.code(f"python -m audit.replay --thread {st.session_state.thread_id}", language="bash")


if __name__ == "__main__":
    main()
