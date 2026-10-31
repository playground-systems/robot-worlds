import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import test, { after } from 'node:test';

// Use the shipped Three implementation without creating a renderer or GPU context.
const directory = await mkdtemp(join(tmpdir(), 'robot-worlds-scene-assets-'));
after(() => rm(directory, { recursive: true, force: true }));
await writeFile(join(directory, 'package.json'), '{"type":"module"}');
for (const name of ['vendor/three.core.js', 'vendor/OBJLoader.js', 'scene-assets.js',
  'scene-resources.js', 'scene-materials.js', 'scene-appearance.js', 'scene-textures.js', 'scene-batching.js']) {
  const source = await readFile(new URL(`../src/robot_worlds/static/${name}`, import.meta.url), 'utf8');
  await writeFile(join(directory, name.split('/').at(-1)), source
    .replaceAll("from 'three'", "from './three.core.js'")
    .replaceAll("'./vendor/OBJLoader.js'", "'./OBJLoader.js'"));
}
const { SceneAssets } = await import(pathToFileURL(join(directory, 'scene-assets.js')));
const { batchSourceMeshes } = await import(pathToFileURL(join(directory, 'scene-batching.js')));
const { disposeTree, SceneResourceCache } = await import(pathToFileURL(join(directory, 'scene-resources.js')));
const THREE = await import(pathToFileURL(join(directory, 'three.core.js')));
const source = `v -1 -2 -3
v 1 -2 -3
v 1 2 -3
v -1 2 -3
v -1 -2 3
v 1 -2 3
v 1 2 3
v -1 2 3
f 1 2 3 4
f 5 8 7 6
f 1 5 6 2
f 4 3 7 8
f 1 4 8 5
f 2 6 7 3
`;
const surface = { metallic: .6, roughness: .4, normal_scale: [1, 1], emissive: [0, 0, 0],
  double_sided: false, alpha_mode: 'OPAQUE', alpha_cutoff: .5, wrap_s: 'repeat', wrap_t: 'repeat' };
const object = { id: 'rack', label: 'Rack', kind: 'mesh', mesh_path: 'assets/rack.obj',
  texture_path: 'assets/color.png', normal_texture_path: 'assets/normal.png', orm_texture_path: 'assets/orm.png',
  surface, position: [0, 0, 3], size: [2, 4, 6], color: [1, 1, 1], opacity: 1 };

function png() {
  const bytes = new Uint8Array(33);
  bytes.set([137,80,78,71,13,10,26,10]);
  const header = new DataView(bytes.buffer);
  header.setUint32(8, 13); header.setUint32(12, 0x49484452);
  header.setUint32(16, 4); header.setUint32(20, 4);
  return bytes; // Header-only fixture; bitmap decoding is explicitly mocked below.
}

function fixtures(t, respond) {
  const calls = [], counts = { closed: 0 };
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push(url);
    return respond ? respond(url, options) : new Response(url.endsWith('.obj') ? source : png());
  });
  const prior = globalThis.createImageBitmap;
  globalThis.createImageBitmap = async () => ({ width: 4, height: 4, close() { counts.closed++; } });
  t.after(() => { globalThis.createImageBitmap = prior; });
  const assets = new SceneAssets(); t.after(() => assets.dispose());
  return { assets, calls, counts };
}

