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
  'scene-resources.js', 'scene-materials.js', 'scene-appearance.js', 'scene-textures.js']) {
  const source = await readFile(new URL(`../src/robot_worlds/static/${name}`, import.meta.url), 'utf8');
  await writeFile(join(directory, name.split('/').at(-1)), source
    .replaceAll("from 'three'", "from './three.core.js'")
    .replaceAll("'./vendor/OBJLoader.js'", "'./OBJLoader.js'"));
}
const { SceneAssets } = await import(pathToFileURL(join(directory, 'scene-assets.js')));
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

function fixtures(t, respond) {
  const calls = [], counts = { closed: 0 };
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push(url);
    return respond ? respond(url, options) : new Response(url.endsWith('.obj') ? source : 'png fixture');
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
    return new Response(url.endsWith('.obj') ? source : 'png fixture');
  });
  await assert.rejects(assets.load(object, '/world/'), /Texture unavailable/);
  assert.equal(counts.closed, 0);
  const colorOnly = await assets.load({ ...object, normal_texture_path: null, orm_texture_path: null }, '/world/');
  fail = false;
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
