/** Load only immutable bundled PNGs; keep decoding and GPU allocations bounded. */
export async function loadBundledTexture(THREE, assetBase, path, signal, colorSpace = 'srgb') {
  if (!['srgb', 'linear'].includes(colorSpace)) throw new Error('Unsupported texture color space');
  if (!/^assets\/[a-zA-Z0-9_.-]+\.png$/i.test(path)) throw new Error('Unbundled texture path');
  const response = await fetch(assetBase + path, { signal });
  if (!response.ok) throw new Error('Texture unavailable');
  const limit = 16 * 1024 * 1024;
  if (Number(response.headers.get('content-length')) > limit) throw new Error('Texture exceeds viewer budget');
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > limit) throw new Error('Texture exceeds viewer budget');
  const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }), {
    imageOrientation: 'flipY', premultiplyAlpha: 'none', colorSpaceConversion: 'none',
  });
  if (signal.aborted || bitmap.width * bitmap.height > 4096 * 4096) {
    bitmap.close(); throw new Error('Texture cancelled or exceeds decoded image budget');
  }
  const texture = new THREE.Texture(bitmap);
  texture.colorSpace = colorSpace === 'srgb' ? THREE.SRGBColorSpace : THREE.NoColorSpace; texture.needsUpdate = true;
  texture.addEventListener('dispose', () => bitmap.close());
  return texture;
}