test('64 repeated fixtures share real geometry/material/maps with independent transforms and selection', async t => {
  const { assets, calls, counts } = fixtures(t);
  const instances = await Promise.all(Array.from({ length: 64 }, (_, i) =>
    assets.load({ ...object, id: `rack${i}`, position: [i * 4, 0, 3] }, '/world/')));
  assert.equal(calls.length, 4); // One OBJ and its three maps, including concurrent requests.
  assert.equal(assets.textureBudget.used, 3*Math.ceil(4*4*4*4/3));
  const first = instances[0].children[0];
  let geometryDisposals = 0, materialDisposals = 0;
  first.geometry.addEventListener('dispose', () => geometryDisposals++);
  first.material.addEventListener('dispose', () => materialDisposals++);
  for (const [i, instance] of instances.entries()) {
    assert.equal(instance.children[0].geometry, first.geometry);
    assert.equal(instance.children[0].material, first.material);
    assert.equal(instance.children[0].userData.object.id, `rack${i}`);
    const box = new THREE.Box3().setFromObject(instance);
    assert.deepEqual(box.getSize(new THREE.Vector3()).toArray(), [2, 4, 6]);
    assert.deepEqual(box.getCenter(new THREE.Vector3()).toArray(), [i * 4, 0, 3]);
  }
  const rotated = await assets.load({ ...object, position: [0, 0, 1], size: [4, 2, 2], yaw: Math.PI / 2 }, '/world/');
  const bounds = new THREE.Box3().setFromObject(rotated).getSize(new THREE.Vector3()).toArray();
  bounds.forEach((v, i) => assert(Math.abs(v - [2, 4, 2][i]) < 1e-6));
  assert.equal(rotated.children[0].geometry, first.geometry);
  instances.forEach(disposeTree); disposeTree(rotated);
  assert.deepEqual([geometryDisposals, materialDisposals, counts.closed], [0, 0, 0]);
  assets.dispose(); assets.dispose();
  assert.equal(assets.textureBudget.used, 0);
  assert.deepEqual([geometryDisposals, materialDisposals, counts.closed], [1, 1, 3]);
});

test('different finishes share geometry/maps but wrapping, color space and world URLs stay distinct', async t => {
  const { assets, calls } = fixtures(t);
  const first = (await assets.load(object, '/one/')).children[0];
  const tinted = (await assets.load({ ...object, color: [.2, .3, .4] }, '/one/')).children[0];
  assert.notEqual(first.material, tinted.material);
  assert.equal(first.material.map, tinted.material.map);
  assert.equal(first.geometry, tinted.geometry);
  const clamped = (await assets.load({ ...object, surface: { ...surface, wrap_s: 'clamp' } }, '/one/')).children[0];
  assert.notEqual(clamped.material.map, first.material.map);
  assert.equal(clamped.material.map.wrapS, THREE.ClampToEdgeWrapping);
  assert.equal(first.material.map.wrapS, THREE.RepeatWrapping);
  const dual = (await assets.load({ ...object, normal_texture_path: object.texture_path }, '/one/')).children[0];
  assert.notEqual(dual.material.map, dual.material.normalMap);
  assert.equal(dual.material.map.colorSpace, THREE.SRGBColorSpace);
  assert.equal(dual.material.normalMap.colorSpace, THREE.NoColorSpace);
  const otherWorld = (await assets.load(object, '/two/')).children[0];
  assert.notEqual(otherWorld.geometry, first.geometry);
  assert.notEqual(otherWorld.material.map, first.material.map);
  assert.equal(calls.filter(url => url.endsWith('rack.obj')).length, 2);
});

test('source quarter turns preserve shared bytes and declared bounds, including articulated body poses', async t => {
  const { assets, calls } = fixtures(t);
  const original = await assets.load(object, '/world/');
  const sourcePoint = new THREE.Vector3(-1, -2, -3);
  for (let turn = 0; turn < 4; turn++) {
    const placed = { ...object, mesh_quarter_turns: turn, size: turn % 2 ? [4, 2, 6] : [2, 4, 6] };
    const group = await assets.load(placed, '/world/');
    const bounds = new THREE.Box3().setFromObject(group);
    bounds.getSize(new THREE.Vector3()).toArray().forEach((value, i) => assert(Math.abs(value-placed.size[i]) < 1e-6));
    assert.equal(group.children[0].geometry, original.children[0].geometry);
    assert.equal(group.children[0].material, original.children[0].material);
    const actual = new THREE.Vector3(-.5, -.5, -.5).applyMatrix4(group.matrixWorld);
    const expected = sourcePoint.clone().applyAxisAngle(new THREE.Vector3(0,0,1), turn*Math.PI/2).add(new THREE.Vector3(...object.position));
    assert(actual.distanceTo(expected) < 1e-6);
  }
  const bodyRotation = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0), Math.PI/2);
  const articulated = await assets.load({ ...object, mesh_quarter_turns: 1, size: [4,2,6],
    quaternion: [bodyRotation.w, bodyRotation.x, bodyRotation.y, bodyRotation.z] }, '/world/');
  articulated.updateMatrixWorld(true);
  const actual = new THREE.Vector3(-.5,-.5,-.5).applyMatrix4(articulated.matrixWorld);
  const expected = sourcePoint.clone().applyAxisAngle(new THREE.Vector3(0,0,1), Math.PI/2)
    .applyQuaternion(bodyRotation).add(new THREE.Vector3(...object.position));
  assert(actual.distanceTo(expected) < 1e-6);
  assert.equal(calls.length, 4);
});

