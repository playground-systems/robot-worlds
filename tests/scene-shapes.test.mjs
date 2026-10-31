import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-shapes.js', import.meta.url));
const { geometryKey, closedProfile } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

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
