#!/usr/bin/env node
/** Export Airsup's actual procedural production-line geometry. No browser/WebGL. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';

const REVISION = 'e21864bda5a614ffae8d0a835518be8e2e571dce';
const REPOSITORY = 'https://github.com/AirsupHQ/airsup-lab';
const argv = process.argv.slice(2);
const option = (name, fallback) => {
  const index = argv.indexOf(name);
  return index < 0 ? fallback : argv[index + 1];
};
if (argv.includes('--help')) {
  console.log('node tools/import_airsup_reference.mjs --source <pinned-source-checkout> [--output .robot-worlds/references/airsup-production-line] [--warm-seconds 40] [--no-room]');
  process.exit(0);
}
const source = fs.realpathSync(path.resolve(option('--source', '/tmp/robot-worlds-airsup-source')));
const output = path.resolve(option('--output', '.robot-worlds/references/airsup-production-line'));
const warmSeconds = Number(option('--warm-seconds', '40'));
const includeRoom = !argv.includes('--no-room');
if (!Number.isFinite(warmSeconds) || warmSeconds < 0 || warmSeconds > 120) throw new Error('Warm seconds must be in [0,120]');
const head = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: source, encoding: 'utf8' }).trim();
if (head !== REVISION) throw new Error(`Reference source must be pinned to ${REVISION}`);
const requireSource = createRequire(path.join(source, 'package.json'));
const licenseText = fs.readFileSync(path.join(source, 'LICENSE'), 'utf8');
const THREE = requireSource('three');
const esbuild = requireSource('esbuild');
const canvas = requireSource('@napi-rs/canvas');
globalThis.HTMLCanvasElement = Object.getPrototypeOf(canvas.createCanvas(1, 1)).constructor;
globalThis.ImageData = canvas.ImageData;
globalThis.document = { createElement(name) {
  if (name !== 'canvas') throw new Error(`Unsupported DOM request from reference: ${name}`);
  return canvas.createCanvas(1, 1);
} };
// THREE UUIDs are random by default. Pin their seed as well as the source sim's.
let randomSeed = 81287;
Math.random = () => ((randomSeed = (1664525 * randomSeed + 1013904223) >>> 0) / 4294967296);

fs.mkdirSync(path.join(output, 'assets'), { recursive: true });
const bundle = path.join(output, '.reference-builder.cjs');
const sourceFiles = new Set();
await esbuild.build({
  stdin: { contents: `export { ProductionLine } from './src/line/line';
export { LINE_X, STATIONS } from './src/line/sim';
export { Room, LINE_POS } from './src/scene/room';
export { SHOTS } from './src/camera';`, resolveDir: source, loader: 'ts' },
  platform: 'node', format: 'cjs', bundle: true, outfile: bundle,
  plugins: [{ name: 'headless-environment-map', setup(build) {
    build.onLoad({ filter: /[\\/]src[\\/].*\.ts$/ }, async args => {
      let contents = fs.readFileSync(args.path, 'utf8');
      const relative = path.relative(source, args.path);
      const pinned = execFileSync('git', ['show', `${REVISION}:${relative}`], {cwd:source, encoding:'utf8'});
      if (contents !== pinned) throw new Error(`Source file differs from pinned revision: ${relative}`);
      sourceFiles.add(relative);
      if (args.path.endsWith(`${path.sep}scene${path.sep}room.ts`)) {
        const original = 'const pm = new THREE.PMREMGenerator(renderer)\n  const tex = pm.fromScene(s, 0.02).texture\n  pm.dispose()\n  return tex';
        if (!contents.includes(original)) throw new Error('Pinned Room headless seam changed');
        contents = contents.replace(original, 'globalThis.__robotWorldsEnvironment = s\n  return null /* export: viewer performs GPU environment-map baking */');
      }
      return { contents, loader: 'ts', resolveDir: path.dirname(args.path) };
    });
  } }],
});
const built = createRequire(bundle)(bundle);
const line = new built.ProductionLine();
line.warm(warmSeconds);
line.update(0, warmSeconds);
const scene = new THREE.Group();
scene.name = 'Airsup production area — source-derived static reference';
if (includeRoom) {
  const room = new built.Room(null);
  room.lineMount.name = 'source-line-mount';
  room.decor.group.name = 'source-decor';
  room.ceiling.name = 'source-ceiling';
  room.bay.group.name = 'source-car-bay';
  room.lineMount.add(line.root);
  room.group.name = 'source-room';
  scene.add(room.group);
} else scene.add(line.root);
scene.updateMatrixWorld(true);

