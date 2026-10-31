import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { SceneAssets } from './scene-assets.js';
import { batchSourceMeshes } from './scene-batching.js';
import { disposeTree } from './scene-resources.js';
import { isInspectionShell, objectBounds, perspectiveFit, sceneBounds } from './scene-inspection.js';
import { APPEARANCE_NOTICE, estimatedAppearance, studioEnvironmentPixels } from './scene-appearance.js';
import { geometryKey, revolvedGeometry } from './scene-shapes.js';

/** Layout inspection only. The native PNG is the physical robot representation. */
export class WorldViewer {
  constructor(canvas, labels, onSelect) {
    this.canvas = canvas;
    this.labels = labels;
    this.onSelect = onSelect; this.active = true; this.onScreen = true; this.disposed = false; this.fitted = true;
    this.shellNodes = []; this.shellCount = 0; this.cutaway = false; this.loads = new Set(); this.pendingLoads = [];
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#d3dae5');
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.02, 500);
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 1.5));
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.autoUpdate = false;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = .95;
    this.appearanceNotice = APPEARANCE_NOTICE;
    const environment = studioEnvironmentPixels();
    this.layoutEnvironment = new THREE.DataTexture(environment.data, environment.width, environment.height, THREE.RGBAFormat);
    this.layoutEnvironment.mapping = THREE.EquirectangularReflectionMapping; this.layoutEnvironment.colorSpace = THREE.LinearSRGBColorSpace;
    this.layoutEnvironment.needsUpdate = true;
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = 1.25;
    this.controls.minDistance = 0.3;
    this.controls.target.set(0, 0, 0.5);
    this.scene.add(new THREE.HemisphereLight(0xe8eef7, 0x797b80, 1.2));
    const sun = new THREE.DirectionalLight(0xfff2df, 1.8);
    sun.position.set(5, -3, 9);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = sun.shadow.camera.bottom = -20;
    sun.shadow.camera.right = sun.shadow.camera.top = 20;
    sun.shadow.camera.far = 50;
    sun.shadow.normalBias = 0.003; sun.shadow.bias = -.0001;
    this.scene.add(sun); this.sun = sun;
    this.fill = new THREE.DirectionalLight(0xd5e6ff, .55); this.scene.add(this.fill);
    this.rim = new THREE.DirectionalLight(0xe5ecff, .35); this.scene.add(this.rim);
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.objects = [];
    this.annotations = [];
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.down = null;
    this.onPointerDown = e => {
      canvas.focus({ preventScroll: true });
      this.down = e.button === 0 ? [e.clientX, e.clientY] : null;
    };
    this.onPointerUp = e => {
      if (!this.down || Math.hypot(e.clientX - this.down[0], e.clientY - this.down[1]) > 5) return;
      const rect = canvas.getBoundingClientRect();
      this.pointer.set((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
      this.raycaster.setFromCamera(this.pointer, this.camera);
      const candidates = this.objects.filter(node => { for (let current = node; current; current = current.parent) if (!current.visible) return false; return true; });
      const hit = this.raycaster.intersectObjects(candidates)[0];
      const object = hit && (hit.object.userData.objects?.[hit.instanceId] || hit.object.userData.object);
      if (object) this.onSelect(object);
      this.down = null;
    };
    canvas.addEventListener('pointerdown', this.onPointerDown);
    canvas.addEventListener('pointerup', this.onPointerUp);
    this.onKeyDown = event => this.handleShortcut(event);
    canvas.addEventListener('keydown', this.onKeyDown);
    this.animate = () => {
      this.animation = null;
      if (!this.isVisible()) return;
      this.resize();
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      for (const a of this.annotations) {
        const point = a.position.clone().project(this.camera);
        a.node.style.display = point.z > 1 || point.z < -1 ? 'none' : '';
        a.node.style.left = `${(point.x + 1) * canvas.clientWidth / 2}px`;
        a.node.style.top = `${(1 - point.y) * canvas.clientHeight / 2}px`;
      }
    };
    this.onControlChange = () => this.invalidate();
    this.onControlStart = () => { this.fitted = false; };
    this.controls.addEventListener('change', this.onControlChange);
    this.controls.addEventListener('start', this.onControlStart);
    this.resizeObserver = new ResizeObserver(() => this.invalidate());
    this.resizeObserver.observe(canvas.parentElement);
    this.observeVisibility();
    this.invalidate();
  }

  isVisible() {
    return !this.disposed && this.active && this.onScreen && !this.canvas.hidden && !document.hidden;
  }

  observeVisibility() {
    // Wait for the first intersection before starting potentially large source loads.
    if (typeof IntersectionObserver !== 'undefined') {
      this.onScreen = false;
      this.intersectionObserver = new IntersectionObserver(entries => {
        const entry = entries.findLast(entry => entry.target === this.canvas);
        if (!entry) return;
        this.onScreen = entry.isIntersecting && entry.intersectionRatio > 0;
        this.updateVisibility();
      });
      this.intersectionObserver.observe(this.canvas);
    }
    this.onVisibility = () => this.updateVisibility();
    document.addEventListener('visibilitychange', this.onVisibility);
  }

  updateVisibility() {
    if (!this.isVisible()) {
      if (this.animation != null) cancelAnimationFrame(this.animation);
      this.animation = null;
      return;
    }
    this.pumpLoads(); this.invalidate();
  }

  handleShortcut(event) {
    if (!this.isVisible() || document.activeElement !== this.canvas || event.target !== this.canvas ||
        event.altKey || event.ctrlKey || event.metaKey || event.repeat) return;
    if (event.key === 'Home' || event.key.toLowerCase() === 'f') {
      event.preventDefault();
      this.fit(event.key === 'Home' ? null : this.selectedObject);
    }
  }

  invalidate() {
    if (this.isVisible() && this.animation == null) this.animation = requestAnimationFrame(this.animate);
  }

  resize() {
    const width = this.canvas.parentElement.clientWidth, height = this.canvas.parentElement.clientHeight;
    if (!width || !height || (this.width === width && this.height === height)) return;
    this.width = width; this.height = height;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    if (this.fitted && this.spec && !this.reference) this.applyFit(this.fitBounds || sceneBounds(this.spec), this.fitDirection);
  }

  setSpec(spec, assetBase = null) {
    this.abortLoads();
    this.assets = new SceneAssets();
    this.loadGeneration = (this.loadGeneration || 0) + 1;
    this.select(null); this.shellNodes = []; this.shellCount = 0; this.cutaway = false;
    const generation = this.loadGeneration;
    this.spec = spec; this.reference = false; this.controls.maxPolarAngle = 1.25; this.camera.layers.set(0); this.camera.fov = 30; this.camera.updateProjectionMatrix();
    this.scene.children.filter(n => n.isLight).forEach(n => { n.visible = true; });
    this.scene.background.set('#d3dae5');
    this.scene.environment = this.layoutEnvironment; this.environmentTarget?.dispose(); this.environmentTarget = null;
    this.renderer.toneMappingExposure = .95; this.renderer.shadowMap.needsUpdate = true;
    disposeTree(this.group);
    this.group.clear(); this.objects = []; this.annotations = []; this.labels.replaceChildren();
    this.sourceGroup = new THREE.Group(); this.group.add(this.sourceGroup);
    this.sourceBatched = false; this.sourceBatchStats = null;
    this.assetErrorLabels = new Map(); this.additionalAssetErrors = 0; this.assetErrorSummary = null;
    const [w, d] = spec.bounds || [6, 6, 3];
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshStandardMaterial({ color: 0xe6ebf2, roughness: .86, metalness: .02, envMapIntensity: .35 }));
    floor.receiveShadow = true; floor.position.z = -0.012; this.group.add(floor);
    const grid = new THREE.GridHelper(Math.max(w, d), Math.max(2, Math.min(200, Math.ceil(Math.max(w, d) * 2))), 0xaab6c8, 0xc3cad6);
    grid.scale.set(w/Math.max(w,d), 1, d/Math.max(w,d));
    grid.rotation.x = Math.PI / 2; grid.position.z = 0.001; grid.material.transparent = true; grid.material.opacity = .16; this.group.add(grid);
    const buckets = new Map();
    for (const object of spec.objects || []) {
      if (object.visible === false) continue;
      const kind = object.kind || 'box';
      const shell = isInspectionShell(object, spec.bounds); if (shell) this.shellCount++;
      if (kind === 'mesh' && assetBase && /\.obj$/i.test(object.mesh_path || '')) { this.pendingLoads.push([object, assetBase, generation]); continue; }
      const appearance = estimatedAppearance(object);
      const key = `${geometryKey(object)}:${shell ? 'shell' : 'solid'}:${appearance.id}:${object.opacity ?? 1}`;
      if (!buckets.has(key)) buckets.set(key, { kind, shell, appearance, objects: [] });
      buckets.get(key).objects.push(object);
    }
    const matrix = new THREE.Matrix4(), rotation = new THREE.Quaternion(), position = new THREE.Vector3(), scale = new THREE.Vector3();
    const geometries = new Map();
    for (const { kind, shell, appearance, objects } of buckets.values()) {
      const shapeKey = geometryKey(objects[0]);
      let geometry = geometries.get(shapeKey);
      if (!geometry) {
      if (kind === 'sphere') geometry = new THREE.SphereGeometry(.5, 20, 12);
      else if (kind === 'cylinder') { geometry = new THREE.CylinderGeometry(.5, .5, 1, 24); geometry.rotateX(Math.PI / 2); }
      else if (kind === 'revolved') {
        geometry = revolvedGeometry(THREE, objects[0].revolved);
      }
      else geometry = new THREE.BoxGeometry(1, 1, 1);
      geometries.set(shapeKey, geometry);
      }
      const opacity = kind === 'mesh' ? .48 : (objects[0].opacity ?? 1);
      const material = new THREE.MeshStandardMaterial({ roughness: appearance.roughness, metalness: appearance.metalness, envMapIntensity: .65, transparent: opacity < 1, opacity });
      const batch = new THREE.InstancedMesh(geometry, material, objects.length);
      batch.castShadow = true; batch.receiveShadow = true; batch.userData.objects = objects; batch.userData.appearance = appearance;
      if (shell) this.shellNodes.push(batch);
      objects.forEach((object, index) => {
        const size = object.size || [.2, .2, .2];
        position.set(...object.position); scale.set(...size); rotation.identity();
        if (object.quaternion) rotation.set(object.quaternion[1], object.quaternion[2], object.quaternion[3], object.quaternion[0]);
        else if (object.yaw) rotation.setFromAxisAngle(new THREE.Vector3(0, 0, 1), object.yaw);
        matrix.compose(position, rotation, scale); batch.setMatrixAt(index, matrix);
        batch.setColorAt(index, new THREE.Color().setRGB(...(object.color || [.5, .6, .7])));
        if ((spec.objects || []).length <= 45 && (object.movable || object.joint)) this.label(object.label || object.id, position.clone().add(new THREE.Vector3(0, 0, size[2] / 2 + .12)));
      });
      batch.instanceMatrix.needsUpdate = true;
      if (batch.instanceColor) batch.instanceColor.needsUpdate = true;
      batch.computeBoundingSphere(); this.group.add(batch); this.objects.push(batch);
    }
    const extent = Math.max(w, d);
    this.camera.far = Math.max(500, extent * 10); this.camera.updateProjectionMatrix();
    this.sun.position.set(extent * .4, -extent * .3, extent * .9);
    this.fill.position.set(-extent*.45, -extent*.2, extent*.65);
    this.rim.position.set(-extent*.3, extent*.5, extent*.5);
    Object.assign(this.sun.shadow.camera, { left: -extent * .6, right: extent * .6, top: extent * .6, bottom: -extent * .6, far: extent * 3 });
    this.sun.shadow.camera.updateProjectionMatrix();
    for (const region of spec.regions || []) {
      const [x, y, z] = region.size;
      const area = new THREE.Mesh(new THREE.BoxGeometry(x, y, Math.min(z, 0.018)), new THREE.MeshBasicMaterial({ color: 0x8795cf, transparent: true, opacity: 0.16, depthWrite: false }));
      area.position.set(region.position[0], region.position[1], 0.012); this.group.add(area);
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(area.geometry), new THREE.LineBasicMaterial({ color: 0xa5b4ed, transparent: true, opacity: 0.5 }));
      outline.position.copy(area.position); this.group.add(outline);
    }
    const start = spec.robot_start || [0, 0, 0];
    const marker = new THREE.Mesh(new THREE.RingGeometry(0.25, 0.29, 48), new THREE.MeshBasicMaterial({ color: 0xaab8ee, side: THREE.DoubleSide }));
    marker.position.set(start[0], start[1], 0.025); this.group.add(marker);
    const arrow = new THREE.ArrowHelper(new THREE.Vector3(Math.cos(start[2]), Math.sin(start[2]), 0), new THREE.Vector3(start[0], start[1], 0.03), 0.45, 0xaab8ee, 0.1, 0.08);
    this.group.add(arrow); this.label('Robot start', new THREE.Vector3(start[0], start[1], 0.17));
    this.setCutaway(false); this.fit(); this.pumpLoads();
  }

  pumpLoads() {
    while (this.isVisible() && this.loads.size < 4 && this.pendingLoads.length) this.loadOBJ(...this.pendingLoads.shift());
  }

  async loadOBJ(object, assetBase, generation) {
    const pending = {}; this.loads.add(pending);
    const assets = this.assets;
    try {
      const group = await assets.load(object, assetBase);
      if (this.disposed || this.loadGeneration !== generation) return;
      group.traverse(node => { if (node.isMesh) this.objects.push(node); });
      if (isInspectionShell(object, this.spec.bounds)) {
        // Keep cutaway shells independent so toggling visibility still works.
        this.shellNodes.push(group); group.visible = !this.cutaway;
        this.group.add(group);
      } else this.sourceGroup.add(group);
    } catch (error) {
      if (assets.controller.signal.aborted || this.disposed || this.loadGeneration !== generation) return;
      const mesh = assets.placeholder(object);
      this.objects.push(mesh);
      if (isInspectionShell(object, this.spec.bounds)) {
        this.shellNodes.push(mesh); mesh.visible = !this.cutaway; this.group.add(mesh);
      } else this.sourceGroup.add(mesh);
      this.showAssetError(object, error);
    } finally {
      this.loads.delete(pending); this.pumpLoads();
      if (!this.disposed && this.loadGeneration === generation && !this.loads.size && !this.pendingLoads.length) this.batchSources();
      if (!this.disposed) this.renderer.shadowMap.needsUpdate = true;
      if (this.loadGeneration === generation) this.invalidate();
    }
  }

  showAssetError(object, error) {
    const message = ['Scene texture budget reached', 'Source load timed out'].includes(error.message) ? error.message : 'mesh unavailable';
    const key = JSON.stringify([object.mesh_path, object.texture_path, object.normal_texture_path,
      object.orm_texture_path, object.emissive_texture_path, message]);
    const existing = this.assetErrorLabels.get(key);
    if (existing) {
      existing.count++; existing.node.textContent = `${existing.text} (${existing.count} copies)`; return;
    }
    if (this.assetErrorLabels.size < 16) {
      const text = `${object.label || object.id}: ${message}`;
      const node = this.label(text, new THREE.Vector3(...object.position));
      this.assetErrorLabels.set(key, { node, text, count: 1 });
    } else {
      this.additionalAssetErrors++;
      if (!this.assetErrorSummary) this.assetErrorSummary = this.label('', new THREE.Vector3(...object.position));
      this.assetErrorSummary.textContent = `${this.additionalAssetErrors} more unavailable meshes`;
    }
  }

  batchSources() {
    if (this.sourceBatched || !this.sourceGroup) return;
    this.sourceBatched = true;
    try {
      const previous = new Set();
      this.sourceGroup.traverse(node => { if (node.isMesh) previous.add(node); });
      const result = batchSourceMeshes(THREE, this.sourceGroup);
      this.objects = this.objects.filter(node => !previous.has(node)).concat(result.objects);
      this.sourceBatchStats = result.stats;
    } catch {
      // Batching is optional; its transaction leaves the original meshes intact.
      this.sourceBatchStats = { error: 'Batching unavailable; retained individual source meshes' };
    }
  }

  async setReference(data, manifest, environment = null) {
    this.setSpec({ bounds: manifest.bounds_z_up?.size || [10, 8, 6], objects: [], regions: [] });
    const generation = this.loadGeneration;
    // Reference JSON is data only; disallow network image URLs and shader code.
    for (const image of data.images || []) if (typeof image.url !== 'string' || !image.url.startsWith('data:image/png;base64,') || image.url.length > 16 * 1024 * 1024) throw new Error('Reference textures must be bounded embedded PNG images');
    for (const material of data.materials || []) if (!['MeshBasicMaterial', 'MeshStandardMaterial', 'MeshPhysicalMaterial'].includes(material.type)) throw new Error('Unsupported reference material');
    const group = await new THREE.ObjectLoader().parseAsync(data);
    if (generation !== this.loadGeneration) { disposeTree(group); return; }
    disposeTree(this.group);
    this.group.clear(); this.labels.replaceChildren(); this.annotations = []; this.objects = [];
    group.traverse(node => { if (node.isMesh) { node.layers.enableAll(); node.castShadow = true; node.receiveShadow = true; } });
    this.group.add(group); this.reference = true; this.controls.maxPolarAngle = Math.PI * .49; this.camera.layers.enableAll();
    this.scene.background.set('#1e1e1e');
    this.scene.environment = null; this.renderer.toneMappingExposure = 1.2; this.renderer.shadowMap.needsUpdate = true;
    this.scene.children.filter(n => n.isLight).forEach(n => { n.visible = false; });
    if (environment) {
      for (const image of environment.images || []) if (typeof image.url !== 'string' || !image.url.startsWith('data:image/png;base64,')) throw new Error('Embedded environment textures required');
      for (const material of environment.materials || []) if (!['MeshBasicMaterial', 'MeshStandardMaterial', 'MeshPhysicalMaterial'].includes(material.type)) throw new Error('Unsupported environment material');
      const environmentScene = await new THREE.ObjectLoader().parseAsync(environment);
      if (generation !== this.loadGeneration) { disposeTree(environmentScene); return; }
      const pmrem = new THREE.PMREMGenerator(this.renderer);
      try {
        this.environmentTarget = pmrem.fromScene(environmentScene, .02);
        this.scene.environment = this.environmentTarget.texture;
      } finally {
        pmrem.dispose(); disposeTree(environmentScene);
      }
    }
    const shot = manifest.camera_shots.line;
    this.camera.position.set(...shot.position); this.camera.fov = shot.fov; this.camera.updateProjectionMatrix();
    this.controls.target.set(...shot.target); this.controls.update(); this.resize();
    this.referenceShot = shot;
    this.invalidate();
  }

  label(text, position) {
    const node = document.createElement('div'); node.className = 'scene-label'; node.textContent = text;
    this.labels.append(node); this.annotations.push({ node, position }); return node;
  }

  applyFit(bounds, direction = [.8, -.9, .85]) {
    const view = perspectiveFit(bounds, this.camera.aspect, this.camera.fov, direction);
    this.camera.position.set(...view.position); this.controls.target.set(...view.target);
    this.camera.near = view.near; this.camera.far = view.far; this.camera.updateProjectionMatrix();
    this.controls.maxDistance = view.maxDistance; this.controls.minDistance = Math.max(.03, view.near*2);
    this.controls.update(); this.fitBounds = bounds; this.fitDirection = direction; this.fitted = true;
  }

  fit(object = null, direction = [.8, -.9, .85]) {
    this.resize();
    if (this.reference && this.referenceShot) {
      this.camera.position.set(...this.referenceShot.position); this.controls.target.set(...this.referenceShot.target);
      this.camera.fov = this.referenceShot.fov; this.camera.updateProjectionMatrix(); this.controls.update(); this.invalidate(); return;
    }
    this.applyFit(object ? objectBounds(object) : sceneBounds(this.spec || {}), direction); this.invalidate();
  }

  select(object) {
    this.selectedObject = object;
    if (this.selectionOutline) { disposeTree(this.selectionOutline); this.scene.remove(this.selectionOutline); this.selectionOutline = null; }
    if (object) {
      const bounds = objectBounds(object);
      this.selectionOutline = new THREE.Box3Helper(new THREE.Box3(new THREE.Vector3(...bounds.min), new THREE.Vector3(...bounds.max)), 0x48aaff);
      this.selectionOutline.material.depthTest = false; this.selectionOutline.renderOrder = 10; this.scene.add(this.selectionOutline);
    }
    this.invalidate();
  }

  setCutaway(enabled) {
    this.cutaway = !!enabled && !this.reference;
    for (const node of this.shellNodes) node.visible = !this.cutaway;
    this.renderer.shadowMap.needsUpdate = true; this.invalidate(); return this.cutaway;
  }

  setActive(active) {
    this.active = active;
    this.updateVisibility();
  }

  abortLoads() { this.assets?.dispose(); this.assets = null; this.loads.clear(); this.pendingLoads = []; }

  dispose() {
    if (this.disposed) return;
    this.disposed = true; this.loadGeneration = (this.loadGeneration || 0)+1;
    this.setActive(false); this.abortLoads(); this.resizeObserver.disconnect(); this.intersectionObserver?.disconnect();
    this.canvas.removeEventListener('pointerdown', this.onPointerDown);
    this.canvas.removeEventListener('pointerup', this.onPointerUp);
    this.canvas.removeEventListener('keydown', this.onKeyDown);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.controls.removeEventListener('change', this.onControlChange);
    this.controls.removeEventListener('start', this.onControlStart); this.controls.dispose();
    disposeTree(this.scene); this.scene.clear(); this.environmentTarget?.dispose(); this.environmentTarget = null;
    this.labels.replaceChildren(); this.objects = []; this.annotations = []; this.shellNodes = [];
    this.sourceGroup = null; this.sourceBatchStats = null;
    this.assetErrorLabels?.clear(); this.assetErrorSummary = null;
    this.layoutEnvironment?.dispose(); this.layoutEnvironment = null; this.scene.environment = null;
    this.renderer.dispose(); this.renderer.forceContextLoss();
  }
}
