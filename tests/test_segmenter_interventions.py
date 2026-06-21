"""
Tests for intervention-aware milestone segmenter:
  - _render_trace includes intervention markers at correct step indices
  - _render_trace renders rich prior context (conditionals, taught status)
  - _snap_label fuzzy matching via token-set Jaccard
  - coalescer passes interventions + prior_nodes through to segment()
"""
from unittest.mock import patch

from shepherd_types import (
    InterventionEvent, RoutineStep, TaskGraphNode, Conditional,
    TaskGraph, RunTrace,
)
from engine.milestones import (
    _render_trace, _snap_label, _token_set, segment,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _step(action: str, description: str = "", text: str = "",
          target: str = "") -> RoutineStep:
    return RoutineStep(action=action, description=description,
                       text=text, target=target)


def _intervention(step_index: int, instruction: str = "",
                  scenario: str = "", trigger: str = "",
                  flag: str = "save_as_rule") -> InterventionEvent:
    return InterventionEvent(
        step_index=step_index, instruction=instruction,
        scenario=scenario, trigger=trigger, flag=flag,
    )


def _node(key: str, kind: str, label: str, conditionals=None,
          source: str = "observed") -> TaskGraphNode:
    return TaskGraphNode(
        key=key, kind=kind, label=label,
        conditionals=conditionals or [], source=source,
    )


# ── Tests: _render_trace with interventions ──────────────────────────────────

class TestRenderTraceInterventions:
    """Intervention markers appear inline at the step where they occurred."""

    def test_no_interventions_unchanged(self):
        steps = [_step("open_app", "Open browser", target="Chrome")]
        result = _render_trace(steps, ["Open browser"])
        assert "<<< USER INTERVENED" not in result
        assert "PRIOR MILESTONES: Open browser" in result

    def test_single_intervention_at_step(self):
        steps = [
            _step("open_app", "Open browser", target="Chrome"),
            _step("type", "Fill name", text="Alice"),
            _step("hotkey", "Open new tab"),
        ]
        iv = _intervention(
            step_index=2,
            instruction="research their GitHub projects first",
            scenario="projects field was empty",
        )
        result = _render_trace(steps, [], interventions=[iv])
        lines = result.split("\n")
        # The intervention marker should appear on line for step 2
        step2_line = [l for l in lines if l.startswith("2 ")][0]
        assert "<<< USER INTERVENED:" in step2_line
        assert "research their GitHub projects first" in step2_line
        assert "reason: projects field was empty" in step2_line

    def test_multiple_interventions_at_different_steps(self):
        steps = [
            _step("open_app", "Open browser"),
            _step("type", "Fill name"),
            _step("type", "Fill email"),
            _step("hotkey", "Submit"),
        ]
        ivs = [
            _intervention(1, "use full legal name", scenario="name field"),
            _intervention(3, "also CC the manager", scenario="submission step"),
        ]
        result = _render_trace(steps, [], interventions=ivs)
        lines = result.split("\n")
        step1_line = [l for l in lines if l.startswith("1 ")][0]
        step3_line = [l for l in lines if l.startswith("3 ")][0]
        assert "<<< USER INTERVENED:" in step1_line
        assert "use full legal name" in step1_line
        assert "<<< USER INTERVENED:" in step3_line
        assert "also CC the manager" in step3_line

    def test_intervention_with_trigger_fallback(self):
        """When scenario is empty, trigger is used as the reason."""
        steps = [_step("type", "Fill field")]
        iv = _intervention(0, "approve this", trigger="credential")
        result = _render_trace(steps, [], interventions=[iv])
        assert "reason: credential" in result

    def test_intervention_instruction_only(self):
        """When neither scenario nor trigger is set, instruction alone."""
        steps = [_step("type", "Fill field")]
        iv = _intervention(0, "skip this step")
        result = _render_trace(steps, [], interventions=[iv])
        assert "<<< USER INTERVENED: skip this step >>>" in result


# ── Tests: _render_trace with rich prior nodes ───────────────────────────────

class TestRenderTracePriorNodes:
    """Prior milestones include conditionals and taught status."""

    def test_prior_nodes_with_conditionals(self):
        nodes = [
            _node("open::demo", "open", "Open application form"),
            _node("fill::details", "fill", "Fill applicant details",
                  conditionals=[
                      Conditional(when="projects field is empty",
                                  do="research their GitHub"),
                  ]),
            _node("submit::app", "submit", "Submit application"),
        ]
        result = _render_trace(
            [_step("open_app", "Open browser")],
            prior_labels=["Open application form", "Fill applicant details",
                          "Submit application"],
            prior_nodes=nodes,
        )
        assert "PRIOR MILESTONES:" in result
        assert "Fill applicant details" in result
        assert "[taught: if projects field is empty" in result
        assert "research their GitHub" in result

    def test_prior_nodes_taught_source(self):
        nodes = [
            _node("research::gh", "research", "Research GitHub projects",
                  source="taught"),
        ]
        result = _render_trace(
            [_step("open_app", "Open browser")],
            prior_labels=["Research GitHub projects"],
            prior_nodes=nodes,
        )
        assert "(user-taught)" in result

    def test_fallback_to_prior_labels_when_no_nodes(self):
        result = _render_trace(
            [_step("open_app", "Open browser")],
            prior_labels=["Open form", "Fill details"],
            prior_nodes=None,
        )
        assert "PRIOR MILESTONES: Open form; Fill details" in result

    def test_first_run_no_prior(self):
        result = _render_trace(
            [_step("open_app", "Open browser")],
            prior_labels=[],
            prior_nodes=None,
        )
        assert "none" in result.lower()


# ── Tests: _snap_label fuzzy matching ────────────────────────────────────────

class TestSnapLabelFuzzy:
    """Fuzzy matching prevents key divergence from minor wording variations."""

    def test_exact_match(self):
        assert _snap_label("fill", "Fill applicant details",
                           ["Fill applicant details"]) == "Fill applicant details"

    def test_case_insensitive_exact(self):
        assert _snap_label("fill", "fill applicant details",
                           ["Fill applicant details"]) == "Fill applicant details"

    def test_fuzzy_match_similar(self):
        # "Fill applicant info" vs "Fill applicant details" — 2/3 tokens match
        result = _snap_label("fill", "Fill applicant info",
                             ["Fill applicant details"])
        assert result == "Fill applicant details"

    def test_fuzzy_match_reworded(self):
        # "Complete the application" vs "Complete application" — high overlap
        result = _snap_label("fill", "Complete the application",
                             ["Complete application"])
        assert result == "Complete application"

    def test_no_match_different(self):
        # Completely different labels should not match
        result = _snap_label("fill", "Send email message",
                             ["Fill applicant details"])
        assert result == "Send email message"

    def test_picks_best_match(self):
        priors = ["Open form", "Fill applicant details", "Submit application"]
        result = _snap_label("fill", "Fill applicant info", priors)
        assert result == "Fill applicant details"

    def test_empty_prior_labels(self):
        result = _snap_label("fill", "Fill stuff", [])
        assert result == "Fill stuff"


class TestTokenSet:
    def test_basic(self):
        assert _token_set("Fill applicant details") == {"fill", "applicant", "details"}

    def test_strips_punctuation(self):
        # Non-alpha tokens are excluded
        assert "123" not in _token_set("Step 123 done")


# ── Tests: segment() passes interventions through ────────────────────────────

class TestSegmentPassthrough:
    """segment() passes interventions and prior_nodes to the LLM path."""

    @patch("engine.milestones.llm_available", return_value=False)
    def test_heuristic_fallback_still_works(self, _mock_llm):
        """When no LLM, segment falls back to heuristic (no crash)."""
        steps = [
            _step("open_app", "Open browser", target="Chrome"),
            _step("type", "Type name", text="Alice"),
        ]
        iv = _intervention(1, "use full legal name")
        result = segment(steps, {}, interventions=[iv])
        assert len(result) > 0

    @patch("engine.milestones._llm_segment")
    @patch("engine.milestones.llm_available", return_value=True)
    @patch("engine.milestones._semantic_cache", return_value=None)
    def test_llm_receives_interventions(self, _cache, _avail, mock_llm_seg):
        """The LLM segmenter receives interventions and prior_nodes."""
        mock_llm_seg.return_value = [
            {"kind": "open", "label": "Open form", "value": None,
             "detail": "", "detour": False, "fine": 1, "fine_start": 0, "fine_end": 0},
        ]
        steps = [_step("open_app", "Open browser")]
        iv = _intervention(0, "do it differently")
        node = _node("open::form", "open", "Open form")

        segment(steps, {}, interventions=[iv], prior_nodes=[node])

        mock_llm_seg.assert_called_once()
        call_kwargs = mock_llm_seg.call_args
        # interventions and prior_nodes should be passed through
        assert call_kwargs.kwargs.get("interventions") == [iv]
        assert call_kwargs.kwargs.get("prior_nodes") == [node]


# ── Tests: coalescer passes new args to segment() ───────────────────────────

class TestCoalescerPassthrough:
    """The coalescer passes interventions + prior_nodes to segment()."""

    @patch("engine.coalescer._maybe_auto_promote")
    @patch("engine.coalescer.workflow_edit")
    @patch("engine.coalescer.trace_journal")
    @patch("engine.coalescer.segment")
    def test_coalesce_passes_interventions_and_nodes(
        self, mock_segment, mock_journal, mock_wf_edit, mock_promote,
    ):
        from engine.coalescer import coalesce_now, _store

        iv = _intervention(1, "research GitHub", scenario="projects empty")
        trace = RunTrace(
            run_id="test-run",
            routine_id="test-routine",
            executed=[
                _step("open_app", "Open browser"),
                _step("type", "Fill name"),
            ],
            interventions=[iv],
        )

        # Set up graph store to return a graph with nodes
        node = _node("open::form", "open", "Open form",
                      conditionals=[Conditional(when="x", do="y")])
        graph = TaskGraph(task_key="test-routine", routine_id="test-routine",
                         nodes=[node], edges=[])
        with patch.object(_store, "load", return_value=graph), \
             patch.object(_store, "is_known", return_value=True), \
             patch.object(_store, "record_milestone",
                          return_value=("matched", node)), \
             patch.object(_store, "record_edge"), \
             patch.object(_store, "save"):

            mock_segment.return_value = [
                {"kind": "open", "label": "Open form", "value": None,
                 "detail": "", "detour": False, "fine": 1,
                 "fine_start": 0, "fine_end": 0},
                {"kind": "fill", "label": "Fill name", "value": None,
                 "detail": "", "detour": False, "fine": 1,
                 "fine_start": 1, "fine_end": 1},
            ]
            mock_wf_edit.build_patch.return_value = []
            mock_wf_edit.apply_patch.return_value = []

            coalesce_now(trace)

            mock_segment.assert_called_once()
            call_args = mock_segment.call_args
            assert call_args.kwargs.get("interventions") == [iv]
            assert call_args.kwargs.get("prior_nodes") is not None
            assert len(call_args.kwargs["prior_nodes"]) == 1
            assert call_args.kwargs["prior_nodes"][0].key == "open::form"
