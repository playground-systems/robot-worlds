import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-textures.js', import.meta.url));
const { loadBundledTexture, readAssetBytes, fetchAssetBytes, TextureBudget } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

function png(width = 1024, height = 1024) {
  const bytes = new Uint8Array(33);
  bytes.set([137,80,78,71,13,10,26,10]);
  const header = new DataView(bytes.buffer);
  header.setUint32(8, 13); header.setUint32(12, 0x49484452);
  header.setUint32(16, width); header.setUint32(20, height);
  return bytes; // Synthetic header; no claim of real decoding in these tests.
}
class Texture {
  constructor(image) { this.image = image; }
  addEventListener(name, callback) { assert.equal(name, 'dispose'); this.dispose = callback; }
}
const THREE = { Texture, SRGBColorSpace: 'srgb', NoColorSpace: 'linear' };
function bitmap(t, implementation) {
  const prior = globalThis.createImageBitmap;
  globalThis.createImageBitmap = implementation;
  t.after(() => { globalThis.createImageBitmap = prior; });
}
const signal = () => new AbortController().signal;

test('bundled texture preserves OBJ orientation/color and releases its bitmap and reservation once', async t => {
  let closed = 0;
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, '/world/assets/image.png');
    return new Response(png());
  });
  bitmap(t, async (blob, options) => {
    assert.equal(blob.type, 'image/png'); assert.equal(options.imageOrientation, 'flipY');
    return { width: 1024, height: 1024, close() { closed++; } };
  });
  const budget = new TextureBudget();
  const texture = await loadBundledTexture(THREE, '/world/', 'assets/image.png', signal(), 'srgb', budget);
  assert.equal(texture.colorSpace, 'srgb'); assert.equal(texture.needsUpdate, true);
  assert.equal(budget.used, Math.ceil(1024*1024*4*4/3));
  texture.dispose(); texture.dispose(); assert.equal(closed, 1); assert.equal(budget.used, 0);
});

test('texture loader rejects bad paths and oversized headers before calling the decoder', async t => {
  bitmap(t, () => assert.fail('oversized or malformed image must not be decoded'));
  await assert.rejects(loadBundledTexture(THREE, '/', 'https://other.example/image.png', signal()), /Unbundled/);
  t.mock.method(globalThis, 'fetch', async () => new Response('', { headers: { 'content-length': '99999999' } }));
  await assert.rejects(loadBundledTexture(THREE, '/', 'assets/image.png', signal()), /budget/);
  for (const bytes of [png(8192,8192), png(16384,32), png(0,1), new Uint8Array(40)]) {
    t.mock.method(globalThis, 'fetch', async () => new Response(bytes));
    await assert.rejects(loadBundledTexture(THREE, '/', 'assets/image.png', signal()), /budget|PNG header/);
  }
});

test('streaming limits cancel bodies even with absent or false content lengths', async () => {
  for (const headers of [{}, { 'content-length': '1' }]) {
    let produced = 0, cancelled = 0;
    const body = new ReadableStream({
      pull(controller) { produced++; controller.enqueue(new Uint8Array(8)); },
      cancel() { cancelled++; },
    });
    await assert.rejects(readAssetBytes(new Response(body, { headers }), 12, signal()), /budget/);
    assert.equal(cancelled, 1); assert(produced < 5);
  }
});

test('aborting a pending body read cancels its reader and releases the lock', async () => {
  let cancelled = 0;
  const body = new ReadableStream({ cancel() { cancelled++; } });
  const controller = new AbortController();
  const pending = readAssetBytes(new Response(body), 20, controller.signal);
  controller.abort();
  await assert.rejects(pending, /cancelled/);
  assert.equal(cancelled, 1); assert.equal(body.locked, false);
});