test('failed map loads do not dispose shared successful maps; explicit retry can recover', async t => {
  let fail = true;
  const { assets, calls, counts } = fixtures(t, url => {
    if (fail && url.endsWith('normal.png')) return new Response('', { status: 404 });
    return new Response(url.endsWith('.obj') ? source : png());
  });
  await assert.rejects(assets.load(object, '/world/'), /Texture unavailable/);
  assert.equal(counts.closed, 0);
  const colorOnly = await assets.load({ ...object, normal_texture_path: null, orm_texture_path: null }, '/world/');
  fail = false; assets.retryFailures();
  const complete = await assets.load(object, '/world/');
  assert.equal(colorOnly.children[0].material.map, complete.children[0].material.map);
  assert.equal(calls.filter(url => url.endsWith('color.png')).length, 1);
  assets.dispose(); assert.equal(counts.closed, 3);
});

test('disposing during pending decode aborts requests and releases late bitmaps', async t => {
  const { assets, counts } = fixtures(t);
  let finish, started;
  const decoded = new Promise(resolve => { started = resolve; });
  globalThis.createImageBitmap = () => { started(); return new Promise(resolve => { finish = resolve; }); };
  const pending = assets.load(object, '/world/');
  await decoded;
  assets.dispose();
  assert.equal(assets.controller.signal.aborted, true);
  finish({ width: 4, height: 4, close() { counts.closed++; } });
  await assert.rejects(pending, /cancelled|disposed/);
  assert.equal(counts.closed, 1);
  await assert.rejects(assets.load(object, '/world/'), /disposed/);
});

test('scene cache releases late successful resources once, without resurrecting a closed scene', async () => {
  const cache = new SceneResourceCache();
  let resolve, released = 0;
  const pending = cache.get('mesh', () => new Promise(done => { resolve = done; }), () => released++);
  await Promise.resolve(); cache.dispose(); resolve({});
  await assert.rejects(pending, /disposed/);
  cache.dispose(); assert.equal(released, 1);
});

test('unbundled or non-triangle OBJ input cannot become a shared display asset', async t => {
  const { assets, calls } = fixtures(t, () => new Response('v 0 0 0\nv 1 1 1\nl 1 2\n'));
  await assert.rejects(assets.load({ ...object, mesh_path: '../private.obj' }, '/world/'), /Unbundled/);
  assert.equal(calls.length, 0);
  await assert.rejects(assets.load(object, '/world/'), /triangle meshes/);
});

test('opaque source batches preserve selection and release only owned instance buffers', async t => {
  const { assets, counts } = fixtures(t), root = new THREE.Group();
  const input = Array.from({ length: 64 }, (_, i) => ({ ...object, id: `rack${i}`, position: [i*4+1, 1, 3] }));
  const frozen = JSON.stringify(input);
  for (const item of input) root.add(await assets.load(item, '/world/'));
  const original = root.children[0].children[0];
  let geometryDisposed = 0, materialDisposed = 0, instancesDisposed = 0;
  original.geometry.addEventListener('dispose', () => geometryDisposed++);
  original.material.addEventListener('dispose', () => materialDisposed++);
  const result = batchSourceMeshes(THREE, root, { cellSize: 1000 });
  assert.equal(result.stats.meshesBefore, 64); assert.equal(result.stats.meshesAfter, 1);
  const batch = result.objects[0];
  assert(batch.isInstancedMesh); assert.equal(batch.count, 64);
  assert.equal(batch.geometry, original.geometry); assert.equal(batch.material, original.material);
  assert.deepEqual(batch.userData.objects.map(item => item.id), input.map(item => item.id));
  const ray = new THREE.Raycaster(new THREE.Vector3(17*4+1,1,10), new THREE.Vector3(0,0,-1));
  const hit = ray.intersectObject(batch)[0];
  assert.equal(hit.instanceId, 17); assert.equal(hit.object.userData.objects[hit.instanceId].id, 'rack17');
  assert.equal(JSON.stringify(input), frozen);
  assert.equal(batchSourceMeshes(THREE, root).stats.batches, 0); // No double batching.
  batch.addEventListener('dispose', () => instancesDisposed++);
  disposeTree(root);
  assert.deepEqual([instancesDisposed, geometryDisposed, materialDisposed, counts.closed], [1,0,0,0]);
  assets.dispose();
  assert.deepEqual([geometryDisposed, materialDisposed, counts.closed], [1,1,3]);
});

