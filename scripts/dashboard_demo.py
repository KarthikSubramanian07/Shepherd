"""
In-process Control Hub demo: runs the dashboard server AND a live workflow
traversal in the same process so the traversal's events reach the dashboard's
event bus and render in the browser (Workflow tab). No Agent S / vision — a
scripted worker actuates, so this exercises the monitor + steer + remember loop
purely over the dashboard.

Run:  DISPLAY=:0 .venv/bin/python scripts/dashboard_demo.py
Then open http://127.0.0.1:8765  → "Workflow" tab.

Modes (env DEMO):
  scripted (default) — an intervention is queued at the projects milestone and
                       baked (remember=True); the run advances on a timer so the
                       traversal animates live.
  manual            — no queued directive; pause/steer entirely from the UI.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compat  # noqa: F401

from engine import workflow_control
from engine.workflow_executor import WorkflowExecutor, WorkerResult, END
from dashboard.events import event_bus
from dashboard import server as dash

from scripts.live_job_app import build_workflow, K_PROJECTS, K_RESEARCH, K_SUBMIT

STEP_PACE_S = float(os.getenv("DEMO_PACE_S", "3.0"))
MODE = os.getenv("DEMO", "scripted")

SUMMARY = ("Built Shepherd (agent oversight), VectorRoute (semantic routing), "
           "and GraphBake (workflow distillation).")


class ScriptedWorker:
    """Actuates each milestone deterministically with a pause so the traversal is
    watchable in the dashboard. Research yields a mock projects summary."""

    def act(self, turn):
        time.sleep(STEP_PACE_S)
        key = turn.node.key
        if key == K_RESEARCH:
            return WorkerResult(did="opened the projects page and summarized it",
                                status="done",
                                next=(turn.options[0].key if turn.options else K_PROJECTS),
                                extracted={"projects_summary": SUMMARY})
        if key == K_PROJECTS:
            if turn.profile.get("projects_summary"):
                nxt = next((o.key for o in turn.options if o.key == K_SUBMIT), END)
                return WorkerResult(did=f"filled Projects = {SUMMARY[:48]}…",
                                    status="done", next=nxt)
            # empty + no human steer → take the research branch if one is previewed
            for o in turn.options:
                if o.via == "conditional" or o.key == K_RESEARCH:
                    return WorkerResult(did="projects empty → research branch",
                                        status="done", next=o.key, branch=o.when)
            return WorkerResult(did="projects empty (awaiting steer)", status="done",
                                next=(turn.options[0].key if turn.options else END))
        if key == K_SUBMIT:
            return WorkerResult(did="clicked Submit Application", status="done", next=END)
        # name / email
        val = turn.resolved.get(turn.node.requires[0], "") if turn.node.requires else ""
        return WorkerResult(did=f"filled {turn.node.label} = {val}", status="done",
                            next=(turn.options[0].key if turn.options else END))


def _run_traversal(workflow, applicant):
    # Give the server a moment to bind + set its async loop.
    time.sleep(2.0)
    if MODE == "manual":
        print("[demo] manual mode — pausing; steer from the UI")
        workflow_control.request_pause()
    if MODE == "scripted":
        print("[demo] queuing a REMEMBER intervention at the projects milestone")
        workflow_control.submit_intervention(
            instruction="open the applicant's projects page and summarize it into the field",
            next_key=K_RESEARCH, scenario="the projects field is empty",
            remember=True, target_node=K_PROJECTS,
        )
    ex = WorkflowExecutor(ScriptedWorker(), event_emit=event_bus.emit,
                          gate=workflow_control.review)
    run = ex.run(workflow, goal="apply to the job", params=dict(applicant))

    applied = workflow_control.bake(workflow, run.interventions, run_id="demo")
    if applied:
        workflow.version += 1
        event_bus.emit("workflow.baked", {
            "workflow_id": workflow.id, "version": workflow.version, "ops": applied,
        })
    print(f"[demo] run status={run.status} path={[r.label for r in run.path]}")


def main():
    workflow = build_workflow()
    applicant = {"applicant_name": "Alex Johnson",
                 "applicant_email": "alex.johnson@example.com"}
    workflow_control.reset()
    threading.Thread(target=_run_traversal, args=(workflow, applicant), daemon=True).start()
    print(f"[demo] dashboard → http://127.0.0.1:{dash.DASHBOARD_PORT}  (open the 'Workflow' tab)")
    dash.start_dashboard()   # blocks (uvicorn)


if __name__ == "__main__":
    main()
