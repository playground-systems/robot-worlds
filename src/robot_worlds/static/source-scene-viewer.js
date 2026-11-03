import * as THREE from 'three';
import { fetchAssetBytes, TextureBudget } from './scene-textures.js';

const MAX_GEOMETRY = 96 * 1024 * 1024, MAX_IMAGE = 32 * 1024 * 1024;
const MAX_RECORDS = 100000;
const TYPES = { 0: 'plane', 2: 'sphere', 3: 'capsule', 4: 'ellipsoid', 5: 'cylinder', 6: 'box', 7: 'mesh' };

function finite(value, label) {
  if (!Number.isFinite(value)) throw new Error(`Invalid source ${label}`);
  return value;
}
function vector(value, length, label) {
  if (!Array.isArray(value) || value.length !== length) throw new Error(`Invalid source ${label}`);
  return value.map(v => finite(v, label));
}
function identifier(value) {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error('Invalid native source id');
  return value;
}
function records(value, label) {
  if (!Array.isArray(value) || value.length > MAX_RECORDS) throw new Error(`Invalid source ${label}`);
  return value;
}
function indexed(rows, label) {
  const result = new Map();
  for (const row of records(rows, label)) {
    identifier(row.id);
    if (result.has(row.id)) throw new Error(`Duplicate source ${label} id`);
    result.set(row.id, row);
  }
  return result;
}
function pathURL(base, path, pattern) {
  if (typeof path !== 'string' || !pattern.test(path)) throw new Error('Unsafe source asset path');
  if (typeof base !== 'string' || !base.endsWith('/') || /[?#\\%]/.test(base) || /(^|\/)\.\.?\//.test(base)) throw new Error('Invalid source asset base');
  const url = new URL(base, globalThis.location?.href || 'http://localhost/');
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Invalid source asset base');
  if (globalThis.location && url.origin !== globalThis.location.origin) throw new Error('Cross-origin source asset base');
  const target = new URL(path, url);
  if (target.origin !== url.origin || !target.pathname.startsWith(url.pathname)) throw new Error('Unsafe source asset path');
  return target.href;
}
function pose(node, position, quaternion) {
  node.position.fromArray(vector(position, 3, 'position'));
  const q = vector(quaternion, 4, 'quaternion');
  if (Math.abs(Math.hypot(...q) - 1) > 1e-5) throw new Error('Invalid source unit quaternion');
  node.quaternion.set(q[1], q[2], q[3], q[0]);
}
function dataAttribute(bytes, descriptor, size, label) {
  if (!descriptor || descriptor.item_size !== size || !Number.isSafeInteger(descriptor.offset) || descriptor.offset < 0 ||
      descriptor.offset % 4 || !Number.isSafeInteger(descriptor.count) || descriptor.count < 0) throw new Error(`Invalid source ${label} span`);
  const length = descriptor.count * size, end = descriptor.offset + length * 4;
  if (!Number.isSafeInteger(end) || end > bytes.byteLength) throw new Error(`Invalid source ${label} span`);
  const values = new Float32Array(length), view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < length; i++) values[i] = finite(view.getFloat32(descriptor.offset + 4 * i, true), label);
  return new THREE.BufferAttribute(values, size);
}
function nativeType(object) {
  const type = typeof object.type === 'number' ? TYPES[object.type] : object.type;
  if (!Object.values(TYPES).includes(type)) throw new Error(`Unsupported source geom type ${object.type}`);
  return type;
}
function visualSize(object, type) {
  if (object.visual_size) return vector(object.visual_size, 3, 'visual size');
  const size = vector(object.size, 3, 'geom size');
  if (type === 'sphere') return [size[0], size[0], size[0]];
  if (type === 'cylinder' || type === 'capsule') return [size[0], size[0], size[1]];
  return size;
}

// MuJoCo3.17 render/classic/render_context.c1075–1109: both sides share each axis mapping.
function boxUVs(geometry, size, plane = false) {
  const p = geometry.getAttribute('position'), n = geometry.getAttribute('normal'), uv = new Float32Array(p.count * 2);
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i) / size[0], y = p.getY(i) / size[1], z = plane ? 0 : p.getZ(i) / size[2];
    const nx = Math.abs(n.getX(i)), nz = Math.abs(n.getZ(i));
    uv[2*i] = ((plane || nz > .5 ? x : nx > .5 ? y : x) + 1) / 2;
    uv[2*i+1] = (1 - (plane || nz > .5 ? y : z)) / 2;
  }
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
}
function primitive(object, type) {
  const size = vector(object.size, 3, 'geom size');
  if (size.some(v => v < 0)) throw new Error('Invalid negative source geom size');
  let geometry;
  if (type === 'box') geometry = new THREE.BoxGeometry(...size.map(v => 2*v));
  else if (type === 'plane') {
    if (size[0] <= 0 || size[1] <= 0) throw new Error('Infinite source plane needs an authored display extent');
    geometry = new THREE.PlaneGeometry(2*size[0], 2*size[1]);
  } else if (type === 'sphere' || type === 'ellipsoid') {
    geometry = new THREE.SphereGeometry(1, 32, 24);
    geometry.rotateX(Math.PI / 2);
    geometry.scale(...(type === 'sphere' ? [size[0], size[0], size[0]] : size));
  } else if (type === 'cylinder') {
    geometry = new THREE.CylinderGeometry(size[0], size[0], 2*size[1], 32);
    geometry.rotateX(Math.PI / 2);
  } else if (type === 'capsule') {
    geometry = new THREE.CapsuleGeometry(size[0], 2*size[1], 8, 32);
    geometry.rotateX(Math.PI / 2);
  }
  if (!geometry) throw new Error(`Unsupported source primitive ${type}`);
  if (type === 'box' || type === 'plane') boxUVs(geometry, size, type === 'plane');
  return geometry;
}
function flag(value) { return value === true || value === 'true' || value === 1 || value === '1'; }
function textureType(value) { return typeof value === 'number' ? ['2d', 'cube', 'skybox'][value] : String(value).toLowerCase(); }
function resolvedRGBA(object, source) {
  if (object.effective_rgba) return vector(object.effective_rgba, 4, 'RGBA');
  const rgba = vector(object.rgba, 4, 'RGBA');
  // MuJoCo setMaterial: a non-default geom RGBA overrides material RGBA.
  return source && rgba.every((v, i) => v === [.5,.5,.5,1][i]) ? vector(source.rgba, 4, 'material RGBA') : rgba;
}
function opaqueTextureAlpha(material) {
  // Default MuJoCo nchannel=3 drops source PNG alpha before upload.
  material.onBeforeCompile = shader => {
    shader.fragmentShader = shader.fragmentShader.replace('#include <map_fragment>',
      '#ifdef USE_MAP\n diffuseColor *= vec4(texture2D(map, vMapUv).rgb, 1.0);\n#endif');
  };
  material.customProgramCacheKey = () => 'source-rgb-texture-v1';
}

