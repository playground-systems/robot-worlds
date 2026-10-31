/** Release shared scene resources once, including textures owned by materials. */
const sharedResources = Symbol('scene-owned resources');

export function markSharedResources(root) {
  root.traverse(node => { if (node.isMesh) node[sharedResources] = true; });
  return root;
}

/** One owner per scene, including resources that finish loading after disposal. */
export class SceneResourceCache {
  constructor() { this.entries = new Map(); this.closed = false; }

  get(key, load, release) {
    if (this.closed) return Promise.reject(new Error('Scene resources disposed'));
    if (this.entries.has(key)) return this.entries.get(key).promise;
    const entry = { release, ready: false };
    entry.promise = Promise.resolve().then(load).then(value => {
      if (this.closed) { release(value); throw new Error('Scene resources disposed'); }
      entry.value = value; entry.ready = true; return value;
    }).catch(error => {
      if (this.entries.get(key) === entry) this.entries.delete(key);
      throw error;
    });
    this.entries.set(key, entry);
    return entry.promise;
  }

  dispose() {
    if (this.closed) return;
    this.closed = true;
    for (const entry of this.entries.values()) if (entry.ready) entry.release(entry.value);
    this.entries.clear();
  }
}

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
    if (!node[sharedResources] && node.geometry && !seen.has(node.geometry)) {
      seen.add(node.geometry);
      node.geometry.dispose();
    }
    if (!node[sharedResources]) disposeMaterials(node.material, seen);
    node.shadow?.dispose();
  });
}
