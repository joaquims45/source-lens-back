import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GraphBuilder, evidence, validateGraph, trace } from '../src/domain/graph.js';
test('requires provenance and valid endpoints, deduplicates evidence, traverses cycles', () => {
  const b = new GraphBuilder('test'), e = [evidence('src/a.ts', 1, 'import')];
  for (const id of ['a','b','c']) b.node(id, id, 'module', e);
  b.node('a', 'a', 'module', e);
  b.edge('a','b','imports',e); b.edge('b','a','imports',e); b.edge('b','c','imports',e);
  const graph = b.build();
  assert.equal(graph.nodes[0].evidence.length, 1);
  assert.equal(trace(graph,'a','c').length, 2);
  assert.equal(trace(graph,'c','a'), null);
  assert.throws(() => validateGraph({...graph, edges:[{...graph.edges[0], target:'absent'}]}));
  assert.throws(() => new GraphBuilder('bad').node('x','x','module',[]).build());
});
