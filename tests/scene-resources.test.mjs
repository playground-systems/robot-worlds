import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-resources.js', import.meta.url));
const { disposeTree } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

test('replacing a scene releases instance buffers and shared GPU resources once', () => {
  const counts = {};
  const resource = name => ({ dispose() { counts[name] = (counts[name] ?? 0) + 1; } });
  const texture = { ...resource('texture'), isTexture: true };
  const material = { ...resource('material'), map: texture, normalMap: texture };
  const geometry = resource('geometry');
  const nodes = [
    { ...resource('instances'), isInstancedMesh: true, geometry, material: [material] },
    { geometry, material },
    { shadow: resource('shadow') },
  ];
  disposeTree({ traverse: callback => nodes.forEach(callback) });
  assert.deepEqual(counts, { instances: 1, geometry: 1, texture: 1, material: 1, shadow: 1 });
});
