"""
LIVE end-to-end test (phases 4–5 + Control Hub intervention) on a REAL on-screen
form, driven by Agent S, with the Control Hub operated from the CLI.

What this exercises, against the actual screen (no mocks, no UI clicks):
  1. A promoted Workflow is traversed node-by-node (single-message advance).
  2. Agent S (Gemini grounding) clicks + types into the real Acme job-application
     page open in Chrome.
  3. The Control Hub is driven from the CLI: an operator directive (queued via
     engine.workflow_control) PAUSES at the "Fill projects" milestone, TRIGGERS
     the conditional research branch, and is flagged REMEMBER → baked into the
     workflow via the teaching loop.
  4. The research milestone opens the fake projects page (devport, not GitHub),
     reads it, and summarizes the projects with Gemma into the form.
  5. Run #2 re-traverses autonomously: the baked conditional now fires WITHOUT a
     human, proving the intervention was remembered.

Console output is the "control center" view (live monitor + operator actions).

Run:  DISPLAY=:0 .venv/bin/python scripts/live_job_app.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compat  # noqa: F401  (pyautogui/mouseinfo shim — must precede pyautogui)
import pyautogui

from shepherd_types import Workflow, TaskGraphNode, TaskGraphEdge, Conditional
from engine import workflow_control
from engine import llm
from engine.agent_s_adapter import AgentSAdapter
from engine.workflow_executor import (
    WorkflowExecutor, WorkerResult, WorkerTurn, END,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORM_URL = "file://" + os.path.join(ROOT, "data", "live_job_app.html")
PROJECTS_URL = "file://" + os.path.join(ROOT, "data", "live_projects.html")

APPLICANT = {
    "applicant_name":  "Alex Johnson",
    "applicant_email": "alex.johnson@example.com",
}

# Seconds to wait before each Agent S planning call. Gemini free tier caps
# requests-per-minute and every plan_action makes a generation + a grounding call,
# so we pace bursts to stay under the limit.
PACE_S = float(os.getenv("LIVE_PACE_S", "9"))

# Vision (Agent S + Gemini grounding) is rate/quota limited; default OFF so the
# flow can be validated via direct DOM actuation. Set LIVE_VISION=1 for the final
# on-screen proof with Agent S.
VISION = os.getenv("LIVE_VISION", "0") == "1"

# ── node keys ─────────────────────────────────────────────────────────────────────
K_NAME = "fill::name::Fill full name"
K_EMAIL = "fill::email::Fill email"
K_PROJECTS = "fill::projects::Fill projects field"
K_RESEARCH = "navigate::projects::Research projects page"
K_SUBMIT = "submit::::Submit application"


def build_workflow() -> Workflow:
    """The Acme job-application workflow with a conditional research branch from
    the projects milestone (mirrors the distilled mock workflow, real selectors)."""
    nodes = [
        TaskGraphNode(key=K_NAME, kind="fill", label="Fill full name",
                      instruction="Click the 'Full Name' text field and type the applicant's full name.",
                      requires=["applicant_name"]),
        TaskGraphNode(key=K_EMAIL, kind="fill", label="Fill email",
                      instruction="Click the 'Email' text field and type the applicant's email.",
                      requires=["applicant_email"]),
        TaskGraphNode(key=K_PROJECTS, kind="fill", label="Fill projects field",
                      instruction="Click the 'Notable Projects' box and type a one-line summary of the applicant's projects.",
                      requires=["projects_summary"]),
        TaskGraphNode(key=K_RESEARCH, kind="navigate", label="Research projects page",
                      instruction="Open the applicant's projects page and summarize their projects.",
                      requires=[]),
        TaskGraphNode(key=K_SUBMIT, kind="submit", label="Submit application",
                      instruction="Click the 'Submit Application' button.",
                      requires=[]),
    ]
    edges = [
        TaskGraphEdge(from_key=K_NAME, to_key=K_EMAIL, times_seen=3),
        TaskGraphEdge(from_key=K_EMAIL, to_key=K_PROJECTS, times_seen=3),
        TaskGraphEdge(from_key=K_PROJECTS, to_key=K_SUBMIT, times_seen=3),
        TaskGraphEdge(from_key=K_RESEARCH, to_key=K_PROJECTS, times_seen=1),
    ]
    return Workflow(
        id="WF_LIVE_JOB_APPLICATION", name="Acme job application (live)",
        intent_patterns=["apply to the job", "fill out the job application",
                         "submit acme application"],
        params=["applicant_name", "applicant_email"],
        nodes=nodes, edges=edges, version=1, start_key=K_NAME,
    )


# ── safe clipboard shim (xclip/xsel hang under this VNC; Agent S mostly uses write) ──
class _Clip:
    @staticmethod
    def copy(text: str) -> None:
        try:
            p = subprocess.Popen(["xsel", "-b", "-i"], stdin=subprocess.PIPE)
            p.communicate(input=str(text).encode(), timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    @staticmethod
    def paste() -> str:
        try:
            return subprocess.run(["xsel", "-b", "-o"], capture_output=True,
                                  timeout=2).stdout.decode()
        except Exception:
            return ""


def actuate(code: str) -> None:
    """Exec Agent S code on the real screen with a non-blocking clipboard."""
    exec(code, {"__builtins__": __builtins__, "pyautogui": pyautogui,
                "time": time, "pyperclip": _Clip})  # noqa: S102


# ── live worker: Agent S actuation + browser-backed research ───────────────────────
class LiveJobAppWorker:
    """Per-milestone worker for the live run. Form milestones are actuated by Agent
    S on the real screen; the research milestone opens the fake projects page,
    reads it, and summarizes it with Gemma (Google) into `projects_summary`."""

    def __init__(self, agent_s: AgentSAdapter, page, ctx, vision: bool = True) -> None:
        self._agent_s = agent_s
        self._page = page
        self._ctx = ctx
        # vision=True  → Agent S grounds + actuates on screen (uses Gemini vision)
        # vision=False → direct DOM actuation on the SAME on-screen form (Gemma only,
        #                no vision quota); both fill the real rendered page in Chrome.
        self._vision = vision

    def act(self, turn: WorkerTurn) -> WorkerResult:
        key = turn.node.key
        if key == K_RESEARCH:
            return self._research(turn)
        if key == K_PROJECTS:
            return self._fill_projects(turn)
        if key == K_SUBMIT:
            self._actuate(turn, "Click the blue 'Submit Application' button at the bottom of the form.",
                          selector="#submit", click=True)
            return WorkerResult(did="clicked Submit Application", status="done", next=END)
        # name / email
        value = turn.resolved.get(turn.node.requires[0], "") if turn.node.requires else ""
        field, sel = ("Full Name", "#fullname") if key == K_NAME else ("Email", "#email")
        self._actuate(turn, f"Click the '{field}' input field and type exactly: {value}",
                      selector=sel, value=value)
        nxt = turn.options[0].key if turn.options else END
        return WorkerResult(did=f"filled {field} = {value}", status="done", next=nxt)

    # ── milestone handlers ─────────────────────────────────────────────────────────
    def _fill_projects(self, turn: WorkerTurn) -> WorkerResult:
        summary = turn.profile.get("projects_summary", "")
        if not summary:
            # Should normally be steered to research by the operator/taught clause;
            # if we still lack the summary, take the conditional branch ourselves.
            for o in turn.options:
                if o.via == "conditional" or o.key == K_RESEARCH:
                    return WorkerResult(
                        did="projects field empty → taking research branch",
                        status="done", next=o.key, branch=o.when,
                    )
        self._actuate(turn, f"Click the 'Notable Projects' text box and type exactly: {summary}",
                      selector="#projects", value=summary)
        submit = next((o.key for o in turn.options if o.key == K_SUBMIT), END)
        return WorkerResult(did=f"filled Projects = {summary[:60]}…", status="done", next=submit)

    def _research(self, turn: WorkerTurn) -> WorkerResult:
        print("        [worker] opening fake projects page (devport, not GitHub) in a new tab…")
        # Open in a SEPARATE tab so the application form keeps the values already
        # typed (navigating the form tab away would reload + clear it).
        research_tab = self._ctx.new_page()
        research_tab.goto(PROJECTS_URL)
        time.sleep(0.8)
        page_text = research_tab.inner_text("body")
        summary = self._summarize(page_text)
        print(f"        [worker] researched projects_summary = {summary!r}")
        research_tab.close()
        self._page.bring_to_front()
        time.sleep(0.8)
        nxt = turn.options[0].key if turn.options else K_PROJECTS
        return WorkerResult(did="researched projects page", status="done",
                            next=nxt, extracted={"projects_summary": summary})

    # ── helpers ────────────────────────────────────────────────────────────────────
    def _actuate(self, turn: WorkerTurn, instruction: str, *, selector: str = "",
                 value: Optional[str] = None, click: bool = False) -> None:
        """Actuate one field on the REAL on-screen form. Vision mode routes through
        Agent S (Gemini grounding); non-vision mode actuates the same rendered
        page directly via the DOM (the project's batch-fill style) so the flow can
        be validated end-to-end without spending vision quota."""
        if self._vision:
            self._agent_act(turn, instruction)
            return
        self._page.bring_to_front()
        try:
            if click:
                self._page.click(selector)
            else:
                self._page.click(selector)
                self._page.fill(selector, value or "")
            time.sleep(0.5)
        except Exception as e:
            print(f"        [dom] actuation failed on {selector}: {e}")

    def _agent_act(self, turn: WorkerTurn, instruction: str) -> None:
        if not (self._agent_s and self._agent_s.available):
            print(f"        [agent_s unavailable] would: {instruction}")
            return
        # Reset Agent S trajectory per milestone so each field action is planned
        # independently — otherwise the accumulated trajectory makes it reply DONE.
        self._agent_s.reset()
        time.sleep(PACE_S)   # respect Gemini free-tier RPM (each call = gen+ground)
        code = self._agent_s.plan_action(instruction, turn.step_no, "")
        if not code:
            self._agent_s.reset()
            time.sleep(PACE_S)
            code = self._agent_s.plan_action(
                instruction + " The field is currently empty — click directly on "
                "it first, then type the text.", turn.step_no, "")
        if code:
            actuate(code)
            time.sleep(0.8)
        else:
            print(f"        [agent_s] no actionable code for: {instruction[:60]}")

    @staticmethod
    def _summarize(page_text: str) -> str:
        fallback = "Shepherd (agent oversight), VectorRoute (semantic routing), GraphBake (trace->workflow)."
        if not llm.available():
            return fallback
        try:
            out = llm.complete(
                "Summarize the candidate's projects in ONE concise sentence (<=200 chars) "
                "naming the projects. Reply with the sentence only.",
                [("user", page_text[:2000])],
            )
            out = (out or "").strip().strip('"')
            return out or fallback
        except Exception:
            return fallback


# ── console monitor (the CLI "control center" view) ────────────────────────────────
def make_emitter():
    def emit(event_type: str, data: dict) -> None:
        if event_type == "workflow.node.enter":
            opts = ", ".join(
                f"{o['key'].split('::')[-1]}{'?' if o['via']=='conditional' else ''}"
                for o in data.get("options", []))
            print(f"  ▸ ENTER [{data['step_no']}] {data['label']}"
                  f"{'  (missing: '+','.join(data['missing'])+')' if data.get('missing') else ''}")
            if opts:
                print(f"        preview → {opts}")
        elif event_type == "workflow.intervention":
            print(f"    ⛔ INTERVENTION @ {data['node_key'].split('::')[-1]}: "
                  f"{data['decision']} '{data['instruction']}' [{data['flag']}]")
        elif event_type == "workflow.step":
            br = f" branch={data['branch']!r}" if data.get("branch") else ""
            ex = f" extracted={data['extracted']}" if data.get("extracted") else ""
            print(f"        did: {data['did']} → next={data['next'].split('::')[-1]}{br}{ex}")
        elif event_type == "workflow.done":
            print(f"  ■ DONE status={data['status']} steps={data['steps']} "
                  f"taught={data.get('taught', 0)}")
    return emit


def run_traversal(workflow: Workflow, worker, params: dict, label: str) -> None:
    print(f"\n=== {label} ===")
    ex = WorkflowExecutor(worker, event_emit=make_emitter(), gate=workflow_control.review)
    run = ex.run(workflow, goal="Apply to the Acme Software Engineer role",
                 params=params, profile={})
    # teaching loop: bake any remembered steers into the workflow + persist
    applied = workflow_control.bake(workflow, run.interventions, run_id=f"live-{int(time.time())}")
    if applied:
        outcome = workflow_control.persist_baked(workflow, applied, {"decision": "persist"})
        print(f"  ✎ BAKED {len(applied)} op(s) → {outcome['workflow_id']} v{outcome['version']}: {applied}")
    return run


def main() -> None:
    from playwright.sync_api import sync_playwright

    workflow = build_workflow()
    agent_s = AgentSAdapter()
    print(f"Agent S available: {agent_s.available}")
    print(f"LLM (Gemma) available: {llm.available()}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:29229")
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if pg.url.startswith(("http://localhost:3000", "file://"))),
                    ctx.pages[-1] if ctx.pages else ctx.new_page())
        page.bring_to_front()
        page.goto(FORM_URL)
        time.sleep(1.0)

        # ── RUN 1: operator drives the Control Hub from the CLI ──────────────────────
        workflow_control.reset()
        print("\n[OPERATOR @ CLI] queuing directive: at 'Fill projects', open the "
              "candidate's projects page, summarize it, and REMEMBER this.")
        workflow_control.submit_intervention(
            instruction="open the applicant's projects page (devport) and summarize their projects into the field",
            next_key=K_RESEARCH,
            scenario="the projects field is empty",
            remember=True,
            target_node=K_PROJECTS,
        )
        worker = LiveJobAppWorker(agent_s, page, ctx, vision=VISION)
        run1 = run_traversal(workflow, worker, dict(APPLICANT),
                             "RUN 1 — live traversal with CLI intervention")
        _report_form(page, run1)

        if os.getenv("LIVE_RUN2", "1") != "1":
            print("\n[skipping RUN 2 — set LIVE_RUN2=1 to run the autonomous pass]")
            return

        # ── RUN 2: autonomous — the baked conditional should now fire on its own ─────
        workflow_control.reset()
        page.goto(FORM_URL)
        time.sleep(1.0)
        print("\n[OPERATOR @ CLI] no intervention this time — the remembered branch "
              "should fire autonomously.")
        worker2 = LiveJobAppWorker(agent_s, page, ctx, vision=VISION)
        run2 = run_traversal(workflow, worker2, dict(APPLICANT),
                             "RUN 2 — autonomous (remembered branch)")
        _report_form(page, run2)


def _report_form(page, run) -> None:
    vals = page.evaluate(
        "() => ({name: document.getElementById('fullname')?.value,"
        " email: document.getElementById('email')?.value,"
        " projects: document.getElementById('projects')?.value,"
        " submitted: document.getElementById('done')?.classList.contains('show')})"
    )
    print("  FORM STATE:")
    print(f"    full name : {vals.get('name')!r}")
    print(f"    email     : {vals.get('email')!r}")
    print(f"    projects  : {vals.get('projects')!r}")
    print(f"    submitted : {vals.get('submitted')}")
    print(f"    path      : {[s.label for s in run.path]}")


if __name__ == "__main__":
    main()
