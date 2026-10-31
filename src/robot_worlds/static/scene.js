import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { OBJLoader } from './vendor/OBJLoader.js';
import { disposeMaterials, disposeTree } from './scene-resources.js';
import { isInspectionShell, objectBounds, perspectiveFit, sceneBounds } from './scene-inspection.js';
import { APPEARANCE_NOTICE, estimatedAppearance, studioEnvironmentPixels } from './scene-appearance.js';
import { geometryKey, revolvedGeometry } from './scene-shapes.js';

/** Layout inspection only. The native PNG is the physical robot representation. */
export class WorldViewer {
  constructor(canvas, labels, onSelect) {
    this.canvas = canvas;
    this.labels = labels;
    this.onSelect = onSelect; this.active = true; this.disposed = false; this.fitted = true;
    this.shellNodes = []; this.shellCount = 0; this.cutaway = false; this.loads = new Set(); this.pendingLoads = [];
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#1e1e1e');
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.02, 500);
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.autoUpdate = false;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
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
    this.controls.maxPolarAngle = Math.PI * 0.49;
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
    this.onPointerDown = e => { this.down = e.button === 0 ? [e.clientX, e.clientY] : null; };
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
    this.animate = () => {
      this.animation = null;
      if (this.disposed || !this.active || canvas.hidden || document.hidden) return;
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
    this.onVisibility = () => { if (!document.hidden) this.invalidate(); };
    document.addEventListener('visibilitychange', this.onVisibility);
    this.invalidate();
  }

  invalidate() {
    if (!this.disposed && this.active && this.animation == null) this.animation = requestAnimationFrame(this.animate);
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
    this.loadGeneration = (this.loadGeneration || 0) + 1;
    this.select(null); this.shellNodes = []; this.shellCount = 0; this.cutaway = false;
    const generation = this.loadGeneration;
    this.spec = spec; this.reference = false; this.camera.layers.set(0); this.camera.fov = 42; this.camera.updateProjectionMatrix();
    this.scene.children.filter(n => n.isLight).forEach(n => { n.visible = true; });
    this.scene.environment = this.layoutEnvironment; this.environmentTarget?.dispose(); this.environmentTarget = null;
    this.renderer.toneMappingExposure = .95; this.renderer.shadowMap.needsUpdate = true;
    disposeTree(this.group);
    this.group.clear(); this.objects = []; this.annotations = []; this.labels.replaceChildren();
    const [w, d] = spec.bounds || [6, 6, 3];
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshStandardMaterial({ color: 0x62676d, roughness: .86, metalness: .02, envMapIntensity: .35 }));
    floor.receiveShadow = true; floor.position.z = -0.012; this.group.add(floor);
    const grid = new THREE.GridHelper(Math.max(w, d), Math.max(2, Math.min(200, Math.ceil(Math.max(w, d) * 2))), 0x818992, 0x6a737e);
    grid.scale.set(w/Math.max(w,d), 1, d/Math.max(w,d));
    grid.rotation.x = Math.PI / 2; grid.position.z = 0.001; grid.material.transparent = true; grid.material.opacity = .16; this.group.add(grid);
    const buckets = new Map();
    for (const object of spec.objects || []) {
      const kind = object.kind || 'box';
      const shell = isInspectionShell(object, spec.bounds); if (shell) this.shellCount++;
      if (kind === 'mesh' && assetBase && /\.obj$/i.test(object.mesh_path || '')) { this.pendingLoads.push([object, assetBase, generation]); continue; }
      const appearance = estimatedAppearance(object);
      const key = `${geometryKey(object)}:${shell ? 'shell' : 'solid'}:${appearance.id}`;
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
      const material = new THREE.MeshStandardMaterial({ roughness: appearance.roughness, metalness: appearance.metalness, envMapIntensity: .65, transparent: kind === 'mesh', opacity: kind === 'mesh' ? .48 : 1 });
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
    while (!this.disposed && this.active && this.loads.size < 4 && this.pendingLoads.length) this.loadOBJ(...this.pendingLoads.shift());
  }