const origin = includeRoom ? built.LINE_POS.clone() : new THREE.Vector3();
const toZUp = new THREE.Matrix4().makeRotationX(Math.PI / 2);
const shift = new THREE.Matrix4().makeTranslation(-origin.x, -origin.y, -origin.z);
const transform = toZUp.clone().multiply(shift);
// The immediate source area: original racks, room shell and nearby props.
const crop = new THREE.Box3(new THREE.Vector3(-6, -6.8, 0), new THREE.Vector3(6, 4.3, 5.2));
const physical = [];
const components = [];
const visual = new THREE.Group();
visual.name = 'source-derived-z-up-reference';
visual.userData.attribution = {repository:REPOSITORY,revision:REVISION,license:'MIT',license_text:licenseText};
const materialMap = new Map();
const textureMap = new Map();
const excluded = [];
const sourceLights = [];
let triangles = 0;
let meshNumber = 0;
const overall = new THREE.Box3();
const round = value => Number(value.toFixed(9));
const vector = v => v.toArray().map(round);
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
const safeName = text => text.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 65);
function materialInfo(material) {
  const color = material.color?.toArray() || [1, 1, 1];
  const emissive = material.isMeshBasicMaterial ? color : (material.emissive?.toArray() || [0, 0, 0]).map(v => v * (material.emissiveIntensity ?? 1));
  const info = { type: material.type, color_linear: color.map(round),
    color_srgb_hex: material.color ? `#${material.color.getHexString()}` : '#ffffff',
    metalness: material.metalness ?? 0, roughness: material.roughness ?? 1,
    opacity: material.opacity ?? 1, transparent: material.transparent ?? false,
    emissive: emissive.map(round), side: material.side,
    clearcoat: material.clearcoat ?? 0, clearcoatRoughness: material.clearcoatRoughness ?? 0,
    anisotropy: material.anisotropy ?? 0, blending: material.blending,
    procedural_shader_detail: material.userData?.u?.uDetail?.value?.toArray() || null,
  };
  if (material.map) {
    const image = material.map.image;
    if (image?.toBuffer) {
      const bytes = image.toBuffer('image/png');
      const name = `assets/texture-${sha256(bytes).slice(0, 20)}.png`;
      if (!textureMap.has(name)) fs.writeFileSync(path.join(output, name), bytes);
      textureMap.set(name, true);
      info.map = { path: name, color_space: material.map.colorSpace,
        repeat: material.map.repeat.toArray(), offset: material.map.offset.toArray(),
        wrapS: material.map.wrapS, wrapT: material.map.wrapT, flipY: material.map.flipY };
    }
  }
  return info;
}

function clipPlaneGeometry(geometry) {
  // Clip source floor/wall planes to the immediate area, interpolating UV/normals.
  // Other source solids are never remodeled or clipped.
  const input = geometry.index ? geometry.toNonIndexed() : geometry;
  const positions = input.getAttribute('position');
  const normals = input.getAttribute('normal');
  const uv = input.getAttribute('uv');
  const result = { position: [], normal: [], uv: [] };
  const planes = [[0,crop.min.x,1],[0,crop.max.x,-1],[1,crop.min.y,1],[1,crop.max.y,-1],[2,crop.min.z,1],[2,crop.max.z,-1]];
  for (let i = 0; i < positions.count; i += 3) {
    let polygon = [0, 1, 2].map(k => ({ p: [positions.getX(i+k),positions.getY(i+k),positions.getZ(i+k)],
      n: normals ? [normals.getX(i+k),normals.getY(i+k),normals.getZ(i+k)] : [0,0,1],
      u: uv ? [uv.getX(i+k),uv.getY(i+k)] : [0,0] }));
    for (const [axis, bound, sign] of planes) {
      const next = [];
      for (let j = 0; j < polygon.length; j++) {
        const a = polygon[j], b = polygon[(j+1)%polygon.length];
        const da = (a.p[axis]-bound)*sign, db = (b.p[axis]-bound)*sign;
        if (da >= -1e-8) next.push(a);
        if ((da >= 0) !== (db >= 0)) {
          const t = da/(da-db);
          const lerp = key => a[key].map((v,k) => v+(b[key][k]-v)*t);
          next.push({ p: lerp('p'), n: lerp('n'), u: lerp('u') });
        }
      }
      polygon = next;
    }
    for (let j = 1; j < polygon.length-1; j++) for (const vertex of [polygon[0],polygon[j],polygon[j+1]]) {
      result.position.push(...vertex.p); result.normal.push(...vertex.n); result.uv.push(...vertex.u);
    }
  }
  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.Float32BufferAttribute(result.position, 3));
  out.setAttribute('normal', new THREE.Float32BufferAttribute(result.normal, 3));
  out.setAttribute('uv', new THREE.Float32BufferAttribute(result.uv, 2));
  return out;
}