test('scene texture reservations prevent concurrent decodes from overspending and permit reuse after disposal', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(png()));
  let complete, started;
  const decoding = new Promise(resolve => { started = resolve; });
  bitmap(t, () => { started(); return new Promise(resolve => { complete = resolve; }); });
  const budget = new TextureBudget(6*1024*1024);
  const first = loadBundledTexture(THREE, '/', 'assets/a.png', signal(), 'srgb', budget);
  await decoding;
  await assert.rejects(loadBundledTexture(THREE, '/', 'assets/b.png', signal(), 'linear', budget), /Scene texture budget/);
  complete({ width: 1024, height: 1024, close() {} });
  const texture = await first;
  texture.dispose(); assert.equal(budget.used, 0);
  const release = budget.reserve(1024,1024); release(); assert.equal(budget.used, 0);
});

test('decoder failure and unexpected dimensions release reserved texture capacity', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(png()));
  const budget = new TextureBudget();
  bitmap(t, async () => { throw new Error('decoder failure'); });
  await assert.rejects(loadBundledTexture(THREE, '/', 'assets/a.png', signal(), 'srgb', budget), /decoder failure/);
  assert.equal(budget.used, 0);
  let closed = 0;
  globalThis.createImageBitmap = async () => ({ width: 8192, height: 8192, close() { closed++; } });
  await assert.rejects(loadBundledTexture(THREE, '/', 'assets/a.png', signal(), 'srgb', budget), /dimensions differ/);
  assert.equal(closed, 1); assert.equal(budget.used, 0);
});

test('many small chunks are accumulated into bounded storage without changing bytes', async () => {
  let next = 0;
  const body = new ReadableStream({ pull(controller) {
    if (next === 70000) { controller.close(); return; }
    const length = Math.min(17, 70000-next);
    controller.enqueue(Uint8Array.from({ length }, (_, i) => (next+i)%251)); next += length;
  } });
  const bytes = await readAssetBytes(new Response(body), 80000, signal());
  assert.equal(bytes.byteLength, 70000); assert(bytes.buffer.byteLength <= 80000);
  assert(bytes.every((value, i) => value === i%251));
});

test('request timeout aborts stalled headers without cancelling the displayed world', async t => {
  let child;
  t.mock.method(globalThis, 'fetch', async (_url, { signal }) => {
    child = signal;
    return new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('fetch aborted')), { once: true }));
  });
  const parent = new AbortController();
  await assert.rejects(fetchAssetBytes('/asset', 100, parent.signal, { timeoutMs: 10 }), /Source load timed out/);
  assert(child.aborted); assert.equal(parent.signal.aborted, false);
});

test('request deadline includes a stalled body after headers arrive', async t => {
  let cancelled = 0;
  const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([1])); }, cancel() { cancelled++; } });
  t.mock.method(globalThis, 'fetch', async () => new Response(body));
  await assert.rejects(fetchAssetBytes('/asset', 100, signal(), { timeoutMs: 10 }), /Source load timed out/);
  assert.equal(cancelled, 1); assert.equal(body.locked, false);
});

test('parent cancellation propagates immediately and invalid/already-cancelled requests never fetch', async t => {
  let calls = 0, child;
  t.mock.method(globalThis, 'fetch', async (_url, { signal }) => {
    calls++; child = signal;
    return new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('fetch aborted')), { once: true }));
  });
  const parent = new AbortController();
  const pending = fetchAssetBytes('/asset', 100, parent.signal);
  parent.abort();
  await assert.rejects(pending, /cancelled/); assert(child.aborted);
  await assert.rejects(fetchAssetBytes('/asset', 100, parent.signal), /cancelled/);
  for (const timeoutMs of [0, NaN, Infinity, 120001]) {
    await assert.rejects(fetchAssetBytes('/asset', 100, signal(), { timeoutMs }), /Invalid/);
  }
  assert.equal(calls, 1);
});

test('successful request removes its cancellation relay and deadline', async t => {
  let child;
  t.mock.method(globalThis, 'fetch', async (_url, { signal }) => { child = signal; return new Response('ok'); });
  const parent = new AbortController();
  assert.deepEqual([...await fetchAssetBytes('/asset', 100, parent.signal, { timeoutMs: 10 })], [111,107]);
  parent.abort();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(child.aborted, false);
});
