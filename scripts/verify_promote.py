#!/usr/bin/env python3
"""One-time verification script for the auto-promote feature.

NOT part of the automated test suite (does not consume tokens).
Run manually to inspect the quality of promoted workflow output:

    uv run python scripts/verify_promote.py

Exercises realistic task graph scenarios and prints the resulting
workflow structure for manual inspection.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import TaskGraph, TaskGraphNode, TaskGraphEdge
from engine.task_graph import TaskGraphStore
from engine.workflow_store import WorkflowStore


def _promote(graph: TaskGraph, tmp_dir: str) -> dict:
    """Run the same promote logic as relay_client._promote_graph / dashboard endpoint."""
    raw_name = graph.intents[0] if graph.intents else graph.task_key.replace("AUTONOMOUS::", "")
    name = raw_name.strip()[:60]
    intent_patterns = list(graph.intents) if graph.intents else [raw_name]
    slug = graph.task_key.replace("AUTONOMOUS::", "").replace(" ", "_")
    workflow_id = f"WF_{slug.upper()[:40]}"

    store = WorkflowStore(os.path.join(tmp_dir, "workflows.json"))
    wf = store.promote(graph, workflow_id, name, intent_patterns)
    return {
        "workflow_id": wf.id,
        "name": wf.name,
        "version": wf.version,
        "intent_patterns": wf.intent_patterns,
        "node_count": len(wf.nodes),
        "start_key": wf.start_key,
        "from_graph": wf.from_graph,
        "nodes": [{"key": n.key, "label": n.label, "kind": n.kind} for n in wf.nodes],
        "edges": [{"from": e.from_key, "to": e.to_key} for e in wf.edges],
    }


# ── Scenario 1: Job application form ──────────────────────────────────────────

def scenario_job_application() -> TaskGraph:
    graph = TaskGraph(task_key="AUTONOMOUS::apply_to_job", routine_id="AUTONOMOUS::apply_to_job")
    graph.nodes = [
        TaskGraphNode(key="nav::::Open job listing", kind="navigate", label="Open job listing"),
        TaskGraphNode(key="click::::Click Apply button", kind="click", label="Click Apply button"),
        TaskGraphNode(key="fill::::Fill name and email", kind="fill", label="Fill name and email"),
        TaskGraphNode(key="fill::::Upload resume", kind="fill", label="Upload resume"),
        TaskGraphNode(key="fill::::Fill work experience", kind="fill", label="Fill work experience"),
        TaskGraphNode(key="click::::Submit application", kind="click", label="Submit application"),
        TaskGraphNode(key="verify::::Confirm submission", kind="verify", label="Confirm submission"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="nav::::Open job listing", to_key="click::::Click Apply button"),
        TaskGraphEdge(from_key="click::::Click Apply button", to_key="fill::::Fill name and email"),
        TaskGraphEdge(from_key="fill::::Fill name and email", to_key="fill::::Upload resume"),
        TaskGraphEdge(from_key="fill::::Upload resume", to_key="fill::::Fill work experience"),
        TaskGraphEdge(from_key="fill::::Fill work experience", to_key="click::::Submit application"),
        TaskGraphEdge(from_key="click::::Submit application", to_key="verify::::Confirm submission"),
    ]
    graph.intents = ["apply to this job posting", "submit my job application", "fill out the application form"]
    graph.variables = {"JOB_URL": "https://example.com/careers/swe-123", "APPLICANT_NAME": "John Doe"}
    graph.run_count = 1
    return graph


# ── Scenario 2: Research + summarize ──────────────────────────────────────────

def scenario_research_summarize() -> TaskGraph:
    graph = TaskGraph(task_key="AUTONOMOUS::research_topic", routine_id="AUTONOMOUS::research_topic")
    graph.nodes = [
        TaskGraphNode(key="nav::::Open search engine", kind="navigate", label="Open search engine"),
        TaskGraphNode(key="fill::::Enter search query", kind="fill", label="Enter search query"),
        TaskGraphNode(key="click::::Open first result", kind="click", label="Open first result"),
        TaskGraphNode(key="read::::Extract key points", kind="read", label="Extract key points"),
        TaskGraphNode(key="click::::Open second result", kind="click", label="Open second result"),
        TaskGraphNode(key="read::::Extract more points", kind="read", label="Extract more points"),
        TaskGraphNode(key="write::::Compose summary", kind="write", label="Compose summary"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="nav::::Open search engine", to_key="fill::::Enter search query"),
        TaskGraphEdge(from_key="fill::::Enter search query", to_key="click::::Open first result"),
        TaskGraphEdge(from_key="click::::Open first result", to_key="read::::Extract key points"),
        TaskGraphEdge(from_key="read::::Extract key points", to_key="click::::Open second result"),
        TaskGraphEdge(from_key="click::::Open second result", to_key="read::::Extract more points"),
        TaskGraphEdge(from_key="read::::Extract more points", to_key="write::::Compose summary"),
    ]
    graph.intents = ["research this topic for me", "look up and summarize"]
    graph.variables = {"TOPIC": "quantum computing advances 2026"}
    graph.run_count = 1
    return graph


# ── Scenario 3: Multi-step purchase flow ─────────────────────────────────────

def scenario_purchase_item() -> TaskGraph:
    graph = TaskGraph(task_key="AUTONOMOUS::buy item on amazon", routine_id="AUTONOMOUS::buy item on amazon")
    graph.nodes = [
        TaskGraphNode(key="nav::::Go to Amazon", kind="navigate", label="Go to Amazon"),
        TaskGraphNode(key="fill::::Search for product", kind="fill", label="Search for product"),
        TaskGraphNode(key="click::::Select best match", kind="click", label="Select best match"),
        TaskGraphNode(key="click::::Add to cart", kind="click", label="Add to cart"),
        TaskGraphNode(key="click::::Go to checkout", kind="click", label="Go to checkout"),
        TaskGraphNode(key="verify::::Review order total", kind="verify", label="Review order total"),
        TaskGraphNode(key="click::::Place order", kind="click", label="Place order"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="nav::::Go to Amazon", to_key="fill::::Search for product"),
        TaskGraphEdge(from_key="fill::::Search for product", to_key="click::::Select best match"),
        TaskGraphEdge(from_key="click::::Select best match", to_key="click::::Add to cart"),
        TaskGraphEdge(from_key="click::::Add to cart", to_key="click::::Go to checkout"),
        TaskGraphEdge(from_key="click::::Go to checkout", to_key="verify::::Review order total"),
        TaskGraphEdge(from_key="verify::::Review order total", to_key="click::::Place order"),
    ]
    graph.intents = ["buy this item on amazon", "order this product", "purchase item"]
    graph.variables = {"PRODUCT": "USB-C hub", "MAX_PRICE": "$50"}
    graph.run_count = 1
    return graph


# ── Scenario 4: Minimal graph (edge case — only 2 nodes) ─────────────────────

def scenario_minimal() -> TaskGraph:
    graph = TaskGraph(task_key="AUTONOMOUS::quick_check", routine_id="AUTONOMOUS::quick_check")
    graph.nodes = [
        TaskGraphNode(key="nav::::Open page", kind="navigate", label="Open page"),
        TaskGraphNode(key="read::::Check status", kind="read", label="Check status"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="nav::::Open page", to_key="read::::Check status"),
    ]
    graph.intents = ["check the deployment status"]
    graph.variables = {}
    graph.run_count = 1
    return graph


# ── Scenario 5: No intents (edge case — fallback to slug) ────────────────────

def scenario_no_intents() -> TaskGraph:
    graph = TaskGraph(task_key="AUTONOMOUS::update_linkedin_profile", routine_id="AUTONOMOUS::update_linkedin_profile")
    graph.nodes = [
        TaskGraphNode(key="nav::::Open LinkedIn", kind="navigate", label="Open LinkedIn"),
        TaskGraphNode(key="click::::Go to profile", kind="click", label="Go to profile"),
        TaskGraphNode(key="click::::Edit headline", kind="click", label="Edit headline"),
        TaskGraphNode(key="fill::::Type new headline", kind="fill", label="Type new headline"),
        TaskGraphNode(key="click::::Save", kind="click", label="Save"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="nav::::Open LinkedIn", to_key="click::::Go to profile"),
        TaskGraphEdge(from_key="click::::Go to profile", to_key="click::::Edit headline"),
        TaskGraphEdge(from_key="click::::Edit headline", to_key="fill::::Type new headline"),
        TaskGraphEdge(from_key="fill::::Type new headline", to_key="click::::Save"),
    ]
    graph.intents = []  # No intents stored — tests fallback
    graph.variables = {}
    graph.run_count = 1
    return graph


# ── Run all scenarios ─────────────────────────────────────────────────────────

def main():
    scenarios = [
        ("Job Application Form", scenario_job_application),
        ("Research & Summarize", scenario_research_summarize),
        ("Purchase Item (Amazon)", scenario_purchase_item),
        ("Minimal (2 nodes)", scenario_minimal),
        ("No Intents (fallback)", scenario_no_intents),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        print("=" * 72)
        print("AUTO-PROMOTE VERIFICATION — Promoted Workflow Output")
        print("=" * 72)

        for title, builder in scenarios:
            graph = builder()
            result = _promote(graph, tmp)

            print(f"\n{'─' * 72}")
            print(f"  Scenario: {title}")
            print(f"  Task Key: {graph.task_key}")
            print(f"{'─' * 72}")
            print(f"  Workflow ID:      {result['workflow_id']}")
            print(f"  Name:             {result['name']}")
            print(f"  Version:          {result['version']}")
            print(f"  Intent Patterns:  {result['intent_patterns']}")
            print(f"  Node Count:       {result['node_count']}")
            print(f"  Start Key:        {result['start_key']}")
            print(f"  From Graph:       {result['from_graph']}")
            print()
            print("  Nodes:")
            for n in result["nodes"]:
                print(f"    [{n['kind']:10s}] {n['label']}")
            print()
            print("  Edges:")
            for e in result["edges"]:
                src = e["from"].split("::::")[1] if "::::" in e["from"] else e["from"]
                dst = e["to"].split("::::")[1] if "::::" in e["to"] else e["to"]
                print(f"    {src} → {dst}")
            print()

        # Also test round-trip: save + load + promote
        print(f"\n{'═' * 72}")
        print("ROUND-TRIP TEST: save graph → load → promote")
        print(f"{'═' * 72}")
        graph_store = TaskGraphStore(os.path.join(tmp, "task_graphs.json"))
        graph = scenario_job_application()
        graph_store.save(graph, intent_text="apply to this job posting", variables={"JOB_URL": "https://acme.com/job"}, run_id="run-001")
        loaded = graph_store.load("AUTONOMOUS::apply_to_job", {})
        print(f"\n  Loaded graph: {loaded.task_key}")
        print(f"  Run count after save: {loaded.run_count}")
        print(f"  Intents: {loaded.intents}")
        print(f"  Node count: {len(loaded.nodes)}")

        result = _promote(loaded, tmp)
        print(f"\n  Promoted workflow:")
        print(f"    ID:   {result['workflow_id']}")
        print(f"    Name: {result['name']}")
        print(f"    Patterns: {result['intent_patterns']}")
        print(f"    Nodes: {result['node_count']}")
        print()
        print("  All scenarios completed successfully.")
        print("=" * 72)


if __name__ == "__main__":
    main()
