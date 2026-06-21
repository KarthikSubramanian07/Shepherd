/**
 * Graph analysis for the crystallized milestone DAG.
 *
 * The backend accumulates a directed multigraph across runs: nodes are
 * milestones, edges are observed transitions weighted by how many runs took
 * them (`times_seen`). This module turns that raw graph into something a human
 * can reason about, using real graph-drawing and probabilistic-model math:
 *
 *   1. Layered (Sugiyama) layout
 *        a. longest-path layer assignment over the DAG
 *        b. iterative barycenter ordering to MINIMISE EDGE CROSSINGS
 *        c. order-based coordinate assignment, centred per layer
 *
 *   2. Markov view of the workflow
 *        each node's out-edges form a probability distribution
 *        p(u→v) = times_seen(u→v) / Σ_w times_seen(u→w)
 *        so the DAG reads as a Markov chain over milestones.
 *
 *   3. Modal execution path (Viterbi / max-product DP over the DAG)
 *        the single most-probable complete trace from a root to a sink —
 *        i.e. "what this workflow usually does".
 *
 *   4. Decision entropy
 *        H(u) = −Σ p(u→v) log₂ p(u→v) at each branch point, and the mean
 *        over all branch points — a bits measure of how UNPREDICTABLE the
 *        workflow's choices are. 0 bits = fully deterministic.
 *
 * All functions are pure and dependency-free so they can be unit-tested and
 * reused outside React.
 */
import type { TaskGraph } from "./types";

export const COL_GAP = 380;
export const ROW_GAP = 160;

export interface LayoutPos {
  x: number;
  y: number;
  layer: number;
  order: number;
}

export interface EdgeStat {
  from: string;
  to: string;
  timesSeen: number;
  /** Transition probability out of `from` (Markov). */
  prob: number;
  /** Lies on the modal (most-probable) execution path. */
  onModalPath: boolean;
  /** A back/flat edge the layered layout drew against the flow (rare). */
  isBackEdge: boolean;
}

export interface NodeStat {
  key: string;
  inDegree: number;
  outDegree: number;
  isRoot: boolean;
  isSink: boolean;
  /** Out-degree ≥ 2 — the workflow makes a choice here. */
  isBranch: boolean;
  /** In-degree ≥ 2 — multiple paths converge here. */
  isMerge: boolean;
  onModalPath: boolean;
  /** Shannon entropy of this node's out-distribution, in bits. */
  entropy: number;
  /** Max-product probability of reaching this node from a root. */
  reachProb: number;
}

export interface GraphMetrics {
  nodeCount: number;
  edgeCount: number;
  /** Runs the graph was accumulated from. */
  runCount: number;
  /** Longest path length, in milestones. */
  depth: number;
  branchPoints: number;
  mergePoints: number;
  /** Edges / nodes — average out-degree. */
  meanBranching: number;
  /** Mean decision entropy over branch points, in bits. */
  avgEntropy: number;
  /** Probability mass of the modal path = how dominant the "usual" run is. */
  modalCoverage: number;
  /** Ordered node keys of the modal execution path. */
  modalPath: string[];
  /** McCabe cyclomatic complexity (E - N + 2): the number of linearly-independent
   *  paths through the workflow = how many genuinely distinct ways it can run. */
  cyclomatic: number;
}

export interface GraphAnalysis {
  pos: Map<string, LayoutPos>;
  nodeStats: Map<string, NodeStat>;
  edgeStats: EdgeStat[];
  metrics: GraphMetrics;
}

