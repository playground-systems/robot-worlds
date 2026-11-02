import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-materials.js', import.meta.url));
const { sourceMaterialOptions, loadSourceMaps } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

test('source material uses shared ORM channels without changing source physics', () => {
  const object = { mass: 7, friction: .2, opacity: .8,
    surface: { metallic: .9, roughness: .15, alpha_mode: 'MASK', alpha_cutoff: .3 } };
  const before = structuredClone(object);
  const maps = { map: {}, normalMap: {}, orm: {}, emissiveMap: {} };
  const options = sourceMaterialOptions(object, maps);
  assert.equal(options.aoMap, maps.orm);
  assert.equal(options.roughnessMap, maps.orm);
  assert.equal(options.metalnessMap, maps.orm);
  assert.equal(options.map, maps.map);
  assert.equal(options.normalMap, maps.normalMap);
  assert.equal(options.emissiveMap, maps.emissiveMap);
  assert.equal(options.metalness, .9); assert.equal(options.roughness, .15);
  assert.equal(options.alphaTest, .3); assert.equal(options.opacity, .8);
  assert.deepEqual(object, before);
  assert.equal(sourceMaterialOptions({ label: 'old source' }), null);
});

test('normal and ORM maps use linear data while color/emissive use sRGB', async () => {
  const calls = [];
  const object = { texture_path: 'assets/color.png', normal_texture_path: 'assets/normal.png',
    orm_texture_path: 'assets/orm.png', emissive_texture_path: 'assets/emission.png' };
  const maps = await loadSourceMaps(object, async (path, space) => {
    calls.push([path, space]); return { path };
  });
  assert.deepEqual(calls, [['assets/color.png', 'srgb'], ['assets/normal.png', 'linear'],
    ['assets/orm.png', 'linear'], ['assets/emission.png', 'srgb']]);
  assert.deepEqual(Object.keys(maps), ['map', 'normalMap', 'orm', 'emissiveMap']);
});

test('partial map failures dispose already decoded textures exactly once', async () => {
  let disposed = 0;
  const shared = { dispose() { disposed++; } };
  await assert.rejects(loadSourceMaps({ texture_path: 'color', normal_texture_path: 'normal', orm_texture_path: 'fail' },
    async path => { if (path === 'fail') throw new Error('unavailable'); return shared; }), /unavailable/);
  assert.equal(disposed, 1);
});

test('blend and opaque materials have distinct alpha behavior', () => {
  const surface = { metallic: 0, roughness: .5, alpha_mode: 'OPAQUE', alpha_cutoff: .5 };
  assert.equal(sourceMaterialOptions({ surface }).transparent, false);
  assert.equal(sourceMaterialOptions({ surface }).alphaTest, 0);
  assert.equal(sourceMaterialOptions({ surface: { ...surface, alpha_mode: 'BLEND' } }).transparent, true);
});

test('texture loading assigns correct color spaces and releases bitmaps', async () => {
  const text = await readFile(new URL('../src/robot_worlds/static/scene-textures.js', import.meta.url));
  const { loadBundledTexture } = await import(`data:text/javascript;base64,${text.toString('base64')}`);
  const savedFetch = globalThis.fetch, savedBitmap = globalThis.createImageBitmap;
  let closed = 0;
  const THREE = { SRGBColorSpace: 'srgb', NoColorSpace: '', Texture: class {
    constructor(bitmap) { this.bitmap = bitmap; }
    addEventListener(_, callback) { this.cleanup = callback; }
    dispose() { this.cleanup(); }
  } };
  try {
    globalThis.fetch = async () => ({ ok: true, headers: new Map(), arrayBuffer: async () => new ArrayBuffer(4) });
    globalThis.createImageBitmap = async () => ({ width: 1, height: 1, close() { closed++; } });
    const signal = new AbortController().signal;
    const color = await loadBundledTexture(THREE, '/', 'assets/color.png', signal);
    const normal = await loadBundledTexture(THREE, '/', 'assets/normal.png', signal, 'linear');
    assert.equal(color.colorSpace, 'srgb'); assert.equal(normal.colorSpace, '');
    color.dispose(); normal.dispose(); assert.equal(closed, 2);
    await assert.rejects(loadBundledTexture(THREE, '/', '../private.png', signal, 'linear'), /Unbundled/);
  } finally {
    globalThis.fetch = savedFetch; globalThis.createImageBitmap = savedBitmap;
  }
});