test('batch matrices preserve nested transforms, nonuniform scale and quarter turns', async t => {
  const { assets } = fixtures(t), parent = new THREE.Group(), root = new THREE.Group();
  parent.position.set(-3,7,2); parent.rotation.z = .4; parent.add(root);
  root.position.set(4,-3,2); root.rotation.y = .6; root.scale.set(1.3,.8,1.1);
  for (let i=0; i<4; i++) root.add(await assets.load({ ...object, id: `part${i}`,
    position: [i+1,1,3], size: i%2 ? [8,1,3] : [1,8,3], mesh_quarter_turns: i }, '/world/'));
  root.updateWorldMatrix(true,true);
  const original = root.children.map(group => group.children[0].matrixWorld.clone());
  const result = batchSourceMeshes(THREE, root, { cellSize: 1000 });
  assert.equal(result.objects.length, 1);
  const batch = result.objects[0], instance = new THREE.Matrix4(), world = new THREE.Matrix4();
  for (let i=0; i<4; i++) {
    batch.getMatrixAt(i,instance); world.multiplyMatrices(batch.matrixWorld,instance);
    world.elements.forEach((value,j) => assert(Math.abs(value-original[i].elements[j]) < 1e-5));
  }
});

test('spatial cells and materials stay separate; blended hidden reflected and shadow variants retain rendering', async t => {
  const { assets } = fixtures(t), root = new THREE.Group();
  const groups = [];
  for (const [id,x,changes] of [
    ['a',1,{}], ['b',2,{}], ['c',17,{}], ['d',18,{}],
    ['glass1',3,{ surface: { ...surface, alpha_mode: 'BLEND' } }],
    ['glass2',4,{ surface: { ...surface, alpha_mode: 'BLEND' } }],
    ['tint1',5,{ color: [.2,.3,.4] }], ['tint2',6,{ color: [.2,.3,.4] }],
    ['hidden',1,{}], ['reflected',1,{}], ['shadow',1,{}],
  ]) {
    const group = await assets.load({ ...object, id, position: [x,1,3], ...changes }, '/world/');
    if (id === 'hidden') group.visible = false;
    if (id === 'reflected') group.scale.x *= -1;
    if (id === 'shadow') group.children[0].castShadow = false;
    root.add(group); groups.push(group);
  }
  const result = batchSourceMeshes(THREE, root);
  const batches = result.objects.filter(node => node.isInstancedMesh);
  assert.equal(batches.length, 3);
  assert.deepEqual(batches.map(b => b.userData.objects.map(o=>o.id)), [['a','b'],['c','d'],['tint1','tint2']]);
  const retained = result.objects.filter(node => !node.isInstancedMesh);
  assert.deepEqual(retained.map(n=>n.userData.object.id), ['glass1','glass2','hidden','reflected','shadow']);
  assert.equal(groups[8].visible, false);
  assert.equal(retained.at(-1).castShadow, false);
});

test('120 seven-part source fixtures reduce 840 mesh objects to seven batches in one cell', async t => {
  const { assets, calls } = fixtures(t), root = new THREE.Group();
  for (let i=0; i<120; i++) for (let part=0; part<7; part++) {
    root.add(await assets.load({ ...object, id: `carton${i}_part${part}`,
      mesh_path: `assets/part${part}.obj`, position: [i%12*.5,Math.floor(i/12)*.5,3] }, '/world/'));
  }
  const result = batchSourceMeshes(THREE, root);
  assert.equal(result.stats.meshesBefore, 840); assert.equal(result.stats.meshesAfter, 7);
  assert.equal(result.stats.batchedInstances, 840);
  assert(result.objects.every(node => node.isInstancedMesh && node.count === 120));
  assert.equal(calls.filter(path=>path.endsWith('.obj')).length, 7);
});

