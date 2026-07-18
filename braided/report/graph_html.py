"""Self-contained live DAG page (task 5.4). No CDN, no build, no server
beyond `python -m http.server` in the run dir. Reads graph.json (exported by
`braided report --json`, or continuously by the runner via --json polling)
every 3s and re-renders. Touches nothing in the loop."""

from __future__ import annotations

from pathlib import Path

PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>braided autoresearch — live DAG</title>
<style>
  body { background: #0d1117; color: #c9d1d9; font: 13px/1.4 ui-monospace, monospace; margin: 20px; }
  h1 { font-size: 16px; color: #e6edf3; }
  #meta { color: #8b949e; margin-bottom: 12px; }
  svg { width: 100%; }
  .node rect { rx: 4px; }
  .node text { fill: #e6edf3; font: 11px ui-monospace, monospace; }
  .score { fill: #7ee787 !important; font-weight: bold; }
  .edge { stroke: #30363d; stroke-width: 1.5; fill: none; }
  .edge.merge { stroke: #58a6ff; stroke-dasharray: 4 3; }
  .repl { fill: #f85149; font-size: 13px; }
</style>
</head>
<body>
<h1>braided autoresearch — experiment DAG</h1>
<div id="meta">waiting for graph.json …</div>
<svg id="dag"></svg>
<script>
const COLW = 190, ROWH = 46, PAD = 20;

function layout(nodes, edges) {
  const bySha = Object.fromEntries(nodes.map(n => [n.sha, n]));
  const children = {}; const indeg = {};
  nodes.forEach(n => indeg[n.sha] = 0);
  edges.forEach(e => {
    (children[e.from] = children[e.from] || []).push(e.to);
    if (e.to in indeg) indeg[e.to]++;
  });
  // longest-path depth (left→right)
  const depth = {}; const queue = nodes.filter(n => indeg[n.sha] === 0).map(n => n.sha);
  queue.forEach(s => depth[s] = 0);
  const indegLeft = {...indeg};
  while (queue.length) {
    const s = queue.shift();
    (children[s] || []).forEach(c => {
      depth[c] = Math.max(depth[c] ?? 0, depth[s] + 1);
      if (--indegLeft[c] === 0) queue.push(c);
    });
  }
  // rows: one per branch, stable order
  const branches = [...new Set(nodes.map(n => n.branch || "?"))].sort();
  const row = Object.fromEntries(branches.map((b, i) => [b, i]));
  nodes.forEach(n => {
    n.x = PAD + (depth[n.sha] ?? 0) * COLW;
    n.y = PAD + row[n.branch || "?"] * ROWH;
  });
  return { bySha, rows: branches.length, cols: Math.max(...nodes.map(n => (depth[n.sha] ?? 0))) + 1 };
}

function fmt(x) { return x == null ? "?" : (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(3)); }

async function refresh() {
  let data;
  try { data = await (await fetch("graph.json?" + Date.now())).json(); }
  catch (e) { return; }
  const { nodes, edges } = data;
  const { bySha, rows, cols } = layout(nodes, edges);
  const svg = document.getElementById("dag");
  svg.setAttribute("viewBox", `0 0 ${cols * COLW + 2 * PAD} ${rows * ROWH + 2 * PAD}`);
  svg.style.height = (rows * ROWH + 2 * PAD) + "px";
  let s = "";
  edges.forEach(e => {
    const a = bySha[e.from], b = bySha[e.to];
    if (!a || !b) return;
    const mx = (a.x + 150 + b.x) / 2;
    s += `<path class="edge${e.merge ? " merge" : ""}" d="M${a.x + 150},${a.y + 14} C${mx},${a.y + 14} ${mx},${b.y + 14} ${b.x},${b.y + 14}"/>`;
  });
  const best = Math.max(...nodes.filter(n => n.score != null).map(n => n.score));
  nodes.forEach(n => {
    const isBest = n.score === best;
    const fill = n.merge ? "#1f3a5f" : n.kind === "baseline" ? "#30363d" : "#1a2f1a";
    const stroke = isBest ? "#f0883e" : n.merge ? "#58a6ff" : "#238636";
    const shape = n.merge
      ? `<rect x="${n.x}" y="${n.y}" width="150" height="28" fill="${fill}" stroke="${stroke}" transform="" />`
      : `<rect x="${n.x}" y="${n.y}" width="150" height="28" fill="${fill}" stroke="${stroke}"/>`;
    s += `<g class="node">${shape}
      <text x="${n.x + 6}" y="${n.y + 12}">${n.short} ${n.replicated ? '<tspan class="repl">◆</tspan>' : ""} <tspan>(${n.branch || "?"})</tspan></text>
      <text class="score" x="${n.x + 6}" y="${n.y + 24}">${fmt(n.score)}</text>
      <title>${(n.rationale || "").replace(/[<>&]/g, "")}</title></g>`;
  });
  svg.innerHTML = s;
  document.getElementById("meta").textContent =
    `${nodes.length} nodes · best ${fmt(best)} · ` + new Date().toLocaleTimeString();
}
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


def write_graph_html(run_dir: str | Path) -> Path:
    out = Path(run_dir) / "graph.html"
    out.write_text(PAGE)
    return out
