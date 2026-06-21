"""
End-to-end REMOTE-OPERATOR test for the unified Command Center.

Runs the *operated machine* side: it dials OUT to the coordinator over the relay
(exactly as a real agent would), opens the Acme job-application form in Chrome,
and traverses the workflow milestone-by-milestone — streaming workflow.* events
and live screen frames UP to the coordinator. A remote operator then drives the
Next.js Command Center (localhost:3000/remote): watches the live screen beside
the on-the-fly workflow graph, and at the "Fill projects" milestone the run
PAUSES and awaits a directive. The operator picks the research branch, checks
"remember this", and sends it; the steer comes back DOWN over the relay into the
milestone executor, which branches to research, fills, and submits — then bakes
the remembered branch into the workflow.

This exercises the full path: agent → coordinator → UI → command → engine gate.

Run:  DISPLAY=:0 .venv/bin/python scripts/remote_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace

# Configure the relay BEFORE importing config (module-level constants bind at import).
os.environ.setdefault("COORDINATOR_URL", "ws://localhost:8770")
os.environ.setdefault("AGENT_PAIRING_CODE", "DEMO")
os.environ.setdefault("AGENT_ID", "operated-box")
os.environ.setdefault("AGENT_NAME", "Operated Machine")
os.environ.setdefault("AGENT_HOST", "vm-desktop")
os.environ.setdefault("DISPLAY", ":0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compat  # noqa: F401,E402  (pyautogui shim — must precede pyautogui)

from dashboard.events import event_bus  # noqa: E402
from engine import llm, workflow_control  # noqa: E402
from engine.workflow_executor import WorkflowExecutor  # noqa: E402
from services.relay_client import start_relay_client  # noqa: E402
from shepherd_types import Workflow, TaskGraphNode, TaskGraphEdge  # noqa: E402

# reuse the live form worker (DOM actuation, no vision quota)
from scripts.live_job_app import LiveJobAppWorker, FORM_URL, APPLICANT  # noqa: E402

K_NAME = "fill::name::Fill full name"
K_EMAIL = "fill::email::Fill email"
K_PROJECTS = "fill::projects::Fill projects field"
K_RESEARCH = "navigate::projects::Research projects page"
K_SUBMIT = "submit::::Submit application"


def build_workflow() -> Workflow:
    nodes = [
        TaskGraphNode(key=K_NAME, kind="fill", label="Fill full name",
                      instruction="Type the applicant's full name.",
                      requires=["applicant_name"]),
        TaskGraphNode(key=K_EMAIL, kind="fill", label="Fill email",
                      instruction="Type the applicant's email.",
                      requires=["applicant_email"]),
        TaskGraphNode(key=K_PROJECTS, kind="fill", label="Fill projects field",
                      instruction="Type a one-line summary of the applicant's projects.",
                      requires=["projects_summary"]),
        TaskGraphNode(key=K_RESEARCH, kind="navigate", label="Research projects page",
                      instruction="Open the applicant's projects page and summarize their projects.",
                      requires=[]),
        TaskGraphNode(key=K_SUBMIT, kind="submit", label="Submit application",
                      instruction="Click Submit Application.", requires=[]),
    ]
    edges = [
        TaskGraphEdge(from_key=K_NAME, to_key=K_EMAIL, times_seen=3),
        TaskGraphEdge(from_key=K_EMAIL, to_key=K_PROJECTS, times_seen=3),
        # both successors of projects are previewable options the operator can pick:
        TaskGraphEdge(from_key=K_PROJECTS, to_key=K_RESEARCH, times_seen=1),
        TaskGraphEdge(from_key=K_PROJECTS, to_key=K_SUBMIT, times_seen=3),
        TaskGraphEdge(from_key=K_RESEARCH, to_key=K_PROJECTS, times_seen=1),
    ]
    return Workflow(
        id="WF_LIVE_JOB_APPLICATION", name="Acme job application (live)",
        intent_patterns=["apply to the job", "fill out the job application"],
        params=["applicant_name", "applicant_email"],
        nodes=nodes, edges=edges, version=1, start_key=K_NAME,
    )


def _start_bus_loop() -> asyncio.AbstractEventLoop:
    """Give the event bus a loop so its subscribers (the relay) actually fire —
    mirrors what the dashboard server does on startup."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    event_bus.set_async_loop(loop)
    return loop