function exportMesh(mesh, matrix, sourcePath, instanceIndex=null) {
  const id = `airsup_${String(meshNumber++).padStart(4, '0')}`;
  let geometry = mesh.geometry.clone().applyMatrix4(transform.clone().multiply(matrix));
  geometry.computeBoundingBox();
  let box = geometry.boundingBox;
  if (box.isEmpty() || !crop.intersectsBox(box)) return;
  const originalSize = box.getSize(new THREE.Vector3());
  const planar = Math.min(...originalSize.toArray()) < 1e-6;
  const outside = !crop.clone().expandByScalar(1e-5).containsBox(box);
  if (outside && planar) {
    geometry = clipPlaneGeometry(geometry);
    geometry.computeBoundingBox(); box = geometry.boundingBox;
    if (box.isEmpty()) return;
  } else if (outside) {
    excluded.push({ id, source_path: sourcePath, reason: 'solid crosses production-area crop boundary' });
    return;
  }
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  overall.union(box);
  const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  if (mats.length !== 1) throw new Error(`Multiple materials need explicit group splitting: ${sourcePath}`);
  const mat = mats[0];
  const info = materialInfo(mat);
  const materialKey = sha256(JSON.stringify(info));
  materialMap.set(materialKey, info);
  geometry.translate(-center.x,-center.y,-center.z);
  // Parametric geometries serialize constructor parameters, discarding baked
  // vertex transforms. A plain BufferGeometry preserves the actual export.
  const clone = new THREE.Mesh(new THREE.BufferGeometry().copy(geometry), mat);
  clone.name = id;
  clone.position.copy(center);
  clone.layers.mask = mesh.layers.mask;
  visual.add(clone);
  const local = geometry.index ? geometry.toNonIndexed() : geometry;
  const pos = local.getAttribute('position'), nor = local.getAttribute('normal'), uv = local.getAttribute('uv');
  const commentMaterial = { metalness: info.metalness, roughness: info.roughness,
    opacity: info.opacity, emissive: info.emissive };
  const sourceFile = sourcePath.includes(':line/') ? 'src/line/line.ts'
    : sourcePath.includes(':source-decor/') ? 'src/scene/decor.ts'
    : sourcePath.includes(':source-car-bay/') ? 'src/car/bay.ts' : 'src/scene/room.ts';
  const lines = [...licenseText.trimEnd().split('\n').map(line=>`# ${line}`),
    '# Derived from Airsup Lab — original source copyright and permission above',
    `# ${REPOSITORY}/blob/${REVISION}/${sourceFile}`, `# source_mesh ${sourcePath} instance=${instanceIndex}`,
    `# robot_worlds_material ${JSON.stringify(commentMaterial)}`, `o ${id}`];
  for (let i=0;i<pos.count;i++) lines.push(`v ${round(pos.getX(i))} ${round(pos.getY(i))} ${round(pos.getZ(i))}`);
  if (uv) for (let i=0;i<uv.count;i++) lines.push(`vt ${round(uv.getX(i))} ${round(uv.getY(i))}`);
  if (nor) for (let i=0;i<nor.count;i++) lines.push(`vn ${round(nor.getX(i))} ${round(nor.getY(i))} ${round(nor.getZ(i))}`);
  let faceCount = 0;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  for (let i=0;i<pos.count;i+=3) {
    a.fromBufferAttribute(pos,i); b.fromBufferAttribute(pos,i+1); c.fromBufferAttribute(pos,i+2);
    if (b.sub(a).cross(c.sub(a)).lengthSq() < 1e-20) continue;
    const face = [i+1,i+2,i+3].map(k => uv && nor ? `${k}/${k}/${k}` : nor ? `${k}//${k}` : `${k}`);
    lines.push(`f ${face.join(' ')}`); faceCount++;
  }
  const objPath = `assets/${id}.obj`;
  const objText = lines.join('\n')+'\n';
  fs.writeFileSync(path.join(output,objPath),objText);
  triangles += faceCount;
  const component = { id, source_file:sourceFile, source_path: sourcePath, instance_index: instanceIndex,
    geometry_type: mesh.geometry.type, geometry_parameters: mesh.geometry.parameters || null,
    position: vector(center), size: vector(size), material: info, mesh_path: objPath,
    sha256: sha256(objText), triangles: faceCount, source_layer_mask: mesh.layers.mask,
    physics: planar ? 'excluded_zero_thickness_visual' : 'static_per_mesh_convex_hull',
    crop_applied: outside && planar };
  components.push(component);
  if (!planar) physical.push({ id, label: `${mesh.geometry.type} (${sourcePath})`, kind:'mesh',
    position: vector(center), size: vector(size), color: info.color_linear.map(v => Math.min(1,Math.max(0,v))),
    mass:1, movable:false, mesh_path:objPath });
}

