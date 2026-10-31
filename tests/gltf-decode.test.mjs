import assert from 'node:assert/strict';
import test from 'node:test';
import { decodeGlb, MAX_OUTPUT } from '../src/robot_worlds/assets/codecs/decode-glb.mjs';

// Original three-vertex fixture encoded with meshoptimizer 1.3.0.
const vertices = Buffer.from('oAAAATwAAAD//wE8AAAAfn0AAAEMAAAA/wEMAAAAfgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==', 'base64');
const indices = Buffer.from('4fAAdodWZ3iphmWJaJgBaQAA', 'base64');
const image = Buffer.from('unchanged embedded texture');
const binary = Buffer.concat([vertices, indices, image]);

function scene() {
  return {
    asset: { version: '2.0', extras: { source: 'fixture' } },
    buffers: [{ byteLength: binary.length }, { byteLength: 42, extensions: { EXT_meshopt_compression: { fallback: true } } }],
    bufferViews: [
      { buffer: 1, byteLength: 36, byteStride: 12, extensions: { EXT_meshopt_compression: {
        buffer: 0, byteOffset: 0, byteLength: vertices.length, count: 3, byteStride: 12, mode: 'ATTRIBUTES' } } },
      { buffer: 1, byteOffset: 36, byteLength: 6, extensions: { EXT_meshopt_compression: {
        buffer: 0, byteOffset: vertices.length, byteLength: indices.length, count: 3, byteStride: 2, mode: 'TRIANGLES' } } },
      { buffer: 0, byteOffset: vertices.length + indices.length, byteLength: image.length },
    ],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' },
      { bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }] }],
    images: [{ bufferView: 2, mimeType: 'image/webp' }],
    materials: [{ pbrMetallicRoughness: { roughnessFactor: .4, metallicFactor: .8 } }],
    nodes: [{ mesh: 0, translation: [2, 0, 0], extras: { joints: [{ type: 'revolute', axis: [0, 1, 0], min: 0, max: 90 }] } }],
    extensionsUsed: ['EXT_meshopt_compression', 'EXT_texture_webp', 'KHR_physics_rigid_bodies'],
    extensionsRequired: ['EXT_meshopt_compression', 'EXT_texture_webp'],
    extensions: { KHR_physics_rigid_bodies: { physicsMaterials: [{ friction: .5 }] } },
  };
}

function pack(document, bin = binary) {
  const text = Buffer.from(JSON.stringify(document));
  const jsonSize = Math.ceil(text.length / 4) * 4, binSize = Math.ceil(bin.length / 4) * 4;
  const result = Buffer.alloc(28 + jsonSize + binSize);
  result.write('glTF'); result.writeUInt32LE(2, 4); result.writeUInt32LE(result.length, 8);
  result.writeUInt32LE(jsonSize, 12); result.writeUInt32LE(0x4e4f534a, 16);
  result.fill(0x20, 20, 20 + jsonSize); text.copy(result, 20);
  result.writeUInt32LE(binSize, 20 + jsonSize); result.writeUInt32LE(0x004e4942, 24 + jsonSize);
  bin.copy(result, 28 + jsonSize);
  return result;
}

function unpack(bytes) {
  const size = bytes.readUInt32LE(12);
  return { document: JSON.parse(bytes.subarray(20, 20 + size)), binary: bytes.subarray(28 + size) };
}

test('decodes actual compressed vertices/triangles and preserves image/material/joint metadata', async () => {
  const original = scene();
  const { document, binary: decoded } = unpack(await decodeGlb(pack(original)));
  const position = new Float32Array(decoded.buffer, decoded.byteOffset, 9);
  assert.deepEqual([...position], [0, 0, 0, 1, 0, 0, 0, 1, 0]);
  assert.deepEqual([...new Uint16Array(decoded.buffer, decoded.byteOffset + 36, 3)], [0, 1, 2]);
  const textureView = document.bufferViews[2];
  assert.deepEqual(decoded.subarray(textureView.byteOffset, textureView.byteOffset + textureView.byteLength), image);
  for (const key of ['nodes', 'materials', 'meshes', 'accessors', 'images', 'extensions', 'asset']) {
    assert.deepEqual(document[key], original[key]);
  }
  assert.equal(document.buffers.length, 1);
  assert.ok(document.bufferViews.every(view => view.buffer === 0 && !view.extensions));
  assert.deepEqual(document.extensionsRequired, ['EXT_texture_webp']);
});

test('decoded GLB can be processed again without changing bytes', async () => {
  const first = await decodeGlb(pack(scene()));
  assert.deepEqual(await decodeGlb(first), first);
});

for (const [name, mutate] of [
  ['external buffer', doc => { doc.buffers[0].uri = 'https://example.com/mesh'; }],
  ['external image', doc => { doc.images[0].uri = '../../private.png'; }],
  ['unmarked fallback', doc => { delete doc.buffers[1].extensions; }],
  ['bad compressed range', doc => { doc.bufferViews[0].extensions.EXT_meshopt_compression.byteOffset = binary.length; }],
  ['huge output', doc => { doc.bufferViews[0].byteLength = MAX_OUTPUT + 4; }],
  ['size mismatch', doc => { doc.bufferViews[0].byteLength = 32; }],
  ['invalid stride', doc => { doc.bufferViews[0].extensions.EXT_meshopt_compression.byteStride = 3; }],
  ['invalid mode', doc => { doc.bufferViews[0].extensions.EXT_meshopt_compression.mode = 'arbitrary'; }],
  ['invalid filter', doc => { doc.bufferViews[0].extensions.EXT_meshopt_compression.filter = 'arbitrary'; }],
  ['invalid oct stride', doc => { doc.bufferViews[0].extensions.EXT_meshopt_compression.filter = 'OCTAHEDRAL'; }],
  ['fractional offset', doc => { doc.bufferViews[0].extensions.EXT_meshopt_compression.byteOffset = .5; }],
  ['unknown compressed buffer', doc => { doc.bufferViews[0].extensions.EXT_meshopt_compression.buffer = 1; }],
]) {
  test(`rejects ${name}`, async () => {
    const doc = scene(); mutate(doc);
    await assert.rejects(decodeGlb(pack(doc)));
  });
}

test('rejects damaged source bytes instead of returning partial geometry', async () => {
  const damaged = Buffer.from(binary); damaged[0] = 0;
  await assert.rejects(decodeGlb(pack(scene(), damaged)));
});

test('rejects truncated or mismatched container lengths', async () => {
  const valid = pack(scene());
  for (const length of [0, 12, valid.length - 1]) {
    await assert.rejects(decodeGlb(valid.subarray(0, length)));
  }
  const trailing = Buffer.concat([valid, Buffer.alloc(4)]);
  trailing.writeUInt32LE(trailing.length, 8);
  await assert.rejects(decodeGlb(trailing));
});
