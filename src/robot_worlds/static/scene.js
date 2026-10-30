import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { OBJLoader } from './vendor/OBJLoader.js';
import { disposeMaterials, disposeTree } from './scene-resources.js';

/** Layout inspection only. The native PNG is the physical robot representation. */
export class WorldViewer {
  constructor(canvas, labels, onSelect) {
    this.canvas = canvas;
    this.labels = labels;
    this.onSelect = onSelect;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#141820');
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.02, 500);
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.2;
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 0.3;
    this.controls.target.set(0, 0, 0.5);
    this.scene.add(new THREE.HemisphereLight(0xdce4ff, 0x4a5268, 2.3));
    const sun = new THREE.DirectionalLight(0xffffff, 3.2);
    sun.position.set(5, -3, 9);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = sun.shadow.camera.bottom = -20;
    sun.shadow.camera.right = sun.shadow.camera.top = 20;
    sun.shadow.camera.far = 50;
    sun.shadow.normalBias = 0.025;
    this.scene.add(sun); this.sun = sun;
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.objects = [];
    this.annotations = [];
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.down = null;
    canvas.addEventListener('pointerdown', e => { this.down = [e.clientX, e.clientY]; });
    canvas.addEventListener('pointerup', e => {
      if (!this.down || Math.hypot(e.clientX - this.down[0], e.clientY - this.down[1]) > 5) return;
      const rect = canvas.getBoundingClientRect();
      this.pointer.set((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
      this.raycaster.setFromCamera(this.pointer, this.camera);
      const hit = this.raycaster.intersectObjects(this.objects)[0];
      if (hit) this.onSelect(hit.object.userData.objects?.[hit.instanceId] || hit.object.userData.object);
    });
    this.animate = () => {
      this.animation = null;
      if (canvas.hidden || document.hidden) return;
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
    this.controls.addEventListener('change', () => this.invalidate());
    this.resizeObserver = new ResizeObserver(() => this.invalidate());
    this.resizeObserver.observe(canvas.parentElement);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) this.invalidate(); });
    this.invalidate();
  }

  invalidate() {
    if (this.animation == null) this.animation = requestAnimationFrame(this.animate);
  }

  resize() {
    const width = this.canvas.parentElement.clientWidth, height = this.canvas.parentElement.clientHeight;
    if (!width || !height || (this.width === width && this.height === height)) return;
    this.width = width; this.height = height;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  setSpec(spec, assetBase = null) {
    this.loadGeneration = (this.loadGeneration || 0) + 1;
    const generation = this.loadGeneration;
    this.spec = spec; this.reference = false; this.camera.layers.set(0); this.camera.fov = 42; this.camera.updateProjectionMatrix();
    this.scene.children.filter(n => n.isLight).forEach(n => { n.visible = true; });
    this.scene.environment = null; this.environmentTarget?.dispose(); this.environmentTarget = null;
    disposeTree(this.group);
    this.group.clear(); this.objects = []; this.annotations = []; this.labels.replaceChildren();
    const [w, d] = spec.bounds || [6, 6, 3];
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshStandardMaterial({ color: 0x343b48, roughness: 0.95 }));
    floor.receiveShadow = true; floor.position.z = -0.012; this.group.add(floor);
    const grid = new THREE.GridHelper(Math.max(w, d), Math.max(2, Math.min(200, Math.ceil(Math.max(w, d) * 2))), 0x66738d, 0x4b566d);
    grid.rotation.x = Math.PI / 2; grid.position.z = 0.001; grid.material.transparent = true; grid.material.opacity = 0.35; this.group.add(grid);
    const buckets = new Map();
    for (const object of spec.objects || []) {
      const kind = object.kind || 'box';
      if (kind === 'mesh' && assetBase && /\.obj$/i.test(object.mesh_path || '')) { this.loadOBJ(object, assetBase, generation); continue; }
      if (!buckets.has(kind)) buckets.set(kind, []);
      buckets.get(kind).push(object);
    }
    const matrix = new THREE.Matrix4(), rotation = new THREE.Quaternion(), position = new THREE.Vector3(), scale = new THREE.Vector3();
    for (const [kind, objects] of buckets) {
      let geometry;
      if (kind === 'sphere') geometry = new THREE.SphereGeometry(.5, 20, 12);
      else if (kind === 'cylinder') { geometry = new THREE.CylinderGeometry(.5, .5, 1, 24); geometry.rotateX(Math.PI / 2); }
      else geometry = new THREE.BoxGeometry(1, 1, 1);
      const material = new THREE.MeshStandardMaterial({ roughness: .65, metalness: .08, transparent: kind === 'mesh', opacity: kind === 'mesh' ? .48 : 1 });
      const batch = new THREE.InstancedMesh(geometry, material, objects.length);
      batch.castShadow = true; batch.receiveShadow = true; batch.userData.objects = objects;
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
    this.sun.position.set(extent * .4, -extent * .3, extent * .8);
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
    this.fit();
  }

  async loadOBJ(object, assetBase, generation) {
    try {
      if (!/^assets\/[a-zA-Z0-9_.-]+$/.test(object.mesh_path)) throw new Error('Unbundled mesh path');
      const response = await fetch(assetBase + object.mesh_path);
      if (!response.ok) throw new Error('Mesh unavailable');
      const source = await response.text();
      if (source.length > 24 * 1024 * 1024) throw new Error('Mesh exceeds viewer budget');
      if (this.loadGeneration !== generation) return;
      const group = new OBJLoader().parse(source), box = new THREE.Box3().setFromObject(group), size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
      let appearance = {};
      const metadata = source.match(/^# robot_worlds_material (.{1,2048})$/m);
      if (metadata) { try { appearance = JSON.parse(metadata[1]); } catch {} }
      const bounded = (key, fallback) => Number.isFinite(appearance[key]) ? Math.max(0, Math.min(1, appearance[key])) : fallback;
      const material = new THREE.MeshStandardMaterial({ color: new THREE.Color().setRGB(...object.color), metalness: bounded('metalness', .05), roughness: bounded('roughness', .65), transparent: bounded('opacity', 1) < 1, opacity: bounded('opacity', 1) });
      if (Array.isArray(appearance.emissive) && appearance.emissive.length === 3 && appearance.emissive.every(Number.isFinite)) material.emissive.setRGB(...appearance.emissive.map(x => Math.max(0, Math.min(10, x))));
      group.traverse(node => {
        if (!node.isMesh) return;
        node.geometry.translate(-center.x, -center.y, -center.z);
        node.geometry.scale(...object.size.map((extent, axis) => extent / Math.max(size.getComponent(axis), 1e-9)));
        disposeMaterials(node.material); node.material = material; node.castShadow = true; node.receiveShadow = true; node.userData.object = object; this.objects.push(node);
      });
      group.position.set(...object.position); this.group.add(group);
    } catch (error) {
      if (this.loadGeneration !== generation) return;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(...object.size), new THREE.MeshBasicMaterial({ color: 0xcc8c67, wireframe: true }));
      mesh.position.set(...object.position); mesh.userData.object = object; this.group.add(mesh); this.objects.push(mesh);
      this.label(`${object.label || object.id}: mesh unavailable`, new THREE.Vector3(...object.position));
    } finally {
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

  fit() {
    this.invalidate();
    if (this.reference && this.referenceShot) { this.camera.position.set(...this.referenceShot.position); this.controls.target.set(...this.referenceShot.target); this.controls.update(); return; }
    const bounds = this.spec?.bounds || [6, 6, 3], extent = Math.max(bounds[0], bounds[1], 2);
    this.camera.position.set(extent * 0.8, -extent * 0.9, extent * 0.85);
    this.controls.target.set(0, 0, Math.min(bounds[2] * 0.15, 0.8));
    this.controls.maxDistance = extent * 5; this.controls.update(); this.resize();
  }
}