function walk(object, sourcePath='root', ancestorVisible=true) {
  const visible = ancestorVisible && object.visible;
  if (!visible) return;
  if (object.isLight) {
    const position = new THREE.Vector3().setFromMatrixPosition(object.matrixWorld).applyMatrix4(transform);
    if (object.isHemisphereLight || object.isAmbientLight || crop.containsPoint(position)) {
      const light = object.clone(false);
      light.name = `source-light-${sourceLights.length}`;
      if (object.isHemisphereLight) light.position.copy(object.position).applyMatrix4(toZUp);
      else light.position.copy(position);
      if (object.target) {
        light.target = new THREE.Object3D();
        light.target.position.copy(new THREE.Vector3().setFromMatrixPosition(object.target.matrixWorld).applyMatrix4(transform));
        visual.add(light.target);
      }
      visual.add(light);
      sourceLights.push({ type:object.type, position:vector(light.position),
        target:light.target?vector(light.target.position):null, color_linear:object.color.toArray(),
        intensity:object.intensity, distance:object.distance??null, decay:object.decay??null,
        angle:object.angle??null, penumbra:object.penumbra??null,
        ground_color_linear:object.groundColor?.toArray()??null });
    }
  }
  if (object.isMesh) {
    if (object.isInstancedMesh) {
      const instance = new THREE.Matrix4();
      for (let i=0;i<object.count;i++) {
        object.getMatrixAt(i,instance);
        if (Math.abs(instance.determinant()) < 1e-12) continue;
        exportMesh(object,object.matrixWorld.clone().multiply(instance),sourcePath,i);
      }
    } else exportMesh(object,object.matrixWorld,sourcePath);
  }
  object.children.forEach((child,i) => walk(child,`${sourcePath}/${i}:${safeName(child.name||child.type)}`,visible));
}
walk(scene);
const convertPoint = values => vector(new THREE.Vector3(...values).applyMatrix4(transform));
const shots = Object.fromEntries(['line','lineClose','lineBottleneck','lineScreen','lineWide'].map(name => {
  const shot = built.SHOTS[name];
  return [name,{position:convertPoint(shot.pos),target:convertPoint(shot.target),fov:shot.fov}];
}));
const stations = built.STATIONS.map((station,i) => ({name:station.name,cycle_source_seconds:station.cycle,
  position:[built.LINE_X[i],0,.92],source_station:i}));
const regions = stations.map((station,i) => ({ id:`visit_${i}`,label:`${station.name} aisle`,
  position:[station.position[0],-1.5,.75],size:[.65,.65,1.5] }));
const spec = {schema_version:'1.0',name:'Airsup source-derived drone production area',
  description:'Pinned upstream five-station production-line geometry and immediate room context, imported as a static physical reference.',
  task:'Visit the aisle in front of Frame, Motors, Electronics, Test and Pack, in that order.',
  robot:'tiago++',bounds:[12,13.6,5.3],robot_start:[-4,-2,0],objects:physical,regions,
  rubric:stations.map((station,i)=>({id:`inspect_${i}`,description:`Visit ${station.name} station aisle`,
    predicate:'robot_in_region',target_id:`visit_${i}`,mode:'eventually',duration:0,
    after:i?[`inspect_${i-1}`]:[],intent:'achieve'})),
  assumptions:[`Geometry is source-derived from ${REPOSITORY} at ${REVISION}; MIT attribution is in AIRSUP-LICENSE.txt.`,
    `Source uses Y-up; this export uses (x,y,z) -> (x,-z,y), rebased at source LINE_POS ${origin.toArray()}.`,
    `Source discrete simulation was warmed for ${warmSeconds}s and frozen at ${line.sim.t}s of line time. Conveyors and station arms do not run as physical controllers here.`,
    'Static mesh collision uses a per-part convex hull; concave interiors, mass and friction are not measured source physics.',
    'Zero-thickness screens, floor/wall planes and glow effects remain in the visual reference sidecar; native physics omits these plane meshes and uses its own floor.',
    'Source PBR properties and procedural texture metadata are preserved in reference-manifest.json. MuJoCo rendering does not reproduce the source custom shaders, bloom or baked environment lighting.',
    'This is an explicit source-reference import and navigation task, not an autonomous text-generation or full production-process demonstration.' ],
  source_prompt:'Reproduce Airsup’s open-source five-station drone production area as closely as its pinned geometry permits.',
  unvalidated_requirements:[]};