def make_emitter():
    def emit(event_type: str, data: dict) -> None:
        # forward up to the coordinator (relay subscribes to the bus) ...
        event_bus.emit(event_type, data)
        # ... and print a local "operated box" trace.
        if event_type == "workflow.node.enter":
            opts = ", ".join(o["key"].split("::")[-1] for o in data.get("options", []))
            print(f"  ENTER [{data['step_no']}] {data['label']}  → preview: {opts}")
        elif event_type == "workflow.intervention":
            print(f"  STEER @ {data['node_key'].split('::')[-1]}: '{data['instruction']}' "
                  f"next={data.get('decision')} [{data.get('flag')}]")
        elif event_type == "workflow.step":
            print(f"     did: {data['did']} → next={str(data['next']).split('::')[-1]}")
        elif event_type == "workflow.done":
            print(f"  DONE status={data['status']} steps={data['steps']}")
    return emit


def gate(turn):
    """Pause-and-await at the projects milestone when the summary is missing, so
    the remote operator is prompted to steer it from the Command Center. Keeps
    re-awaiting (the bounded review window can lapse) until the operator sends a
    directive, so there's no rush to drive the UI."""
    if turn.node.key == K_PROJECTS and "projects_summary" not in turn.profile:
        while True:
            workflow_control.request_pause()
            iv = workflow_control.review(turn)
            if iv is not None:
                return iv
            print("     … still awaiting operator directive at 'Fill projects' "
                  "(drive it from the Command Center)", flush=True)
    return workflow_control.review(turn)


def main() -> None:
    from playwright.sync_api import sync_playwright

    _start_bus_loop()
    relay = start_relay_client(SimpleNamespace(_mode="LIVE", request_halt=lambda: None),
                               remote_intents=__import__("queue").Queue())
    print(f"[relay] dialing coordinator at {os.environ['COORDINATOR_URL']} "
          f"as session '{os.environ['AGENT_PAIRING_CODE']}' …")
    print(f"[llm] Gemma available: {llm.available()}")
    time.sleep(2.0)  # let the relay connect + register before we start

    workflow = build_workflow()
    workflow_control.reset()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:29229")
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages
                     if pg.url.startswith(("http://localhost:3000", "file://"))),
                    ctx.pages[-1] if ctx.pages else ctx.new_page())
        page.bring_to_front()
        page.goto(FORM_URL)
        time.sleep(1.0)

        worker = LiveJobAppWorker(agent_s=None, page=page, ctx=ctx, vision=False)
        ex = WorkflowExecutor(worker, event_emit=make_emitter(), gate=gate)
        print("\n=== Operated machine ready. Drive it from the Command Center "
              "(localhost:3000/remote, session DEMO). ===")
        print("    It will PAUSE at 'Fill projects' and await your steer "
              "(pick 'Research projects page' + check 'remember').\n")
        run = ex.run(workflow, goal="Apply to the Acme Software Engineer role",
                     params=dict(APPLICANT), profile={})

        applied = workflow_control.bake(workflow, run.interventions,
                                        run_id=f"remote-{int(time.time())}")
        if applied:
            print(f"\n  ✎ BAKED {len(applied)} op(s); awaiting operator's persist choice…")
            decision = workflow_control.await_finalize(workflow, applied)
            outcome = workflow_control.persist_baked(workflow, applied, decision)
            print(f"  ✎ {outcome['action']} → {outcome['workflow_id']} v{outcome['version']}")

        vals = page.evaluate(
            "() => ({name: document.getElementById('fullname')?.value,"
            " email: document.getElementById('email')?.value,"
            " projects: document.getElementById('projects')?.value,"
            " submitted: document.getElementById('done')?.classList.contains('show')})")
        print(f"\n  FORM STATE: {vals}")
        print(f"  PATH: {[s.label for s in run.path]}  status={run.status}")
        print("\n  (leaving the relay up for ~30s so the final state streams to the UI…)")
        time.sleep(30)


if __name__ == "__main__":
    main()
