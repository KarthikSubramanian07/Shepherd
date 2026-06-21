"""Helpers for Agent S span attributes — OpenInference fields Phoenix renders as I/O."""
from __future__ import annotations

import json
import re
from typing import Optional

from opentelemetry.trace import format_span_id

try:
    from openinference.instrumentation._attributes import (
        get_llm_attributes,
        get_tool_attributes,
    )
    from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes as SA
except ImportError:
    get_llm_attributes = None  # type: ignore[assignment,misc]
    get_tool_attributes = None  # type: ignore[assignment,misc]

    class OpenInferenceSpanKindValues:  # type: ignore[no-redef]
        LLM = "LLM"
        TOOL = "TOOL"
        CHAIN = "CHAIN"

    class SA:  # type: ignore[no-redef]
        OPENINFERENCE_SPAN_KIND = "openinference.span.kind"
        INPUT_VALUE = "input.value"
        INPUT_MIME_TYPE = "input.mime_type"
        OUTPUT_VALUE = "output.value"
        OUTPUT_MIME_TYPE = "output.mime_type"
        LLM_INPUT_MESSAGES = "llm.input_messages"
        LLM_OUTPUT_MESSAGES = "llm.output_messages"
        LLM_MODEL_NAME = "llm.model_name"
        LLM_PROVIDER = "llm.provider"
        TOOL_NAME = "tool.name"
        TOOL_PARAMETERS = "tool.parameters"
        TOOL_DESCRIPTION = "tool.description"

_ATTR_LIMIT = 8000


def trunc(s: Optional[str], limit: int = _ATTR_LIMIT) -> str:
    if not s:
        return ""
    s = str(s)
    return s


def _set_attrs(span, attrs: dict) -> None:
    for k, v in attrs.items():
        if v is not None and v != "":
            span.set_attribute(k, v)


def _otel_span_id(span) -> str:
    try:
        sc = span.get_span_context()
        if sc.is_valid:
            return format_span_id(sc.span_id)
    except Exception:
        pass
    return ""


def _annotate_visible(span, *, name: str, label: str, explanation: str) -> None:
    """Mirror I/O into Phoenix Annotations panel (always visible in trace detail UI)."""
    sid = _otel_span_id(span)
    if not sid:
        return
    try:
        from telemetry.phoenix_client import annotate_span
        annotate_span(sid, name=name, label=label, explanation=explanation)
    except Exception:
        pass


def _set_io(span, input_text: str, output_text: str) -> None:
    """
    Phoenix only populates Input/Output columns when OpenInferenceSpan.set_input/set_output
    are called on spans started with openinference_span_kind=...
    """
    if not hasattr(span, "set_input") or not hasattr(span, "set_output"):
        return
    try:
        if input_text:
            span.set_input(trunc(input_text))
        if output_text:
            span.set_output(trunc(output_text))
    except Exception:
        pass


def summarize_agent_code(code: Optional[str]) -> tuple[str, str]:
    """
    Parse pyautogui / AppleScript code for apps touched and action types.
    Returns (apps_csv, tools_csv).
    """
    if not code:
        return "", ""

    apps: list[str] = []
    for pat in (
        r'open\s*\(\s*["\']([^"\']+)["\']',
        r'["\']-a["\'],\s*["\']([^"\']+)["\']',
        r'tell application "([^"]+)"',
        r'Popen\(\[["\']open["\'],\s*["\']-a["\'],\s*["\']([^"\']+)["\']',
    ):
        for m in re.finditer(pat, code, re.IGNORECASE):
            val = m.group(1).strip()
            if val and val not in apps:
                apps.append(val)

    tools: list[str] = []
    checks = (
        ("click", r"\.click\(|\.doubleClick\("),
        ("type", r"typewrite|hotkey\([\"']cmd[\"'],\s*[\"']v"),
        ("hotkey", r"\.hotkey\("),
        ("scroll", r"\.scroll\("),
        ("move", r"\.moveTo\(|\.moveRel\("),
        ("press", r"\.press\("),
        ("wait", r"\.sleep\(|time\.sleep\("),
        ("open_app", r'\["open"|open\s*\(\s*["\']'),
    )
    for name, pat in checks:
        if re.search(pat, code, re.IGNORECASE):
            tools.append(name)

    return ", ".join(apps), ", ".join(tools)


def apply_llm_plan_span(
    span,
    *,
    instruction: str,
    response: str,
    outcome: str = "",
    model: str = "",
    provider: str = "",
    code: Optional[str] = None,
    apps: Optional[str] = None,
    tools: Optional[str] = None,
) -> None:
    """agent_s.plan → LLM span with chat I/O visible in Phoenix."""
    out = trunc(response) or trunc(code) or outcome or "(no llm text captured)"
    _set_io(span, instruction, out)

    if get_llm_attributes is not None:
        _set_attrs(span, get_llm_attributes(
            provider=provider or None,
            model_name=model or None,
            input_messages=[{"role": "user", "content": trunc(instruction)}],
            output_messages=[{"role": "assistant", "content": out}],
        ))
    else:
        span.set_attribute(SA.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.LLM.value)
        span.set_attribute(SA.INPUT_VALUE, trunc(instruction))
        span.set_attribute(SA.OUTPUT_VALUE, out)

    if outcome:
        span.set_attribute("agent.outcome", outcome)
    if code:
        span.set_attribute("agent.code", trunc(code))
    if apps:
        span.set_attribute("agent.apps", apps)
    if tools:
        span.set_attribute("agent.tools", tools)

    _annotate_visible(
        span,
        name="llm_output",
        label=outcome or "llm",
        explanation=f"INPUT:\n{trunc(instruction)}\n\nOUTPUT:\n{out}",
    )


def apply_tool_act_span(
    span,
    *,
    goal: str,
    code: str,
    apps: Optional[str] = None,
    tools: Optional[str] = None,
    status: str = "ok",
) -> None:
    """agent_s.act → TOOL span with executed action visible in Phoenix."""
    tool_name = (tools.split(",")[0].strip() if tools else "pyautogui")
    summary = f"Executed {tool_name}"
    if apps:
        summary += f" · apps: {apps}"
    if status != "ok":
        summary += f" · status: {status}"

    params: dict = {"code": trunc(code, 2000), "goal": trunc(goal, 500)}
    if apps:
        params["apps"] = apps
    if tools:
        params["tools"] = tools

    _set_io(span, code, summary)

    if get_tool_attributes is not None:
        _set_attrs(span, get_tool_attributes(
            name=tool_name,
            description="Agent S desktop action (pyautogui)",
            parameters=json.dumps(params),
        ))
    else:
        span.set_attribute(SA.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.TOOL.value)
        span.set_attribute(SA.INPUT_VALUE, trunc(code))
        span.set_attribute(SA.OUTPUT_VALUE, summary)

    if apps:
        span.set_attribute("agent.apps", apps)
    if tools:
        span.set_attribute("agent.tools", tools)
    span.set_attribute("agent.action", tool_name)

    _annotate_visible(
        span,
        name="tool_call",
        label=tool_name,
        explanation=f"GOAL:\n{trunc(goal, 500)}\n\nCODE:\n{trunc(code, 4000)}\n\nRESULT:\n{summary}",
    )


def apply_chain_span(span, *, input_text: str, output_text: str) -> None:
    """Root / step container spans — goal in, result summary out."""
    _set_io(span, input_text, output_text)
    span.set_attribute(SA.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.CHAIN.value)
    if input_text:
        span.set_attribute(SA.INPUT_VALUE, trunc(input_text))
    if output_text:
        span.set_attribute(SA.OUTPUT_VALUE, trunc(output_text))