  async loadOBJ(object, assetBase, generation) {
    const controller = new AbortController(); this.loads.add(controller);
    try {
      if (!/^assets\/[a-zA-Z0-9_.-]+$/.test(object.mesh_path)) throw new Error('Unbundled mesh path');
      const response = await fetch(assetBase + object.mesh_path, { signal: controller.signal });
      if (!response.ok) throw new Error('Mesh unavailable');
      const source = await response.text();
      if (source.length > 24 * 1024 * 1024) throw new Error('Mesh exceeds viewer budget');
      if (this.disposed || this.loadGeneration !== generation) return;
      const group = new OBJLoader().parse(source), box = new THREE.Box3().setFromObject(group), size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
      let appearance = {};
      const metadata = source.match(/^# robot_worlds_material (.{1,2048})$/m);
      if (metadata) { try { appearance = JSON.parse(metadata[1]); } catch {} }
      const bounded = (key, fallback) => Number.isFinite(appearance[key]) ? Math.max(0, Math.min(1, appearance[key])) : fallback;
      const estimated = estimatedAppearance(object);
      const material = new THREE.MeshStandardMaterial({ color: new THREE.Color().setRGB(...object.color), envMapIntensity: .65, metalness: bounded('metalness', estimated.metalness), roughness: bounded('roughness', estimated.roughness), transparent: bounded('opacity', 1) < 1, opacity: bounded('opacity', 1) });
      if (Array.isArray(appearance.emissive) && appearance.emissive.length === 3 && appearance.emissive.every(Number.isFinite)) material.emissive.setRGB(...appearance.emissive.map(x => Math.max(0, Math.min(10, x))));
      group.traverse(node => {
        if (!node.isMesh) return;
        node.geometry.translate(-center.x, -center.y, -center.z);
        node.geometry.scale(...object.size.map((extent, axis) => extent / Math.max(size.getComponent(axis), 1e-9)));
        disposeMaterials(node.material); node.material = material; node.castShadow = true; node.receiveShadow = true; node.userData.object = object; node.userData.appearance = { ...estimated, basis: metadata ? 'Bundled OBJ appearance metadata with estimated defaults' : estimated.basis }; this.objects.push(node);
      });
      group.position.set(...object.position);
      if (object.quaternion) group.quaternion.set(object.quaternion[1], object.quaternion[2], object.quaternion[3], object.quaternion[0]);
      else if (object.yaw) group.rotation.z = object.yaw;
      this.group.add(group);
    } catch (error) {
      if (controller.signal.aborted || this.disposed || this.loadGeneration !== generation) return;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(...object.size), new THREE.MeshBasicMaterial({ color: 0xcc8c67, wireframe: true }));
      mesh.position.set(...object.position); mesh.userData.object = object; this.group.add(mesh); this.objects.push(mesh);
      this.label(`${object.label || object.id}: mesh unavailable`, new THREE.Vector3(...object.position));
    } finally {
      this.loads.delete(controller); this.pumpLoads();
      if (!this.disposed) this.renderer.shadowMap.needsUpdate = true;
      if (this.loadGeneration === generation) this.invalidate();
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
    this.group.add(group); this.reference = true; this.camera.layers.enableAll();
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
    this.labels.append(node); this.annotations.push({ node, position });
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
    if (!active && this.animation != null) { cancelAnimationFrame(this.animation); this.animation = null; }
    if (active) { this.pumpLoads(); this.invalidate(); }
  }

  abortLoads() { for (const controller of this.loads) controller.abort(); this.loads.clear(); this.pendingLoads = []; }

  dispose() {
    if (this.disposed) return;
    this.disposed = true; this.loadGeneration = (this.loadGeneration || 0)+1;
    this.setActive(false); this.abortLoads(); this.resizeObserver.disconnect();
    this.canvas.removeEventListener('pointerdown', this.onPointerDown);
    this.canvas.removeEventListener('pointerup', this.onPointerUp);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.controls.removeEventListener('change', this.onControlChange);
    this.controls.removeEventListener('start', this.onControlStart); this.controls.dispose();
    disposeTree(this.scene); this.scene.clear(); this.environmentTarget?.dispose(); this.environmentTarget = null;
    this.labels.replaceChildren(); this.objects = []; this.annotations = []; this.shellNodes = [];
    this.layoutEnvironment?.dispose(); this.layoutEnvironment = null; this.scene.environment = null;
    this.renderer.dispose(); this.renderer.forceContextLoss();
  }
}
