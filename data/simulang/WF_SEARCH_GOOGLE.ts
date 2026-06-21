// Auto-compiled by Shepherd from a taught workflow — deterministic replay,
// zero LLM tokens per run. Run with:  npx tsx data/simulang/WF_SEARCH_GOOGLE.ts
import { AccessibilityTree, App, AriaRole, FocusPolicy, Visibility } from '@simular-ai/simulang-js'

const app = App.frontmost().open(FocusPolicy.Steal, Visibility.Show, true)
const tree = AccessibilityTree.fromPid(app.pid)
const root = tree.snapshot(true)
const nodes = flattenDFS(root)

const byLabel = (re, role) => nodes.find(
  (n) => n.refId != null && (role == null || n.role === role) && re.test(labelOf(n)))

// Navigate to Google
{ const el = byLabel(/Navigate to Google/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "google.com") }

// Search query
{ const el = byLabel(/Search query/i); if (el) tree.activate(el.refId) }

// End of compiled workflow.
