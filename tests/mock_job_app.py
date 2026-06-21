"""
Mock job-application scenario for validating workflow dispatch + traversal.

Builds a small dispatchable Workflow (open → fill details → fill projects →
submit) whose "fill projects" milestone carries a TAUGHT conditional: if the
projects field is empty, branch to a RESEARCH milestone that reads a FAKE
projects page (standing in for GitHub) and fills the summary in. The
`MockJobAppEnv` returns the page text the worker "sees" at each milestone, so the
LLM/heuristic worker can follow the flow without a real browser.

Shared by the deterministic test (tests/test_workflow_dispatch.py) and the live
Gemma validation (scripts run manually).
"""
from __future__ import annotations

from shepherd_types import Workflow, TaskGraphNode, TaskGraphEdge, Conditional
from engine.workflow_executor import WorkerTurn

# Node keys (kind::value::label)
OPEN = "open::::Open the job application"
FILL = "fill::details::Fill applicant details"
PROJECTS = "fill::projects::Fill the Projects field"
RESEARCH = "research::projects::Research the projects page"
SUBMIT = "submit::::Submit the application"

# The FAKE projects page (instead of GitHub) with mock data the worker must read.
FAKE_PROJECTS_PAGE = (
    "Projects page — devport.example.com/alex (NOT github)\n"
    "Recent projects by Alex Johnson:\n"
    "  1. Shepherd — local oversight layer for desktop AI agents (Python, FastAPI)\n"
    "  2. VectorRoute — semantic intent router built on Redis vectorsets\n"
    "  3. GraphBake — workflow distillation + teaching loop\n"
    "projects_summary: Built Shepherd (agent oversight), VectorRoute (semantic "
    "routing), and GraphBake (workflow distillation)"
)


def build_workflow() -> Workflow:
    nodes = [
        TaskGraphNode(key=OPEN, kind="open", label="Open the job application",
                      instruction="Open the job application page", source="observed"),
        TaskGraphNode(key=FILL, kind="fill", label="Fill applicant details",
                      instruction="Fill the name and email fields",
                      requires=["applicant_name", "applicant_email"], source="observed"),
        TaskGraphNode(
            key=PROJECTS, kind="fill", label="Fill the Projects field",
            instruction="Fill the Projects field on the form",
            requires=["projects_summary"], source="taught",
            conditionals=[Conditional(
                when="the projects field is empty or you don't have the applicant's projects",
                do="go to the applicant's projects page, read their recent projects, and fill them in",
                goto=RESEARCH, source="taught")],
        ),
        TaskGraphNode(key=RESEARCH, kind="research", label="Research the projects page",
                      instruction="Open the projects page, read the recent projects, "
                                  "summarize them, and fill the Projects field",
                      source="taught"),
        TaskGraphNode(key=SUBMIT, kind="submit", label="Submit the application",
                      instruction="Click submit", source="observed"),
    ]
    edges = [
        TaskGraphEdge(from_key=OPEN, to_key=FILL, times_seen=3),
        TaskGraphEdge(from_key=FILL, to_key=PROJECTS, times_seen=3),
        TaskGraphEdge(from_key=PROJECTS, to_key=SUBMIT, times_seen=2),
        TaskGraphEdge(from_key=PROJECTS, to_key=RESEARCH, times_seen=1,
                      condition="the projects field is empty"),
        TaskGraphEdge(from_key=RESEARCH, to_key=SUBMIT, times_seen=1),
    ]
    return Workflow(
        id="WF_JOB_APPLICATION", name="Apply to a job",
        intent_patterns=["apply to this job", "job application", "apply for the job",
                         "fill out the application"],
        params=["applicant_name", "applicant_email"],
        nodes=nodes, edges=edges, version=1, start_key=OPEN,
    )


class MockJobAppEnv:
    """Returns the page text the worker perceives at each milestone — including the
    fake projects page at the RESEARCH node so the worker can extract the summary."""

    def observe(self, turn: WorkerTurn) -> str:
        key = turn.node.key
        if key == OPEN:
            return "A browser is open at jobs.example.com. The job application form is loaded."
        if key == FILL:
            name = turn.profile.get("applicant_name", "(unknown)")
            email = turn.profile.get("applicant_email", "(unknown)")
            return (f"Application form with fields: Full Name, Email, Projects.\n"
                    f"Name and Email can be filled from known values "
                    f"(name={name}, email={email}).")
        if key == PROJECTS:
            have = "projects_summary" in turn.profile
            state = "already has a summary" if have else "is EMPTY"
            return (f"The form's Projects field {state}. "
                    f"It requires a projects summary to continue.")
        if key == RESEARCH:
            return FAKE_PROJECTS_PAGE
        if key == SUBMIT:
            return "The application is complete. A Submit button is visible."
        return "(no observation)"