function median(xs: number[]): number {
  if (xs.length === 0) return -1;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * Layered layout. Returns per-node {layer, order} plus pixel x/y.
 * Crossings are reduced with the classic median-barycenter sweep heuristic.
 */
function layeredLayout(
  graph: TaskGraph,
  layer: Map<string, number>,
): Map<string, LayoutPos> {
  const succ = new Map<string, string[]>();
  const pred = new Map<string, string[]>();
  graph.nodes.forEach((n) => {
    succ.set(n.key, []);
    pred.set(n.key, []);
  });
  for (const e of graph.edges) {
    if (succ.has(e.from) && pred.has(e.to) && e.from !== e.to) {
      succ.get(e.from)!.push(e.to);
      pred.get(e.to)!.push(e.from);
    }
  }

  // Bucket nodes into layers, initial order = input order.
  const maxLayer = Math.max(0, ...graph.nodes.map((n) => layer.get(n.key) ?? 0));
  const layers: string[][] = Array.from({ length: maxLayer + 1 }, () => []);
  graph.nodes.forEach((n) => layers[layer.get(n.key) ?? 0].push(n.key));

  const orderOf = (arr: string[]) => {
    const m = new Map<string, number>();
    arr.forEach((k, i) => m.set(k, i));
    return m;
  };

  // Median-barycenter sweeps: down then up, a few passes. Each node is moved to
  // the median position of its neighbours in the adjacent (already-ordered)
  // layer; stable-sort by that median reorders the layer to cut crossings.
  for (let pass = 0; pass < 4; pass++) {
    // downward — order layer l by predecessors in l-1
    for (let l = 1; l <= maxLayer; l++) {
      const ord = orderOf(layers[l - 1]);
      const bary = new Map<string, number>();
      layers[l].forEach((k, i) => {
        const ps = (pred.get(k) ?? [])
          .map((p) => ord.get(p))
          .filter((v): v is number => v !== undefined);
        bary.set(k, ps.length ? median(ps) : i);
      });
      layers[l] = [...layers[l]].sort((a, b) => bary.get(a)! - bary.get(b)!);
    }
    // upward — order layer l by successors in l+1
    for (let l = maxLayer - 1; l >= 0; l--) {
      const ord = orderOf(layers[l + 1]);
      const bary = new Map<string, number>();
      layers[l].forEach((k, i) => {
        const ss = (succ.get(k) ?? [])
          .map((s) => ord.get(s))
          .filter((v): v is number => v !== undefined);
        bary.set(k, ss.length ? median(ss) : i);
      });
      layers[l] = [...layers[l]].sort((a, b) => bary.get(a)! - bary.get(b)!);
    }
  }

  const pos = new Map<string, LayoutPos>();
  layers.forEach((group, l) => {
    group.forEach((k, i) => {
      pos.set(k, {
        x: l * COL_GAP,
        y: i * ROW_GAP - ((group.length - 1) * ROW_GAP) / 2,
        layer: l,
        order: i,
      });
    });
  });
  return pos;
}

export function analyzeGraph(graph: TaskGraph): GraphAnalysis {
  const keys = graph.nodes.map((n) => n.key);
  const inEdges = new Map<string, { from: string; ts: number }[]>();
  const outEdges = new Map<string, { to: string; ts: number }[]>();
  keys.forEach((k) => {
    inEdges.set(k, []);
    outEdges.set(k, []);
  });
  for (const e of graph.edges) {
    if (outEdges.has(e.from) && inEdges.has(e.to) && e.from !== e.to) {
      outEdges.get(e.from)!.push({ to: e.to, ts: e.times_seen });
      inEdges.get(e.to)!.push({ from: e.from, ts: e.times_seen });
    }
  }

  // ── Layer assignment (longest path) via Kahn topological order ──────────────
  const indeg = new Map<string, number>();
  keys.forEach((k) => indeg.set(k, inEdges.get(k)!.length));
  const layer = new Map<string, number>();
  keys.forEach((k) => layer.set(k, 0));
  const work = new Map(indeg);
  const q = keys.filter((k) => work.get(k) === 0);
  const topo: string[] = [];
  const seen = new Set(q);
  while (q.length) {
    const k = q.shift()!;
    topo.push(k);
    for (const { to } of outEdges.get(k) ?? []) {
      layer.set(to, Math.max(layer.get(to) ?? 0, (layer.get(k) ?? 0) + 1));
      work.set(to, (work.get(to) ?? 0) - 1);
      if ((work.get(to) ?? 0) <= 0 && !seen.has(to)) {
        seen.add(to);
        q.push(to);
      }
    }
  }
  // Any node a cycle left unreached: park it after the topo nodes by input index.
  keys.forEach((k, i) => {
    if (!seen.has(k)) {
      layer.set(k, (layer.get(k) ?? 0) || i);
      topo.push(k);
    }
  });

  const pos = layeredLayout(graph, layer);

  // ── Markov transition probabilities + per-node entropy ──────────────────────
  const probOf = new Map<string, number>(); // "from→to" → p
  const entropyOf = new Map<string, number>();
  for (const k of keys) {
    const outs = outEdges.get(k)!;
    const total = outs.reduce((s, e) => s + e.ts, 0) || 1;
    let h = 0;
    for (const e of outs) {
      const p = e.ts / total;
      probOf.set(`${k}→${e.to}`, p);
      if (p > 0) h -= p * Math.log2(p);
    }
    entropyOf.set(k, h);
  }

  // ── Modal execution path: max-product DP over the DAG (Viterbi) ─────────────
  // reach[v] = most-probable mass arriving at v from any root; back[v] = chosen
  // predecessor. Forward edges only (layer increases) so cycles can't trap it.
  const reach = new Map<string, number>();
  const back = new Map<string, string | null>();
  keys.forEach((k) => {
    reach.set(k, indeg.get(k) === 0 ? 1 : 0);
    back.set(k, null);
  });
  for (const u of topo) {
    for (const { to: v, ts } of outEdges.get(u) ?? []) {
      if ((layer.get(v) ?? 0) <= (layer.get(u) ?? 0)) continue; // skip back/flat
      const p = probOf.get(`${u}→${v}`) ?? 0;
      const cand = (reach.get(u) ?? 0) * p;
      if (cand > (reach.get(v) ?? 0)) {
        reach.set(v, cand);
        back.set(v, u);
      }
    }
  }
  // Pick the best-scoring sink (no forward out-edges) and backtrack.
  let best: string | null = null;
  let bestScore = -1;
  for (const k of keys) {
    const forwardOut = (outEdges.get(k) ?? []).some(
      (e) => (layer.get(e.to) ?? 0) > (layer.get(k) ?? 0),
    );
    if (!forwardOut && (reach.get(k) ?? 0) > bestScore) {
      bestScore = reach.get(k) ?? 0;
      best = k;
    }
  }
  const modalPath: string[] = [];
  const modalSet = new Set<string>();
  for (let cur = best; cur; cur = back.get(cur) ?? null) {
    modalPath.unshift(cur);
    modalSet.add(cur);
  }
  const modalEdges = new Set<string>();
  for (let i = 0; i + 1 < modalPath.length; i++) {
    modalEdges.add(`${modalPath[i]}→${modalPath[i + 1]}`);
  }

  // ── Per-node + per-edge stat objects ────────────────────────────────────────
  const nodeStats = new Map<string, NodeStat>();
  for (const k of keys) {
    const ins = inEdges.get(k)!.length;
    const outs = outEdges.get(k)!.length;
    nodeStats.set(k, {
      key: k,
      inDegree: ins,
      outDegree: outs,
      isRoot: ins === 0,
      isSink: outs === 0,
      isBranch: outs >= 2,
      isMerge: ins >= 2,
      onModalPath: modalSet.has(k),
      entropy: entropyOf.get(k) ?? 0,
      reachProb: reach.get(k) ?? 0,
    });
  }

  const edgeStats: EdgeStat[] = graph.edges
    .filter((e) => e.from !== e.to)
    .map((e) => {
      const id = `${e.from}→${e.to}`;
      return {
        from: e.from,
        to: e.to,
        timesSeen: e.times_seen,
        prob: probOf.get(id) ?? 0,
        onModalPath: modalEdges.has(id),
        isBackEdge: (layer.get(e.to) ?? 0) <= (layer.get(e.from) ?? 0),
      };
    });

  // ── Aggregate metrics ───────────────────────────────────────────────────────
  const branchKeys = keys.filter((k) => nodeStats.get(k)!.isBranch);
  const avgEntropy =
    branchKeys.length === 0
      ? 0
      : branchKeys.reduce((s, k) => s + (entropyOf.get(k) ?? 0), 0) /
        branchKeys.length;

  const metrics: GraphMetrics = {
    nodeCount: keys.length,
    edgeCount: edgeStats.length,
    runCount: graph.run_count,
    depth: (Math.max(0, ...keys.map((k) => layer.get(k) ?? 0)) || 0) + (keys.length ? 1 : 0),
    branchPoints: branchKeys.length,
    mergePoints: keys.filter((k) => nodeStats.get(k)!.isMerge).length,
    meanBranching: keys.length ? edgeStats.length / keys.length : 0,
    avgEntropy,
    modalCoverage: bestScore < 0 ? 0 : bestScore,
    modalPath,
    // McCabe: E - N + 2 (single connected component). >=1 when the graph has
    // nodes; rises by one per genuine decision point.
    cyclomatic: keys.length ? Math.max(1, edgeStats.length - keys.length + 2) : 0,
  };

  return { pos, nodeStats, edgeStats, metrics };
}