function classicSourceDiffuse(material) {
  const textureHook = material.onBeforeCompile, textureKey = material.customProgramCacheKey();
  material.onBeforeCompile = shader => {
    textureHook.call(material, shader);
    // Classic source lights are fixed-function coefficients, not irradiance.
    // Three's Blinn-Phong diffuse divides by PI; source diffuse/ambient do not.
    // Scope this correction to the two diffuse calls; leave specular untouched.
    shader.fragmentShader = shader.fragmentShader.replace('#include <lights_phong_pars_fragment>',
      'vec3 sourceClassicDiffuse(const in vec3 color) { return color; }\n' +
      '#define BRDF_Lambert sourceClassicDiffuse\n#include <lights_phong_pars_fragment>\n#undef BRDF_Lambert');
  };
  material.customProgramCacheKey = () => `source-classic-diffuse-v1:${textureKey}`;
}

// OpenGL cube face lookup, using object-space texgen, not an environment reflection.
// MuJoCo3.17 render_gl3.c223–230; the source's 1x1 image is repeated on all six faces.
const CUBE_FUNCTION = `
varying vec3 vSourceCubeDirection;
uniform vec2 sourceCubeFlip;
vec2 sourceCubeUV(vec3 d) {
  vec3 a = abs(d); vec2 st;
  if (a.x >= a.y && a.x >= a.z) st = vec2(d.x >= 0.0 ? -d.z : d.z, -d.y) / max(a.x, 1e-30);
  else if (a.y >= a.z) st = vec2(d.x, d.y >= 0.0 ? d.z : -d.z) / max(a.y, 1e-30);
  else st = vec2(d.z >= 0.0 ? d.x : -d.x, -d.y) / max(a.z, 1e-30);
  vec2 uv = (st + 1.0) * 0.5;
  return mix(uv, 1.0 - uv, sourceCubeFlip);
}`;
function cubeMaterial(material, object, type, source, texture) {
  if (type === 'capsule') throw new Error('Source capsule cube texgen requires separate hemisphere coordinates');
  const size = visualSize(object, type), scale = new THREE.Vector3();
  for (let i = 0; i < 3; i++) {
    const normalizer = type === 'mesh' ? 1 : size[i];
    if (!(normalizer > 0)) throw new Error('Unsupported degenerate source cube texgen');
    scale.setComponent(i, (flag(source.texuniform) ? size[i] : 1) / normalizer);
  }
  const attrs = texture.original_attributes || {};
  material.onBeforeCompile = shader => {
    shader.uniforms.sourceCubeScale = { value: scale };
    shader.uniforms.sourceCubeFlip = { value: new THREE.Vector2(flag(attrs.hflip) ? 1 : 0, flag(attrs.vflip) ? 1 : 0) };
    shader.vertexShader = shader.vertexShader.replace('#include <common>', '#include <common>\nvarying vec3 vSourceCubeDirection;\nuniform vec3 sourceCubeScale;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvSourceCubeDirection = position * sourceCubeScale;');
    shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>\n${CUBE_FUNCTION}`)
      .replace('#include <map_fragment>', '#ifdef USE_MAP\n diffuseColor *= vec4(texture2D(map, sourceCubeUV(vSourceCubeDirection)).rgb, 1.0);\n#endif');
  };
  material.customProgramCacheKey = () => 'source-repeated-cube-v1';
}

/** Original source visuals and explicitly kinematic articulation, with no physics stepping. */
export async function loadSourceScene(data, base, { signal, onProgress } = {}) {
  if (!data || data.schema_version !== 1 || data.geometry_file !== 'geometry.bin') throw new Error('Unsupported source preview schema');
  if ((data.geometry_dtype ?? 'float32') !== 'float32' || (data.geometry_byte_order ?? 'little') !== 'little' ||
      (data.geometry_offset_unit ?? 'bytes') !== 'bytes') throw new Error('Unsupported source geometry encoding');
  const bodySpecs = indexed(data.bodies, 'bodies'), meshSpecs = indexed(data.meshes, 'meshes');
  const materialSpecs = indexed(data.materials, 'materials'), textureSpecs = indexed(data.textures, 'textures');
  const objects = records(data.objects, 'objects');
  indexed(objects, 'objects');
  const geometryLength = data.geometry_byte_length;
  if (!Number.isSafeInteger(geometryLength) || geometryLength < 0 || geometryLength > MAX_GEOMETRY) throw new Error('Source geometry exceeds viewer budget');
  const group = new THREE.Group(), bodies = new Map(), joints = new Map(), meshNodes = new Map();
  group.name = 'Source scene — kinematic inspection';
  const geometries = new Set(), materials = new Set(), textures = new Set(), bitmaps = new Set(), imageReservations = new Set();
  const imageCache = new Map(), textureCache = new Map(), meshCache = new Map();
  const warnings = [], warningKeys = new Set(), controller = new AbortController(), budget = new TextureBudget();
  let disposed = false;
  function warn(detail) {
    const key = JSON.stringify(detail);
    if (!warningKeys.has(key)) { warningKeys.add(key); warnings.push(detail); }
  }
  function dispose() {
    if (disposed) return;
    disposed = true; controller.abort(); signal?.removeEventListener('abort', dispose);
    group.removeFromParent(); group.clear();
    for (const resource of [...geometries, ...materials, ...textures]) resource.dispose();
    for (const bitmap of bitmaps) bitmap.close();
    for (const release of imageReservations) release();
    geometries.clear(); materials.clear(); textures.clear(); bitmaps.clear(); imageReservations.clear();
    bodies.clear(); joints.clear(); meshNodes.clear(); imageCache.clear(); textureCache.clear(); meshCache.clear();
  }
  function active() { if (disposed || controller.signal.aborted) throw new Error('Source scene load cancelled'); }
  function progress(stage, done, total) { active(); onProgress?.({ stage, done, total }); }
  signal?.addEventListener('abort', dispose, { once: true });
  if (signal?.aborted) dispose();
  async function image(texture) {
    const path = texture.path;
    const url = pathURL(base, path, /^textures\/[a-fA-F0-9]{64}\.(png|jpe?g|webp)$/);
    if (imageCache.has(path)) return imageCache.get(path);
    const pending = (async () => {
      const width = texture.width, height = texture.height;
      if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0 || width > 8192 || height > 8192) throw new Error('Invalid source image dimensions');
      const release = budget.reserve(width, height);
      imageReservations.add(release);
      try {
        const bytes = await fetchAssetBytes(url, MAX_IMAGE, controller.signal, { unavailable: 'Source texture unavailable' });
        if (texture.byte_length != null && bytes.byteLength !== texture.byte_length) throw new Error('Source texture length mismatch');
        const png = [137, 80, 78, 71, 13, 10, 26, 10];
        if (bytes.length < 33 || png.some((v, i) => bytes[i] !== v)) throw new Error('Unsupported source image format; PNG required');
        const header = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        if (header.getUint32(8) !== 13 || header.getUint32(12) !== 0x49484452 || header.getUint32(16) !== width || header.getUint32(20) !== height) throw new Error('Source image dimensions differ from header');
        active();
        const bitmap = await createImageBitmap(new Blob([bytes]), { imageOrientation: 'none', premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
        if (disposed || bitmap.width !== width || bitmap.height !== height) { bitmap.close(); throw new Error('Source texture cancelled or dimensions differ'); }
        bitmaps.add(bitmap);
        return bitmap;
      } catch (error) { release(); imageReservations.delete(release); throw error; }
    })();
    imageCache.set(path, pending);
    return pending;
  }
  async function loadTexture(texture) {
    const type = textureType(texture.type), attrs = texture.original_attributes || {};
    if (!['2d', 'cube'].includes(type) || !texture.path) throw new Error('Unsupported source procedural/skybox texture');
    if (type === 'cube' && ((attrs.gridsize && String(attrs.gridsize).trim() !== '1 1') || texture.width !== texture.height)) throw new Error('Unsupported source cube grid layout');
    const colorspace = type === 'cube' ? 'linear' : texture.colorspace;
    if (!['linear', 'srgb'].includes(colorspace)) throw new Error('Unresolved source texture color space');
    // MuJoCo classic cube upload uses GL_RGB, independently of PNG sRGB metadata.
    const key = JSON.stringify([texture.path, type, colorspace]);
    if (textureCache.has(key)) return textureCache.get(key);
    const bitmap = await image(texture); active();
    const value = new THREE.Texture(bitmap);
    textures.add(value); value.flipY = false;
    value.colorSpace = colorspace === 'srgb' ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    value.wrapS = value.wrapT = type === 'cube' ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
    value.magFilter = THREE.LinearFilter; value.minFilter = THREE.LinearMipmapLinearFilter;
    value.needsUpdate = true; textureCache.set(key, value);
    return value;
  }
  try {
    active();
    for (const feature of data.unsupported_features || []) warn(feature);
    const bytes = await fetchAssetBytes(pathURL(base, data.geometry_file, /^geometry\.bin$/), MAX_GEOMETRY, controller.signal,
      { unavailable: 'Source geometry unavailable' });
    if (bytes.byteLength !== geometryLength) throw new Error('Source geometry length mismatch');
    for (const body of bodySpecs.values()) {
      const node = new THREE.Group(); node.name = body.name || `body_${body.id}`;
      node.userData.sourceBodyId = body.id; pose(node, body.position, body.quaternion); bodies.set(body.id, node);
    }
    for (const body of bodySpecs.values()) {
      const node = bodies.get(body.id), visited = new Set([body.id]); let parent = body.parent_id;
      while (parent != null) {
        if (!bodySpecs.has(parent) || visited.has(parent) || visited.size > 1024) throw new Error('Invalid source body hierarchy');
        visited.add(parent); parent = bodySpecs.get(parent).parent_id;
      }
      (body.parent_id == null ? group : bodies.get(body.parent_id)).add(node);
      const list = records(body.joints || [], 'body joints');
      for (const source of list) {
        identifier(source.id); if (joints.has(source.id)) throw new Error('Duplicate source joint id');
        const supported = list.length === 1 && source.inspection_supported !== false && ['hinge', 'slide'].includes(source.type);
        const currentValue = ['free', 'ball'].includes(source.type)
          ? vector(source.reference, source.type === 'free' ? 7 : 4, 'joint reference')
          : finite(source.reference, 'joint reference');
        const joint = { ...source, body_id: body.id, supported, currentValue };
        if (supported) {
          vector(source.axis, 3, 'joint axis'); vector(source.position, 3, 'joint pivot');
          if (Math.abs(Math.hypot(...source.axis) - 1) > 1e-5) throw new Error('Invalid source joint axis');
          if (source.limited) vector(source.range, 2, 'joint range');
        } else warn({ joint_id: source.id, body_id: body.id, reason: 'Unsupported multi-joint, free or ball body; retained at source pose' });
        joints.set(source.id, joint);
      }
    }
    let completed = 0;
    for (const object of objects) {
      active();
      const type = nativeType(object), source = object.material_id == null ? null : materialSpecs.get(object.material_id);
      if (object.material_id != null && !source) throw new Error('Missing source material');
      const body = bodies.get(object.body_id); if (!body) throw new Error('Missing source geom body');
      let geometry;
      if (type === 'mesh') {
        if (!meshCache.has(object.mesh_id)) {
          const mesh = meshSpecs.get(object.mesh_id); if (!mesh) throw new Error('Missing native source mesh');
          const value = new THREE.BufferGeometry(); geometries.add(value);
          const position = dataAttribute(bytes, mesh.position, 3, 'mesh positions');
          const normal = dataAttribute(bytes, mesh.normal, 3, 'mesh normals');
          if (position.count % 3 || normal.count !== position.count) throw new Error('Invalid source triangle stream');
          value.setAttribute('position', position); value.setAttribute('normal', normal);
          if (mesh.uv) {
            const uv = dataAttribute(bytes, mesh.uv, 2, 'mesh UVs');
            if (uv.count !== position.count) throw new Error('Invalid source UV count');
            value.setAttribute('uv', uv);
          }
          meshCache.set(object.mesh_id, value);
        }
        geometry = meshCache.get(object.mesh_id);
      } else { geometry = primitive(object, type); geometries.add(geometry); }
      const rgba = resolvedRGBA(object, source);
      const specular = source ? finite(source.specular, 'specular') : .5;
      const shininess = source ? finite(source.shininess, 'shininess') : .5;
      const emission = source ? finite(source.emission, 'emission') : 0;
      const color = new THREE.Color().setRGB(...rgba.slice(0, 3));
      const material = new THREE.MeshPhongMaterial({ color, opacity: rgba[3], transparent: rgba[3] < 1,
        specular: new THREE.Color().setRGB(specular, specular, specular), shininess: 128*shininess,
        emissive: color.clone().multiplyScalar(emission), side: THREE.DoubleSide });
      materials.add(material);
      material.userData.sourceMaterial = source || null;
      if (source?.reflectance) warn({ feature: 'source_reflectance_not_rendered', object_id: object.id, material_id: source.id });
      if (source?.layers?.length) throw new Error('Unsupported source material texture layers');
      if (source?.texture_id != null) {
        const texture = textureSpecs.get(source.texture_id);
        if (!texture) throw new Error('Missing source texture binding');
        const mapping = textureType(texture.type);
        try {
          material.map = await loadTexture(texture);
          if (mapping === 'cube') cubeMaterial(material, object, type, source, texture);
          else {
            const channels = Number(texture.nchannel ?? texture.original_attributes?.nchannel ?? 3);
            if (![3, 4].includes(channels)) throw new Error('Unsupported source texture channel count');
            if (channels === 3) opaqueTextureAlpha(material);
            if (!geometry.getAttribute('uv') || !['mesh', 'box', 'plane'].includes(type)) throw new Error('Source primitive or missing-UV 2D mapping not implemented');
            geometry = geometry.clone(); geometries.add(geometry);
            const uv = geometry.getAttribute('uv'), size = visualSize(object, type), repeat = vector(source.texrepeat, 2, 'texture repeat');
            const attrs = texture.original_attributes || {};
            for (let i = 0; i < uv.count; i++) {
              let u = uv.getX(i) * (repeat[0] > 0 ? repeat[0] : 1) * (flag(source.texuniform) && size[0] > 0 ? size[0] : 1);
              let v = uv.getY(i) * (repeat[1] > 0 ? repeat[1] : 1) * (flag(source.texuniform) && size[1] > 0 ? size[1] : 1);
              if (flag(attrs.hflip)) u = 1-u; if (flag(attrs.vflip)) v = 1-v;
              uv.setXY(i, u, v);
            }
          }
        } catch (error) {
          // Missing bytes/failed decode is a load failure. Mapping omissions are visible and inspectable.
          if (!/^Unsupported source|^Unresolved source|^Source primitive|^Source capsule/.test(error.message)) throw error;
          material.map = null;
          warn({ object_id: object.id, material_id: source.id, texture_id: texture.id, reason: error.message });
        }
      }
      classicSourceDiffuse(material);
      const mesh = new THREE.Mesh(geometry, material); mesh.name = object.name || `geom_${object.id}`;
      pose(mesh, object.position, object.quaternion);
      mesh.userData.object = { ...object, id: object.id, body_id: object.body_id, name: mesh.name };
      mesh.userData.sourceInspection = 'Kinematic source preview; no contact or task validation';
      mesh.castShadow = true; mesh.receiveShadow = true; body.add(mesh); meshNodes.set(object.id, mesh);
      progress('objects', ++completed, objects.length);
    }
    function setJointValue(id, value) {
      active(); const joint = joints.get(id);
      if (!joint || !joint.supported) throw new Error('Unsupported source joint for kinematic inspection');
      finite(value, 'joint value');
      if (joint.limited && (value < joint.range[0] || value > joint.range[1])) throw new Error('Source joint value outside limits');
      const body = bodySpecs.get(joint.body_id), node = bodies.get(joint.body_id);
      pose(node, body.position, body.quaternion);
      const axis = new THREE.Vector3().fromArray(joint.axis), pivot = new THREE.Vector3().fromArray(joint.position), delta = value - joint.reference;
      if (joint.type === 'slide') node.position.add(axis.multiplyScalar(delta).applyQuaternion(node.quaternion));
      else {
        const rotation = new THREE.Quaternion().setFromAxisAngle(axis, delta);
        const offset = pivot.clone().sub(pivot.clone().applyQuaternion(rotation)).applyQuaternion(node.quaternion);
        node.position.add(offset); node.quaternion.multiply(rotation);
      }
      joint.currentValue = value; group.updateMatrixWorld(true);
      return value;
    }
    group.userData.sourceScene = { warnings, kinematicOnly: true, source: data.source || null };
    group.updateMatrixWorld(true); active();
    return { group, bodies, joints, meshNodes, warnings, setJointValue, dispose };
  } catch (error) { dispose(); throw error; }
}
