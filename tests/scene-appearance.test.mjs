import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-appearance.js', import.meta.url));
const { estimatedAppearance, studioEnvironmentPixels, APPEARANCE_PROFILE_COUNT, APPEARANCE_NOTICE } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

test('finish estimates preserve geometry/color/physics and disclose the inference', () => {
  const object = { id: 'steel_workpiece', label: 'Bare stainless part', color: [.6, .62, .64], size: [.1, .2, .3], position: [1, 2, 3], mass: 5, friction: [.8, .1, .1] };
  const original = structuredClone(object), estimate = estimatedAppearance(object);
  assert.equal(estimate.id, 'metal'); assert.equal(estimate.estimated, true);
  assert.match(APPEARANCE_NOTICE, /visual estimates/);
  assert.deepEqual(object, original);
  for (const key of ['color', 'size', 'position', 'mass', 'friction', 'emissive', 'transmission']) assert(!(key in estimate));
  assert.equal(estimate.opacity, 1);
  assert.deepEqual(estimatedAppearance(object), estimate);
});

test('explicit names take precedence and ambiguous geometry gets a neutral finish', () => {
  const expected = [
    ['rubber gasket', 'rubber'], ['cardboard carton', 'cardboard'], ['wooden rack', 'wood'],
    ['plastic tote', 'polymer'], ['concrete wall', 'architectural'], ['painted cabinet', 'painted'],
    ['unknown object', 'matte'], ['glass panel', 'matte'], ['screen', 'matte'],
  ];
  for (const [label, id] of expected) {
    const estimate = estimatedAppearance({ label, color: [.5, .5, .5] }); assert.equal(estimate.id, id);
    assert.equal(estimate.opacity, 1); assert(!('emissive' in estimate));
  }
  assert.equal(estimatedAppearance({ id: 'steel_rack', color: [.8, .1, .1] }).id, 'painted');
  assert.equal(estimatedAppearance({ id: 'steel_rack', color: [.5, .51, .5] }).id, 'metal');
});

test('large arrays of entities share a bounded material palette, not one material per entity', () => {
  const ids = new Set();
  for (let i = 0; i < 1000; i++) {
    const estimate = estimatedAppearance({ id: `rack_${i}`, color: [i/1000, .4, .3] });
    ids.add(estimate.id); assert(estimate.metalness >= 0 && estimate.metalness <= 1);
    assert(estimate.roughness >= 0 && estimate.roughness <= 1);
  }
  assert.equal(ids.size, 1); assert(APPEARANCE_PROFILE_COUNT <= 8);
});

test('studio reflections use a deterministic tiny neutral map without external assets', () => {
  const first = studioEnvironmentPixels(), next = studioEnvironmentPixels();
  assert.equal(first.width, 64); assert.equal(first.height, 32); assert.equal(first.data.byteLength, 8192);
  assert.deepEqual(first.data, next.data);
  let min = 255, max = 0;
  for (let i = 0; i < first.data.length; i += 4) {
    assert.equal(first.data[i+3], 255); min = Math.min(min, first.data[i]); max = Math.max(max, first.data[i]);
  }
  assert(max-min > 100, 'reflection highlights distinguish metallic surfaces');
  assert(first.data[0] > first.data[(first.height-1)*first.width*4], 'upper hemisphere stays brighter');
});
