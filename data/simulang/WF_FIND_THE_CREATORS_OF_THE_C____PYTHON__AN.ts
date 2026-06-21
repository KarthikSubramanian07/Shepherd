// Auto-compiled by Shepherd from a taught workflow — deterministic replay,
// zero LLM tokens per run. Run with:  simulang run data/simulang/WF_FIND_THE_CREATORS_OF_THE_C____PYTHON__AN.ts
import { AccessibilityTree, App, AriaRole, FocusPolicy, Visibility } from '@simular-ai/simulang-js'

const app = App.frontmost().open(FocusPolicy.Steal, Visibility.Show, true)
const tree = AccessibilityTree.fromPid(app.pid)
const root = tree.snapshot(true)
const nodes = flattenDFS(root)

const byLabel = (re, role) => nodes.find(
  (n) => n.refId != null && (role == null || n.role === role) && re.test(labelOf(n)))

// Open browser
{ const el = byLabel(/Open browser/i); if (el) tree.activate(el.refId) }

// Go to Wikipedia
{ const el = byLabel(/Go to Wikipedia/i); if (el) tree.activate(el.refId) }

// Search language creator
{ const el = byLabel(/Search language creator/i); if (el) tree.activate(el.refId) }

// (skipped non-actionable milestone: Read results)

// End of compiled workflow.
