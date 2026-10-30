/** Release shared scene resources once, including textures owned by materials. */
export function disposeMaterials(materials, seen = new Set()) {
  for (const material of Array.isArray(materials) ? materials : [materials]) {
    if (!material || seen.has(material)) continue;
    seen.add(material);
    for (const value of Object.values(material)) {
      if (value?.isTexture && !seen.has(value)) {
        seen.add(value);
        value.dispose();
      }
    }
    material.dispose();
  }
}

export function disposeTree(root) {
  const seen = new Set();
  root.traverse(node => {
    // Instance matrix/color buffers belong to the mesh, not its geometry.
    if (node.isInstancedMesh) node.dispose();
    if (node.geometry && !seen.has(node.geometry)) {
      seen.add(node.geometry);
      node.geometry.dispose();
    }
    disposeMaterials(node.material, seen);
    node.shadow?.dispose();
  });
}
