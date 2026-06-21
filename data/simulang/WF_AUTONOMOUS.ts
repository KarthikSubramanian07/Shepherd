// Auto-compiled by Shepherd from a taught workflow — deterministic replay,
// zero LLM tokens per run. Run with:  npx tsx data/simulang/WF_AUTONOMOUS.ts
import { AccessibilityTree, App, AriaRole, FocusPolicy, Visibility } from '@simular-ai/simulang-js'

const app = App.frontmost().open(FocusPolicy.Steal, Visibility.Show, true)
const tree = AccessibilityTree.fromPid(app.pid)
const root = tree.snapshot(true)
const nodes = flattenDFS(root)

const byLabel = (re, role) => nodes.find(
  (n) => n.refId != null && (role == null || n.role === role) && re.test(labelOf(n)))

// Open Google Chrome
{ const el = byLabel(/Open Google Chrome/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "Google Chrome") }

// (skipped non-actionable milestone: Scan results)

// Navigate to rohanbayya99@gmail.com
{ const el = byLabel(/Navigate to rohanbayya99@gmail\.com/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "rohanbayya99@gmail.com") }

// Enter details
{ const el = byLabel(/Enter details/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "") }

// Submit
{ const el = byLabel(/Submit/i); if (el) tree.activate(el.refId) }

// Search: crazy frog
{ const el = byLabel(/Search: crazy frog/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "crazy frog") }

// Search: despacito
{ const el = byLabel(/Search: despacito/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "despacito") }

// Search: Despacito
{ const el = byLabel(/Search: Despacito/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "Despacito") }

// Search: Crazy Frog
{ const el = byLabel(/Search: Crazy Frog/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "Crazy Frog") }

// Open Notes
{ const el = byLabel(/Open Notes/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "Notes") }

// Open Photos
{ const el = byLabel(/Open Photos/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "Photos") }

// Interact
{ const el = byLabel(/Interact/i); if (el) tree.activate(el.refId) }

// Open Photo Booth
{ const el = byLabel(/Open Photo Booth/i, AriaRole.Textbox); if (el) tree.setValue(el.refId, "Photo Booth") }

// End of compiled workflow.
