// Auto-compiled by Shepherd from a taught workflow — deterministic replay,
// zero LLM tokens per run. Run with:  simulang run data/simulang/WF_BIG.ts
import { AccessibilityTree, App, AriaRole, FocusPolicy, Visibility } from '@simular-ai/simulang-js'

const app = App.frontmost().open(FocusPolicy.Steal, Visibility.Show, true)
const tree = AccessibilityTree.fromPid(app.pid)
const root = tree.snapshot(true)
const nodes = flattenDFS(root)

const byLabel = (re, role) => nodes.find(
  (n) => n.refId != null && (role == null || n.role === role) && re.test(labelOf(n)))

// Step0
{ const el = byLabel(/Step0/i); if (el) tree.activate(el.refId) }

// Step1
{ const el = byLabel(/Step1/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "") }

// Step2
{ const el = byLabel(/Step2/i); if (el) tree.activate(el.refId) }

// (skipped non-actionable milestone: Step3)

// End of compiled workflow.
