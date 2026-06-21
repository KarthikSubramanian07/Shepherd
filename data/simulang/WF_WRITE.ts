// Auto-compiled by Shepherd from a taught workflow — deterministic replay,
// zero LLM tokens per run. Run with:  npx tsx data/simulang/WF_WRITE.ts
import { AccessibilityTree, App, AriaRole, FocusPolicy, Visibility } from '@simular-ai/simulang-js'

const app = App.frontmost().open(FocusPolicy.Steal, Visibility.Show, true)
const tree = AccessibilityTree.fromPid(app.pid)
const root = tree.snapshot(true)
const nodes = flattenDFS(root)

const byLabel = (re, role) => nodes.find(
  (n) => n.refId != null && (role == null || n.role === role) && re.test(labelOf(n)))

// Open Notes
{ const el = byLabel(/Open Notes/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "Notes") }

// Enter details
{ const el = byLabel(/Enter details/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "") }

// Submit
{ const el = byLabel(/Submit/i); if (el) tree.activate(el.refId) }

// End of compiled workflow.