test('batch construction failure retains original scene and cleans new instance buffers', async t => {
  const { assets } = fixtures(t), root = new THREE.Group();
  for (const x of [1,2,17,18]) root.add(await assets.load({ ...object, position: [x,1,3] }, '/world/'));
  const original = root.children.slice();
  let built = 0, released = 0;
  class FailedBatch extends THREE.InstancedMesh {
    constructor(...args) {
      if (++built === 2) throw new Error('fixture allocation failure');
      super(...args); this.addEventListener('dispose', () => released++);
    }
  }
  assert.throws(() => batchSourceMeshes({ ...THREE, InstancedMesh: FailedBatch },root), /allocation failure/);
  assert.deepEqual(root.children,original); assert.equal(released,1);
  assert(original.every(group=>group.children.length === 1));
  assert.throws(() => batchSourceMeshes(THREE,root,{cellSize:0}), /limits/);
  const limited = batchSourceMeshes(THREE,root,{cellSize:1000,maxInstances:2});
  assert.equal(limited.stats.batches,0); assert.equal(limited.objects.length,4);
});

test('repeated missing sources make one request until an explicit retry', async t => {
  let missing = true;
  const { assets, calls } = fixtures(t, url => missing ? new Response('', { status: 404 })
    : new Response(url.endsWith('.obj') ? source : png()));
  for (let i = 0; i < 200; i++) {
    await assert.rejects(assets.load({ ...object, id: `copy${i}` }, '/world/'), /unavailable/);
  }
  assert.equal(calls.length, 1);
  missing = false;
  await assert.rejects(assets.load(object, '/world/'), /unavailable/);
  assert.equal(calls.length, 1);
  assets.retryFailures();
  const loaded = await assets.load(object, '/world/');
  assert.equal(loaded.children[0].userData.object.id, object.id);
  assert.equal(calls.length, 5);
});

test('failure bounds share resources, preserve pose and remain batchable and selectable', t => {
  const { assets } = fixtures(t), root = new THREE.Group();
  const rotation = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,0,1), Math.PI/2);
  const input = { ...object, id: 'unavailable', position: [2,3,4], size: [4,2,6], mesh_quarter_turns: 1,
    quaternion: [rotation.w, rotation.x, rotation.y, rotation.z] };
  const before = structuredClone(input);
  const first = assets.placeholder(input), second = assets.placeholder({ ...input, id: 'other' });
  root.add(first, second); root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(first), size = bounds.getSize(new THREE.Vector3()).toArray();
  size.forEach((v, i) => assert(Math.abs(v-[2,4,6][i]) < 1e-6));
  assert.deepEqual(bounds.getCenter(new THREE.Vector3()).toArray(), [2,3,4]);
  assert.deepEqual(input, before); assert.equal(first.material.wireframe, true);
  assert.equal(first.geometry, second.geometry); assert.equal(first.material, second.material);
  let geometry = 0, material = 0;
  first.geometry.addEventListener('dispose', () => geometry++); first.material.addEventListener('dispose', () => material++);
  const result = batchSourceMeshes(THREE, root);
  assert.equal(result.stats.batchedInstances, 2);
  assert.deepEqual(result.objects[0].userData.objects.map(o => o.id), ['unavailable', 'other']);
  disposeTree(root); assert.equal(geometry, 0); assert.equal(material, 0);
  assets.dispose(); assets.dispose(); assert.equal(geometry, 1); assert.equal(material, 1);
  assert.throws(() => assets.placeholder(input), /disposed/);
});

test('external request cancellation does not prevent later resource disposal', t => {
  const { assets } = fixtures(t);
  const marker = assets.placeholder(object);
  let released = 0;
  marker.geometry.addEventListener('dispose', () => released++);
  assets.controller.abort();
  assets.dispose(); assets.dispose();
  assert.equal(released, 1); assert.equal(assets.cache.closed, true);
});
