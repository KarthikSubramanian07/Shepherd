// Auto-compiled by Shepherd from a taught workflow — deterministic replay,
// zero LLM tokens per run. Run with:  npx tsx data/simulang/WF_OPEN_SETTINGS_AND_TOGGLE_A_SETTING.ts
import { AccessibilityTree, App, AriaRole, FocusPolicy, Visibility } from '@simular-ai/simulang-js'

const app = App.frontmost().open(FocusPolicy.Steal, Visibility.Show, true)
const tree = AccessibilityTree.fromPid(app.pid)
const root = tree.snapshot(true)
const nodes = flattenDFS(root)

const byLabel = (re, role) => nodes.find(
  (n) => n.refId != null && (role == null || n.role === role) && re.test(labelOf(n)))

// Open settings page
{ const el = byLabel(/Open settings page/i); if (el) tree.activate(el.refId) }

// Toggle dark mode
{ const el = byLabel(/Toggle dark mode/i); if (el) tree.activate(el.refId) }

// End of compiled workflow.
