/** Source material maps all use the mesh's prepared UV set. Physics is unchanged. */
export const SOURCE_MAPS = Object.freeze([
  ['texture_path', 'map', 'srgb'],
  ['normal_texture_path', 'normalMap', 'linear'],
  ['orm_texture_path', 'orm', 'linear'],
  ['emissive_texture_path', 'emissiveMap', 'srgb'],
]);

export function sourceMaterialOptions(object, maps = {}) {
  const surface = object.surface;
  if (!surface) return null;
  return {
    map: maps.map || null, normalMap: maps.normalMap || null,
    aoMap: maps.orm || null, roughnessMap: maps.orm || null, metalnessMap: maps.orm || null,
    emissiveMap: maps.emissiveMap || null,
    metalness: surface.metallic, roughness: surface.roughness,
    opacity: object.opacity ?? 1, transparent: surface.alpha_mode === 'BLEND' || (object.opacity ?? 1) < 1,
    alphaTest: surface.alpha_mode === 'MASK' ? surface.alpha_cutoff : 0,
  };
}

export async function loadSourceMaps(object, load) {
  const maps = {};
  try {
    // At most four maps per object; keep the viewer's existing four-object limit.
    for (const [field, role, space] of SOURCE_MAPS) {
      if (object[field]) maps[role] = await load(object[field], space);
    }
    return maps;
  } catch (error) {
    for (const texture of new Set(Object.values(maps))) texture.dispose();
    throw error;
  }
}
