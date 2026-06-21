"""
Live validation of workflow dispatch + traversal on the mock job-application case
using the REAL model (Gemma by default, via the modular LLM layer).

Run:  .venv/bin/python scripts/validate_job_app.py

It dispatches the saved "Apply to a job" workflow, then traverses it node-by-node
with the LLM worker reasoning over the mock pages — including the FAKE projects
page (instead of GitHub). Prints the single-message advance at each milestone and
asserts the taught conditional branch is taken and the projects summary is read
off the fake page.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from shepherd_types import Intent
from engine import llm
from engine.workflow_store import WorkflowStore
from engine.workflow_executor import WorkflowExecutor, LLMWorker
from router.router import ShepherdIntentRouter
from tests.mock_job_app import build_workflow, MockJobAppEnv, RESEARCH


def main() -> int:
    print(f"[validate] provider={llm.provider()} model={llm.model_name()} available={llm.available()}")

    # Persist the workflow so the router can dispatch it like any saved workflow.
    store = WorkflowStore()
    store.save(build_workflow())

    router = ShepherdIntentRouter()
    plan = router.resolve_plan(Intent(raw_text="can you apply to this job for me", timestamp=0.0))
    print(f"[validate] router plan → kind={plan.kind} target={plan.target} "
          f"conf={plan.confidence} source={plan.source} matched={plan.matched}")
    assert plan.kind == "WORKFLOW", f"expected WORKFLOW dispatch, got {plan.kind}"

    workflow = store.get(plan.target)

    def show(name, payload):
        if name == "workflow.step":
            print(f"\n  ▸ STEP {payload['step_no']}: {payload['label']}")
            print(f"      did   : {payload['did']}")
            if payload.get("branch"):
                print(f"      branch: took conditional → {payload['branch']}")
            print(f"      next  : {payload['next']}   (options: "
                  f"{[o['key'].split('::')[-1] for o in payload['options']]})")
            if payload.get("extracted"):
                print(f"      learned: {payload['extracted']}")

    ex = WorkflowExecutor(LLMWorker(MockJobAppEnv()), event_emit=show)
    run = ex.run(workflow, goal="apply to this job",
                 params={"applicant_name": "Alex Johnson", "applicant_email": "alex@example.com"})

    print("\n[validate] ── result ──")
    print(f"  status : {run.status}")
    print(f"  path   : {[k.split('::')[-1] for k in run.visited_keys]}")
    print(f"  summary: {run.profile.get('projects_summary', '(none)')}")

    ok = (run.status == "completed"
          and RESEARCH in run.visited_keys
          and "projects_summary" in run.profile)
    note = ("took taught research branch to the fake projects page and filled the summary"
            if ok else "did not follow the expected flow")
    print(f"\n[validate] {'PASS' if ok else 'FAIL'} — {note}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
