import * as THREE from 'three';
import { OBJLoader } from './vendor/OBJLoader.js';
import { disposeMaterials, disposeTree, markSharedResources, SceneResourceCache } from './scene-resources.js';
import { estimatedAppearance } from './scene-appearance.js';
import { SOURCE_MAPS, sourceMaterialOptions } from './scene-materials.js';
import { fetchAssetBytes, loadBundledTexture, TextureBudget } from './scene-textures.js';

/** Immutable asset resources shared only for the lifetime of one displayed world. */
export class SceneAssets {
  constructor({ maxTextureBytes, assetTimeoutMs = 30000 } = {}) {
    if (!Number.isSafeInteger(assetTimeoutMs) || assetTimeoutMs <= 0 || assetTimeoutMs > 120000) throw new Error('Invalid asset request timeout');
    this.assetTimeoutMs = assetTimeoutMs;
    this.cache = new SceneResourceCache({ cacheFailures: true });
    this.textureBudget = new TextureBudget(maxTextureBytes);
    this.controller = new AbortController();
  }

  mesh(base, path) {
    return this.cache.get(JSON.stringify(['mesh', base, path]), async () => {
      if (!/^assets\/[a-zA-Z0-9_.-]+\.obj$/i.test(path)) throw new Error('Unbundled mesh path');
      const bytes = await fetchAssetBytes(base + path, 24 * 1024 * 1024, this.controller.signal,
        { timeoutMs: this.assetTimeoutMs, unavailable: 'Mesh unavailable' });
      const source = new TextDecoder().decode(bytes);
      if (this.controller.signal.aborted) throw new Error('Scene resources disposed');
      const group = new OBJLoader().parse(source);
      try {
        const box = new THREE.Box3().setFromObject(group);
        const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
        const geometries = new Set(), materials = new Set();
        let meshCount = 0;
        group.traverse(node => {
          if (node.isLine || node.isPoints) throw new Error('OBJ must contain triangle meshes only');
          if (!node.isMesh) return;
          meshCount++;
          if (!geometries.has(node.geometry)) {
            geometries.add(node.geometry);
            node.geometry.translate(-center.x, -center.y, -center.z);
            node.geometry.scale(...size.toArray().map(value => 1 / Math.max(value, 1e-9)));
          }
          disposeMaterials(node.material, materials); node.material = [];
        });
        if (!meshCount) throw new Error('OBJ contains no usable meshes');
        let appearance = {};
        const metadata = source.match(/^# robot_worlds_material (.{1,2048})$/m);
        if (metadata) { try { appearance = JSON.parse(metadata[1]); } catch {} }
        return { group, appearance, metadata: !!metadata };
      } catch (error) { disposeTree(group); throw error; }
    }, asset => disposeTree(asset.group));
  }

  texture(base, path, space, surface) {
    // Sampling/color-space differences require distinct GPU textures.
    const wrapS = surface?.wrap_s || 'clamp', wrapT = surface?.wrap_t || 'clamp';
    return this.cache.get(JSON.stringify(['texture', base, path, space, wrapS, wrapT]), async () => {
      const texture = await loadBundledTexture(THREE, base, path, this.controller.signal, space, this.textureBudget, this.assetTimeoutMs);
      const wraps = { repeat: THREE.RepeatWrapping, clamp: THREE.ClampToEdgeWrapping, mirror: THREE.MirroredRepeatWrapping };
      texture.wrapS = wraps[wrapS]; texture.wrapT = wraps[wrapT]; texture.needsUpdate = true;
      return texture;
    }, texture => texture.dispose());
  }

  async load(object, base) {
    const asset = await this.mesh(base, object.mesh_path);
    const estimated = estimatedAppearance(object), appearance = asset.appearance;
    const bounded = (key, fallback) => Number.isFinite(appearance[key]) ? Math.max(0, Math.min(1, appearance[key])) : fallback;
    const opacity = object.opacity ?? bounded('opacity', 1);
    const options = { color: object.color || [.5, .6, .7], opacity,
      metalness: bounded('metalness', estimated.metalness), roughness: bounded('roughness', estimated.roughness) };
    const paths = SOURCE_MAPS.map(([field]) => object[field] || null);
    const material = await this.cache.get(JSON.stringify(['material', base, paths, object.surface || null, options, appearance.emissive]), async () => {
      const maps = {};
      // Cache owns maps even if another map fails; another object may use them.
      if (object.surface) {
        for (const [field, role, space] of SOURCE_MAPS) {
          if (object[field]) maps[role] = await this.texture(base, object[field], space, object.surface);
        }
      } else if (object.texture_path) maps.map = await this.texture(base, object.texture_path, 'srgb');
      const value = new THREE.MeshStandardMaterial({ map: maps.map || null,
        color: new THREE.Color().setRGB(...options.color), envMapIntensity: .65,
        metalness: options.metalness, roughness: options.roughness, transparent: opacity < 1, opacity });
      if (object.surface) {
        Object.assign(value, sourceMaterialOptions(object, maps));
        value.normalScale.set(...object.surface.normal_scale);
        value.emissive.setRGB(...object.surface.emissive);
        value.side = object.surface.double_sided ? THREE.DoubleSide : THREE.FrontSide;
      } else if (Array.isArray(appearance.emissive) && appearance.emissive.length === 3 && appearance.emissive.every(Number.isFinite)) {
        value.emissive.setRGB(...appearance.emissive.map(x => Math.max(0, Math.min(10, x))));
      }
      return value;
    }, material => material.dispose());
    if (this.controller.signal.aborted) throw new Error('Scene resources disposed');
    const group = asset.group.clone(true);
    group.traverse(node => {
      if (!node.isMesh) return;
      node.material = material; node.castShadow = true; node.receiveShadow = true;
      node.userData.object = object;
      node.userData.appearance = { ...estimated,
        ...(object.surface ? { estimated: false, metalness: object.surface.metallic, roughness: object.surface.roughness } : {}),
        basis: object.surface ? 'Source PBR material; classic policy/replay camera rendering differs'
          : object.texture_path ? 'Original bundled texture; other material properties are estimates'
          : asset.metadata ? 'Bundled OBJ appearance metadata with estimated defaults' : estimated.basis };
    });
    // Instance transforms never mutate the shared, normalized source geometry.
    const turns = object.mesh_quarter_turns || 0;
    group.scale.set(...(turns % 2 ? [object.size[1], object.size[0], object.size[2]] : object.size));
    group.position.set(...object.position);
    if (object.quaternion) group.quaternion.set(object.quaternion[1], object.quaternion[2], object.quaternion[3], object.quaternion[0]);
    else if (object.yaw) group.rotation.z = object.yaw;
    if (turns) group.quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), turns*Math.PI/2));
    return markSharedResources(group);
  }

  retryFailures() { this.cache.clearFailures(); }

  placeholder(object) {
    if (this.controller.signal.aborted) throw new Error('Scene resources disposed');
    this.placeholderGeometry ||= new THREE.BoxGeometry(1, 1, 1);
    this.placeholderMaterial ||= new THREE.MeshBasicMaterial({ color: 0xcc8c67, wireframe: true });
    const mesh = new THREE.Mesh(this.placeholderGeometry, this.placeholderMaterial);
    mesh.position.set(...object.position); mesh.scale.set(...object.size);
    // size already includes the source quarter-turn; only the body pose rotates this bound.
    if (object.quaternion) mesh.quaternion.set(object.quaternion[1], object.quaternion[2], object.quaternion[3], object.quaternion[0]);
    else if (object.yaw) mesh.rotation.z = object.yaw;
    mesh.userData.object = object;
    mesh.userData.appearance = { estimated: true, basis: 'Source unavailable; wireframe bounds only' };
    return markSharedResources(mesh);
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true; this.controller.abort(); this.cache.dispose();
    this.placeholderGeometry?.dispose(); this.placeholderMaterial?.dispose();
  }
}