const manifest = {schema_version:'1.0',kind:'source_geometry_reference',repository:REPOSITORY,revision:REVISION,
  license:'MIT',copyright:'Copyright (c) 2026 The lab authors',coordinate_transform:'(x,y,z) -> (x-LINE_POS.x, -(z-LINE_POS.z), y)',
  source_origin:origin.toArray(),warm_seconds:warmSeconds,source_line_time:line.sim.t,
  source_simulation:{finished:line.sim.finished,throughput:line.sim.throughput,items:line.sim.items.length,settings:line.sim.settings},
  dependencies:Object.fromEntries([['three','three'],['esbuild','esbuild'],['canvas','@napi-rs/canvas']].map(([label,pkg])=>[label,JSON.parse(fs.readFileSync(path.join(source,'node_modules',pkg,'package.json'),'utf8')).version])),
  source_files:Object.fromEntries([...sourceFiles].sort().map(file=>[file,sha256(fs.readFileSync(path.join(source,file)))])),
  headless_change:'GPU environment-map baking is replaced by scene capture; actual upstream geometry, lighting, environment-scene constructors and simulation are executed.',
  crop_z_up:{min:vector(crop.min),max:vector(crop.max)},bounds_z_up:{min:vector(overall.min),max:vector(overall.max)},
  include_room:includeRoom,stations,camera_shots:shots,source_lights:sourceLights,environment_bake_sigma:.02,
  counts:{visual_meshes:components.length,physical_meshes:physical.length,triangles,textures:textureMap.size,excluded_boundary_solids:excluded.length},
  materials:[...materialMap.values()],components,excluded,
  limitations:spec.assumptions.slice(2)};
visual.updateMatrixWorld(true);
const serialized = visual.toJSON();
const serializedGeometry = new Map(serialized.geometries.map(g=>[g.uuid,g]));
const expectedComponents = new Map(components.map(c=>[c.id,c]));
let maximumRoundtripError = 0;
for (const child of serialized.object.children.filter(c=>c.type==='Mesh')) {
  const expected = expectedComponents.get(child.name);
  const geometryJSON = serializedGeometry.get(child.geometry);
  if (!expected || geometryJSON.type !== 'BufferGeometry' || !geometryJSON.data) throw new Error('Visual export lost baked geometry');
  const geometry = new THREE.BufferGeometryLoader().parse(geometryJSON);
  geometry.applyMatrix4(new THREE.Matrix4().fromArray(child.matrix));
  geometry.computeBoundingBox();
  const center = geometry.boundingBox.getCenter(new THREE.Vector3()).toArray();
  const size = geometry.boundingBox.getSize(new THREE.Vector3()).toArray();
  maximumRoundtripError = Math.max(maximumRoundtripError,
    ...center.map((v,i)=>Math.abs(v-expected.position[i])), ...size.map((v,i)=>Math.abs(v-expected.size[i])));
}
if (maximumRoundtripError > 1e-5) throw new Error(`Visual geometry roundtrip changed source coordinates by ${maximumRoundtripError}m`);
manifest.validation = {serialized_geometry_components:components.length, maximum_roundtrip_error_m:maximumRoundtripError};
fs.copyFileSync(path.join(source,'LICENSE'),path.join(output,'AIRSUP-LICENSE.txt'));
fs.writeFileSync(path.join(output,'world.json'),JSON.stringify(spec,null,2)+'\n');
fs.writeFileSync(path.join(output,'reference-manifest.json'),JSON.stringify(manifest,null,2)+'\n');
fs.writeFileSync(path.join(output,'visual-scene.json'),JSON.stringify(serialized)+'\n');
if (globalThis.__robotWorldsEnvironment) {
  globalThis.__robotWorldsEnvironment.rotation.x = Math.PI/2;
  globalThis.__robotWorldsEnvironment.updateMatrixWorld(true);
  fs.writeFileSync(path.join(output,'environment-scene.json'),JSON.stringify(globalThis.__robotWorldsEnvironment.toJSON())+'\n');
}
fs.unlinkSync(bundle);
console.log(JSON.stringify({output,world:path.join(output,'world.json'),manifest:path.join(output,'reference-manifest.json'),...manifest.counts},null,2));
