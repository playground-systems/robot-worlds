import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-textures.js', import.meta.url));
const { loadBundledTexture } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

test('bundled texture preserves OBJ orientation/color and releases its decoded bitmap', async t => {
  let closed = 0;
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, '/world/assets/image.png');
    return new Response(new Uint8Array([1, 2, 3]));
  });
  const prior = globalThis.createImageBitmap;
  globalThis.createImageBitmap = async (blob, options) => {
    assert.equal(blob.type, 'image/png');
    assert.equal(options.imageOrientation, 'flipY');
    return { width: 1024, height: 1024, close() { closed++; } };
  };
  t.after(() => { globalThis.createImageBitmap = prior; });
  class Texture {
    constructor(image) { this.image = image; }
    addEventListener(name, callback) { assert.equal(name, 'dispose'); this.dispose = callback; }
  }
  const texture = await loadBundledTexture({ Texture, SRGBColorSpace: 'srgb' }, '/world/', 'assets/image.png', new AbortController().signal);
  assert.equal(texture.colorSpace, 'srgb');
  assert.equal(texture.needsUpdate, true);
  texture.dispose(); assert.equal(closed, 1);
});

test('texture loader rejects external paths and oversized encoded/decoded images', async t => {
  const signal = new AbortController().signal;
  await assert.rejects(loadBundledTexture({}, '/world/', 'https://other.example/image.png', signal), /Unbundled/);
  t.mock.method(globalThis, 'fetch', async () => new Response('', { headers: { 'content-length': '99999999' } }));
  await assert.rejects(loadBundledTexture({}, '/world/', 'assets/image.png', signal), /budget/);
  t.mock.method(globalThis, 'fetch', async () => new Response('small encoded input'));
  let closed = false;
  const prior = globalThis.createImageBitmap;
  globalThis.createImageBitmap = async () => ({ width: 8192, height: 8192, close() { closed = true; } });
  t.after(() => { globalThis.createImageBitmap = prior; });
  await assert.rejects(loadBundledTexture({}, '/world/', 'assets/image.png', signal), /decoded image budget/);
  assert.equal(closed, true);
});
