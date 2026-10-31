import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-shapes.js', import.meta.url));
const { geometryKey, closedProfile, revolvedGeometry } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

test('curved shapes batch only when both profile and physical fidelity match', () => {
  const bowl = {kind: 'revolved', revolved: {profile: [[0,-.5],[.5,-.5],[.5,.5],[.4,.5],[.4,-.4],[0,-.4]], segments: 32}};
  assert.equal(geometryKey(bowl), geometryKey(structuredClone(bowl)));
  assert.notEqual(geometryKey(bowl), geometryKey({...bowl, revolved: {...bowl.revolved, segments: 64}}));
  assert.notEqual(geometryKey(bowl), geometryKey({...bowl, revolved: {...bowl.revolved, profile: [[0,-.5],[.5,-.5],[.5,.5]]}}));
});

test('both windings produce the same closed material surface, without filling the cavity', () => {
  const profile = [[0,-.5],[.5,-.5],[.5,.5],[.4,.5],[.4,-.4],[0,-.4]];
  assert.deepEqual(closedProfile(profile), closedProfile([...profile].reverse()));
  const closed = closedProfile(profile);
  assert.deepEqual(closed.at(-1), closed[0]);
  assert.equal(closed.length, 7);
  assert.deepEqual(closed[3], [.4,.5]);
  assert.equal(profile.length, 6);
});

test('actual viewer geometry has an open cavity, inner bottom and solid rim', async () => {
  const core = await readFile(new URL('../src/robot_worlds/static/vendor/three.core.js', import.meta.url));
  const THREE = await import(`data:text/javascript;base64,${core.toString('base64')}`);
  const shape = {profile: [[0,-.5],[.32,-.5],[.5,.5],[.45,.5],[.28,-.4],[0,-.4]], segments: 32};
  const geometry = revolvedGeometry(THREE, shape), material = new THREE.MeshBasicMaterial({side: THREE.DoubleSide});
  const mesh = new THREE.Mesh(geometry, material);
  mesh.updateMatrixWorld();
  const ray = new THREE.Raycaster(new THREE.Vector3(.01,0,1), new THREE.Vector3(0,0,-1));
  assert(Math.abs(ray.intersectObject(mesh)[0].point.z + .4) < 1e-6);
  ray.set(new THREE.Vector3(.475,0,1), new THREE.Vector3(0,0,-1));
  assert(Math.abs(ray.intersectObject(mesh)[0].point.z - .5) < 1e-6);
  geometry.dispose(); material.dispose();
});
