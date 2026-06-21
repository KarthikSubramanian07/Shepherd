"""Fetch trace spans from local Phoenix for the Control Hub viewer."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from config import ARIZE_PROJECT_NAME, PHOENIX_COLLECTOR_ENDPOINT

_TRACE_SPANS_QUERY = """
query TraceSpans($project: String!, $traceId: ID!) {
  projects(filter: {value: $project, col: name}) {
    edges {
      node {
        trace(traceId: $traceId) {
          traceId
          numSpans
          latencyMs
          rootSpan { name spanKind }
          spans(first: 200) {
            edges {
              node {
                id
                spanId
                name
                spanKind
                parentId
                input { value mimeType }
                output { value mimeType }
                latencyMs
              }
            }
          }
        }
      }
    }
  }
}
"""

_LATEST_TRACE_QUERY = """
query LatestTrace($project: String!) {
  projects(filter: {value: $project, col: name}) {
    edges {
      node {
        spans(
          filterCondition: "name == 'routine.execute'",
          first: 1,
          sort: { col: startTime, dir: desc }
        ) {
          edges {
            node {
              trace { traceId latencyMs }
            }
          }
        }
      }
    }
  }
}
"""


def _graphql(query: str, variables: dict) -> dict | None:
    base = PHOENIX_COLLECTOR_ENDPOINT.rstrip("/").replace("/v1/traces", "")
    url = f"{base}/graphql"
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if payload.get("errors"):
        return None
    return payload.get("data")


def _project_node(data: dict) -> dict | None:
    edges = (data.get("projects") or {}).get("edges") or []
    for edge in edges:
        node = edge.get("node")
        if node and node.get("name") == ARIZE_PROJECT_NAME:
            return node
    return edges[0]["node"] if edges else None


def fetch_trace(trace_id: str) -> dict | None:
    """Return {trace_id, spans: [{id, name, span_kind, parent_id, input, output, latency_ms}]}"""
    data = _graphql(_TRACE_SPANS_QUERY, {"project": ARIZE_PROJECT_NAME, "traceId": trace_id})
    if not data:
        return None
    node = _project_node(data)
    if not node:
        return None
    trace = node.get("trace")
    if not trace:
        return None

    spans = []
    for edge in (trace.get("spans") or {}).get("edges") or []:
        n = edge.get("node") or {}
        inp = n.get("input") or {}
        out = n.get("output") or {}
        spans.append({
            "id":         n.get("id"),
            "span_id":    n.get("spanId"),
            "name":       n.get("name"),
            "span_kind":  n.get("spanKind"),
            "parent_id":  n.get("parentId"),
            "input":      inp.get("value") or "",
            "output":     out.get("value") or "",
            "latency_ms": int(n.get("latencyMs") or 0),
        })

    return {
        "trace_id":  trace.get("traceId"),
        "num_spans": trace.get("numSpans"),
        "latency_ms": int(trace.get("latencyMs") or 0),
        "root_span": (trace.get("rootSpan") or {}).get("name"),
        "spans":     spans,
        "phoenix_url": phoenix_trace_url(trace.get("traceId")),
    }


def fetch_latest_trace() -> dict | None:
    data = _graphql(_LATEST_TRACE_QUERY, {"project": ARIZE_PROJECT_NAME})
    if not data:
        return None
    node = _project_node(data)
    if not node:
        return None
    edges = (node.get("spans") or {}).get("edges") or []
    if not edges:
        return None
    trace = (edges[0].get("node") or {}).get("trace") or {}
    trace_id = trace.get("traceId")
    if not trace_id:
        return None
    return fetch_trace(trace_id)


def phoenix_trace_url(trace_id: str | None) -> str:
    base = PHOENIX_COLLECTOR_ENDPOINT.rstrip("/").replace("/v1/traces", "")
    tid = trace_id or ""
    return f"{base}/projects/{ARIZE_PROJECT_NAME}/traces/{tid}"


def annotate_span(
    span_id: str,
    *,
    name: str,
    label: str,
    explanation: str,
    annotator_kind: str = "CODE",
) -> None:
    """
    Write LLM/tool text into Phoenix's Annotations panel (the panel users actually see).
    span_id: OpenTelemetry span id (hex, no 0x prefix).
    """
    if not span_id or not explanation:
        return
    base = PHOENIX_COLLECTOR_ENDPOINT.rstrip("/").replace("/v1/traces", "")
    url = f"{base}/v1/span_annotations"
    body = json.dumps({
        "data": [{
            "span_id": span_id,
            "name": name,
            "annotator_kind": annotator_kind,
            "result": {
                "label": label,
                "explanation": explanation[:8000],
            },
        }],
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass
