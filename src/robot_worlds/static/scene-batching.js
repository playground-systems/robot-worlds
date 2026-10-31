import { hasSharedResources, markSharedResources } from './scene-resources.js';

/** Batch loaded, scene-owned source meshes without changing source/policy data.
 * Spatial cells retain useful culling for close views. Blended, hidden, reflected,
 * skinned and morphing meshes retain their original individual rendering.
 */
export function batchSourceMeshes(THREE, root, { cellSize = 8, maxInstances = 10000 } = {}) {
  if (!Number.isFinite(cellSize) || cellSize <= 0 || !Number.isInteger(maxInstances) || maxInstances < 2 || maxInstances > 10000) {
    throw new Error('Invalid source batching limits');
  }
  root.updateWorldMatrix(true, true);
  const inverse = root.matrixWorld.clone().invert(), buckets = new Map();
  let before = 0;
  root.traverse(node => {
    if (!node.isMesh) return;
    before++;
    if (!hasSharedResources(node) || node.isInstancedMesh || node.isSkinnedMesh || !node.geometry || Array.isArray(node.material)
        || !node.material || node.material.transparent || !node.userData.object
        || Object.values(node.geometry.morphAttributes).some(value => value.length)) return;
    for (let parent = node; parent !== root; parent = parent.parent) {
      if (!parent.visible || (parent !== node && parent.renderOrder !== 0)) return;
    }
    const matrix = new THREE.Matrix4().multiplyMatrices(inverse, node.matrixWorld);
    if (!matrix.elements.every(Number.isFinite) || matrix.determinant() <= 0) return;
    const cell = [12,13,14].map(index => Math.floor(matrix.elements[index]/cellSize));
    const key = [node.geometry.uuid, node.material.uuid, node.castShadow, node.receiveShadow,
      node.layers.mask, node.renderOrder, node.frustumCulled, ...cell].join(':');
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push({ node, matrix });
  });
  // Build completely before removing any original node. Geometry/materials remain
  // owned by SceneAssets; only the instance buffers belong to the new batches.
  const prepared = [];
  let instances = 0;
  try {
    for (const entries of buckets.values()) {
      if (entries.length < 2 || entries.length > maxInstances) continue;
      const first = entries[0].node;
      const batch = markSharedResources(new THREE.InstancedMesh(first.geometry, first.material, entries.length));
      prepared.push({ batch, entries });
      batch.name = 'Repeated source instances';
      batch.castShadow = first.castShadow; batch.receiveShadow = first.receiveShadow;
      batch.layers.mask = first.layers.mask; batch.renderOrder = first.renderOrder;
      batch.frustumCulled = first.frustumCulled;
      batch.userData.objects = entries.map(({ node }) => node.userData.object);
      batch.userData.appearances = entries.map(({ node }) => node.userData.appearance);
      batch.userData.appearance = first.userData.appearance;
      entries.forEach(({ matrix }, index) => batch.setMatrixAt(index, matrix));
      batch.instanceMatrix.needsUpdate = true;
      batch.computeBoundingBox(); batch.computeBoundingSphere();
      instances += entries.length;
    }
  } catch (error) {
    for (const { batch } of prepared) batch.dispose();
    throw error;
  }
  for (const { batch, entries } of prepared) {
    for (const { node } of entries) node.removeFromParent();
    root.add(batch);
  }
  const emptyGroups = [];
  root.traverse(node => { if (node !== root && node.isGroup) emptyGroups.push(node); });
  for (const group of emptyGroups.reverse()) if (group.children.length === 0) group.removeFromParent();
  root.updateWorldMatrix(true, true);
  const objects = [];
  root.traverse(node => { if (node.isMesh) objects.push(node); });
  return { objects, stats: { meshesBefore: before, meshesAfter: objects.length,
    batches: prepared.length, batchedInstances: instances, cellSize } };
}
