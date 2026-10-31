/** Bound encoded reads even when Content-Length is absent or incorrect. */
export async function readAssetBytes(response, limit, signal) {
  if (!Number.isSafeInteger(limit) || limit <= 0) throw new Error('Invalid asset byte budget');
  const reject = async message => {
    try { await response.body?.cancel(); } catch {}
    throw new Error(message);
  };
  if (signal.aborted) return reject('Asset load cancelled');
  if (Number(response.headers.get('content-length')) > limit) return reject('Asset exceeds viewer budget');
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  let bytes = new Uint8Array(Math.min(limit, 65536)), count = 0;
  const abort = () => { reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', abort, { once: true });
  try {
    while (true) {
      if (signal.aborted) throw new Error('Asset load cancelled');
      const { done, value } = await reader.read();
      if (signal.aborted) throw new Error('Asset load cancelled');
      if (done) break;
      count += value.byteLength;
      if (count > limit) throw new Error('Asset exceeds viewer budget');
      if (count > bytes.length) {
        const expanded = new Uint8Array(Math.min(limit, Math.max(count, bytes.length * 2)));
        expanded.set(bytes); bytes = expanded;
      }
      bytes.set(value, count-value.byteLength);
    }
    return bytes.subarray(0, count);
  } catch (error) {
    try { await reader.cancel(); } catch {}
    throw error;
  } finally {
    signal.removeEventListener('abort', abort);
    reader.releaseLock();
  }
}

/** Estimated RGBA texels plus mipmaps, not measured total browser/GPU memory. */
export class TextureBudget {
  constructor(limit = 512 * 1024 * 1024) {
    if (!Number.isSafeInteger(limit) || limit <= 0) throw new Error('Invalid scene texture budget');
    this.limit = limit; this.used = 0;
  }
  reserve(width, height) {
    const bytes = Math.ceil(width * height * 4 * 4 / 3);
    if (!Number.isSafeInteger(bytes) || bytes <= 0 || bytes > this.limit - this.used) {
      throw new Error('Scene texture budget reached');
    }
    this.used += bytes;
    let released = false;
    return () => { if (!released) { released = true; this.used -= bytes; } };
  }
}

/** Load only immutable bundled PNGs; validate dimensions before native decoding. */
export async function loadBundledTexture(THREE, assetBase, path, signal, colorSpace = 'srgb', budget = null) {
  if (!['srgb', 'linear'].includes(colorSpace)) throw new Error('Unsupported texture color space');
  if (!/^assets\/[a-zA-Z0-9_.-]+\.png$/i.test(path)) throw new Error('Unbundled texture path');
  const response = await fetch(assetBase + path, { signal });
  if (!response.ok) throw new Error('Texture unavailable');
  const bytes = await readAssetBytes(response, 16 * 1024 * 1024, signal);
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (bytes.length < 33 || signature.some((value, index) => bytes[index] !== value)) throw new Error('Invalid PNG header');
  const header = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (header.getUint32(8) !== 13 || header.getUint32(12) !== 0x49484452) throw new Error('Invalid PNG header');
  const width = header.getUint32(16), height = header.getUint32(20);
  if (!width || !height || width > 4096 || height > 4096) throw new Error('Texture exceeds decoded image budget');
  if (signal.aborted) throw new Error('Texture cancelled');
  const release = budget?.reserve(width, height) || (() => {});
  let bitmap, released = false;
  const cleanup = () => {
    if (released) return;
    released = true; bitmap?.close(); release();
  };
  try {
    bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }), {
      imageOrientation: 'flipY', premultiplyAlpha: 'none', colorSpaceConversion: 'none',
    });
    if (signal.aborted || bitmap.width !== width || bitmap.height !== height) {
      throw new Error('Texture cancelled or decoded image dimensions differ from PNG header');
    }
    const texture = new THREE.Texture(bitmap);
    texture.colorSpace = colorSpace === 'srgb' ? THREE.SRGBColorSpace : THREE.NoColorSpace; texture.needsUpdate = true;
    texture.addEventListener('dispose', cleanup);
    return texture;
  } catch (error) { cleanup(); throw error; }
}
