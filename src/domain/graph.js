export const NODE_TYPES = ['application', 'module', 'controller', 'service', 'repository', 'database', 'cache', 'queue', 'worker', 'external_api', 'infrastructure', 'authentication'];
export const EDGE_TYPES = ['imports', 'calls', 'depends_on', 'reads', 'writes', 'publishes', 'consumes', 'exposes', 'authenticates_with'];
export const ORIGINS = ['static', 'heuristic', 'llm'];

function validateEvidence(evidence) {
  if (!Array.isArray(evidence) || !evidence.length) throw new Error('Evidence is required');
  for (const e of evidence) {
    if (typeof e.file !== 'string' || !e.file || e.file.startsWith('/') || e.file.includes('..') || e.file.includes('\\') || /^[A-Za-z]:/.test(e.file)) throw new Error('Evidence must use repository-relative paths');
    if (!Number.isInteger(e.line) || e.line < 1 || !e.reason || !ORIGINS.includes(e.origin)) throw new Error('Invalid evidence');
  }
}
function validateCommon(item) {
  if (!Number.isFinite(item.confidence) || item.confidence < 0 || item.confidence > 1) throw new Error('Invalid confidence');
  validateEvidence(item.evidence);
}
export function validateGraph(graph) {
  const ids = new Set();
  if (graph.schema_version !== '1.0') throw new Error('Unsupported graph schema');
  for (const n of graph.nodes) {
    if (!n.id || ids.has(n.id) || !n.label || !NODE_TYPES.includes(n.type)) throw new Error('Invalid or duplicate node');
    validateCommon(n);
    if (!Array.isArray(n.source_files) || !n.evidence.every(e => n.source_files.includes(e.file))) throw new Error('Missing source file');
    ids.add(n.id);
  }
  for (const e of graph.edges) {
    if (!ids.has(e.source) || !ids.has(e.target) || !EDGE_TYPES.includes(e.type)) throw new Error('Invalid edge');
    validateCommon(e);
  }
  return graph;
}
export class GraphBuilder {
  constructor(name) { this.name = name; this.nodes = new Map(); this.edges = new Map(); this.warnings = []; }
  node(id, label, type, evidence, confidence = 1, metadata = {}) {
    const current = this.nodes.get(id);
    if (current) {
      current.evidence = unique([...current.evidence, ...evidence]);
      current.source_files = [...new Set(current.evidence.map(e => e.file))];
      current.confidence = Math.max(current.confidence, confidence);
      Object.assign(current.metadata, metadata);
    } else this.nodes.set(id, { id, label, type, source_files: [...new Set(evidence.map(e => e.file))], metadata, confidence, evidence });
    return id;
  }
  edge(source, target, type, evidence, confidence = 1) {
    if (source === target) return;
    const key = `${source}|${target}|${type}`;
    const current = this.edges.get(key);
    this.edges.set(key, { source, target, type, evidence: unique([...(current?.evidence ?? []), ...evidence]), confidence: Math.max(current?.confidence ?? 0, confidence) });
  }
  build() {
    return validateGraph({ schema_version: '1.0', name: this.name, nodes: [...this.nodes.values()].sort((a,b) => a.id.localeCompare(b.id)), edges: [...this.edges.values()], warnings: this.warnings });
  }
}
function unique(items) { return [...new Map(items.map(e => [JSON.stringify(e), e])).values()]; }
export function evidence(file, line, reason, origin = 'static') { return { file, line, reason, origin }; }

export function component(graph, id) {
  const node = graph.nodes.find(n => n.id === id);
  if (!node) throw Object.assign(new Error('Component not found'), { status: 404 });
  return node;
}
export function dependencies(graph, id, direction = 'outgoing') {
  component(graph, id);
  if (!['incoming', 'outgoing'].includes(direction)) throw new Error('Invalid direction');
  return graph.edges.filter(e => e[direction === 'outgoing' ? 'source' : 'target'] === id);
}
export function trace(graph, from, to) {
  component(graph, from); component(graph, to);
  const queue = [{ id: from, path: [] }], seen = new Set([from]);
  for (let i = 0; i < queue.length; i++) {
    const current = queue[i];
    if (current.id === to) return current.path;
    for (const edge of dependencies(graph, current.id)) if (!seen.has(edge.target)) {
      seen.add(edge.target); queue.push({ id: edge.target, path: [...current.path, edge] });
    }
  }
  return null;
}
