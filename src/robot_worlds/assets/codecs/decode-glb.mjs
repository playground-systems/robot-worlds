// Decode embedded meshopt buffer views without changing scene/material semantics.
// Deliberately no browser, renderer, workers, network or source-script evaluation.
import { readFileSync, writeFileSync, statSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { MeshoptDecoder } from './meshopt_decoder.mjs';

export const MAX_INPUT = 32 * 1024 * 1024;
export const MAX_OUTPUT = 128 * 1024 * 1024;
const JSON_CHUNK = 0x4e4f534a;
const BIN_CHUNK = 0x004e4942;
const EXTENSION = 'EXT_meshopt_compression';

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}
function integer(value, minimum = 0) {
  requireValue(Number.isSafeInteger(value) && value >= minimum, 'Invalid GLB integer');
  return value;
}
function span(bytes, offset, length) {
  integer(offset); integer(length);
  requireValue(offset <= bytes.length && length <= bytes.length - offset, 'GLB range out of bounds');
  return bytes.subarray(offset, offset + length);
}

export async function decodeGlb(input) {
  requireValue(Buffer.isBuffer(input) && input.length >= 28 && input.length <= MAX_INPUT, 'GLB input size limit');
  requireValue(input.toString('ascii', 0, 4) === 'glTF' && input.readUInt32LE(4) === 2
    && input.readUInt32LE(8) === input.length, 'Invalid GLB header');
  const jsonLength = input.readUInt32LE(12);
  requireValue(jsonLength <= 8 * 1024 * 1024 && jsonLength % 4 === 0
    && input.readUInt32LE(16) === JSON_CHUNK, 'Invalid GLB JSON chunk');
  const document = JSON.parse(span(input, 20, jsonLength).toString('utf8'));
  const binHeader = 20 + jsonLength;
  requireValue(binHeader + 8 <= input.length && input.readUInt32LE(binHeader + 4) === BIN_CHUNK, 'Missing GLB binary chunk');
  const binLength = input.readUInt32LE(binHeader);
  requireValue(binLength % 4 === 0 && binHeader + 8 + binLength === input.length, 'Invalid GLB binary length');
  const binary = span(input, binHeader + 8, binLength);
  requireValue(document.asset?.version === '2.0' && Array.isArray(document.buffers)
    && document.buffers.length >= 1 && document.buffers.length <= 2, 'Unsupported GLB buffers');
  const declared = integer(document.buffers[0].byteLength);
  requireValue(declared <= binary.length && binary.length - declared <= 3, 'Binary buffer size mismatch');
  for (let i = 0; i < document.buffers.length; i++) {
    const buffer = document.buffers[i];
    requireValue(!('uri' in buffer), 'External buffer URI is unsupported');
    integer(buffer.byteLength);
    if (i > 0) requireValue(buffer.extensions?.[EXTENSION]?.fallback === true, 'Unsupported secondary buffer');
  }
  for (const image of document.images ?? []) requireValue(!('uri' in image), 'External image URI is unsupported');
  requireValue(Array.isArray(document.bufferViews) && document.bufferViews.length <= 8192, 'GLB buffer view limit');
  // Check the whole decoded allocation before calling the decoder.
  let total = 0;
  const plans = document.bufferViews.map(view => {
    const length = integer(view.byteLength);
    const offset = total;
    total += Math.ceil(length / 4) * 4;
    requireValue(total <= MAX_OUTPUT, 'Decoded GLB exceeds size budget');
    const compressed = view.extensions?.[EXTENSION];
    if (compressed) {
      requireValue(compressed.buffer === 0, 'Compressed source must be embedded');
      const count = integer(compressed.count, 1), stride = integer(compressed.byteStride, 1);
      requireValue(count * stride === length, 'Decoded buffer view size mismatch');
      requireValue(['ATTRIBUTES', 'TRIANGLES', 'INDICES'].includes(compressed.mode), 'Unsupported meshopt mode');
      const filter = compressed.filter ?? 'NONE';
      requireValue(['NONE', 'OCTAHEDRAL', 'QUATERNION', 'EXPONENTIAL'].includes(filter), 'Unsupported meshopt filter');
      if (compressed.mode === 'ATTRIBUTES') {
        requireValue(stride % 4 === 0 && stride <= 256, 'Invalid attribute stride');
        if (filter === 'OCTAHEDRAL') requireValue(stride === 4 || stride === 8, 'Invalid octahedral stride');
        if (filter === 'QUATERNION') requireValue(stride === 8, 'Invalid quaternion stride');
        if (view.byteStride !== undefined) requireValue(view.byteStride === stride, 'Mismatched attribute stride');
      } else {
        requireValue((stride === 2 || stride === 4) && filter === 'NONE', 'Invalid index encoding');
        if (compressed.mode === 'TRIANGLES') requireValue(count % 3 === 0, 'Invalid triangle count');
      }
      const source = span(binary.subarray(0, declared), compressed.byteOffset ?? 0, integer(compressed.byteLength, 1));
      return { view, offset, length, compressed, source };
    }
    requireValue(view.buffer === 0, 'Undecodable fallback buffer view');
    return { view, offset, length, source: span(binary.subarray(0, declared), view.byteOffset ?? 0, length) };
  });
  await MeshoptDecoder.ready;
  const decoded = Buffer.alloc(total);
  for (const plan of plans) {
    const target = decoded.subarray(plan.offset, plan.offset + plan.length);
    const c = plan.compressed;
    if (c) MeshoptDecoder.decodeGltfBuffer(target, c.count, c.byteStride, plan.source, c.mode, c.filter ?? 'NONE');
    else target.set(plan.source);
    plan.view.buffer = 0;
    plan.view.byteOffset = plan.offset;
    if (plan.view.extensions) {
      delete plan.view.extensions[EXTENSION];
      if (Object.keys(plan.view.extensions).length === 0) delete plan.view.extensions;
    }
  }
  document.buffers = [{ byteLength: decoded.length }];
  for (const key of ['extensionsUsed', 'extensionsRequired']) {
    if (document[key]) document[key] = document[key].filter(name => name !== EXTENSION);
  }
  const json = Buffer.from(JSON.stringify(document));
  const paddedJson = Math.ceil(json.length / 4) * 4;
  const resultLength = 28 + paddedJson + decoded.length;
  requireValue(resultLength <= MAX_OUTPUT, 'Decoded GLB exceeds total size budget');
  const result = Buffer.alloc(resultLength);
  result.write('glTF'); result.writeUInt32LE(2, 4); result.writeUInt32LE(resultLength, 8);
  result.writeUInt32LE(paddedJson, 12); result.writeUInt32LE(JSON_CHUNK, 16);
  result.fill(0x20, 20, 20 + paddedJson); json.copy(result, 20);
  result.writeUInt32LE(decoded.length, 20 + paddedJson);
  result.writeUInt32LE(BIN_CHUNK, 24 + paddedJson);
  decoded.copy(result, 28 + paddedJson);
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    requireValue(process.argv.length === 4, 'Expected input and output paths');
    requireValue(statSync(process.argv[2]).size <= MAX_INPUT, 'GLB input size limit');
    const output = await decodeGlb(readFileSync(process.argv[2]));
    writeFileSync(process.argv[3], output, { flag: 'wx' });
  } catch {
    // Do not print source JSON, paths, or an untrusted decoder exception.
    process.stderr.write('GLB decoding failed validation or resource limits\n');
    process.exitCode = 1;
  }
}
