import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-inspection.js', import.meta.url));
const { boundsCorners, objectBounds, perspectiveFit, sceneBounds, isInspectionShell, initialCutaway } = await import(`data:text/javascript;base64,${source.toString('base64')}`);
const dot = (a, b) => a.reduce((sum, value, i) => sum+value*b[i], 0);

for (const aspect of [.22, .5, 1, 1.8, 3.5]) {
  for (const bounds of [
    { min: [-50, -30, 0], max: [50, 30, 6] },
    { min: [-3, -40, 0], max: [3, 40, 15] },
    { min: [7, -2, 1], max: [7.1, -1.9, 1.2] },
  ]) {
    test(`all corners fit both frustum axes for aspect ${aspect} and ${bounds.max}`, () => {
      for (const direction of [[.8, -.9, .85], [0, -.001, 1]]) for (const fov of [30, 42]) {
        const fit = perspectiveFit(bounds, aspect, fov, direction), tangent = Math.tan(fov*Math.PI/360);
        for (const point of boundsCorners(bounds)) {
          const relative = point.map((value, i) => value-fit.position[i]);
          const depth = -dot(relative, fit.backward);
          assert(depth > fit.near && depth < fit.far);
          assert(Math.abs(dot(relative, fit.right)/(depth*tangent*aspect)) < .85);
          assert(Math.abs(dot(relative, fit.up)/(depth*tangent)) < .85);
        }
      }
    });
  }
}

test('rotated/off-centre declared objects extend the framing volume without mutating spec', () => {
  const object = { id: 'beam', position: [9, 0, 4], size: [6, 2, 2], yaw: Math.PI/2 };
  const spec = { bounds: [8, 6, 3], objects: [object] }, original = structuredClone(spec);
  const bounds = objectBounds(object), scene = sceneBounds(spec);
  assert(Math.abs(bounds.min[0]-8) < 1e-10);
  assert(Math.abs(bounds.max[1]-3) < 1e-10);
  assert.equal(scene.max[2], 5); assert.equal(scene.max[0], 10);
  assert.deepEqual(spec, original);
});

test('cutaway identifies shell candidates but excludes machines, moving or articulated walls', () => {
  const wall = { id: 'west_wall', kind: 'box', position: [-5, 0, 1.5], size: [.2, 8, 3], movable: false };
  assert.equal(isInspectionShell(wall, [10, 8, 3]), true);
  assert.equal(isInspectionShell({ ...wall, id: 'unknown_boundary' }, [10, 8, 3]), true);
  assert.equal(isInspectionShell({ ...wall, movable: true }, [10, 8, 3]), false);
  assert.equal(isInspectionShell({ ...wall, joint: { type: 'hinge' } }, [10, 8, 3]), false);
  assert.equal(isInspectionShell({ ...wall, parent_id: 'machine' }, [10, 8, 3]), false);
  assert.equal(isInspectionShell({ ...wall, kind: 'mesh' }, [10, 8, 3]), false);
  assert.equal(isInspectionShell({ ...wall, id: 'cabinet', position: [0, 0, 1] }, [10, 8, 3]), false);
  assert.equal(isInspectionShell({ id: 'roof', kind: 'box', size: [10, 8, .2], position: [0, 0, 3] }, [10, 8, 3]), true);
  assert.equal(isInspectionShell({ id: 'table', kind: 'box', size: [3, 3, .2], position: [0, 0, 1.5] }, [10, 8, 3]), false);
});

test('invalid viewport parameters fail explicitly rather than moving the camera to NaN', () => {
  assert.throws(() => perspectiveFit({ min: [0, 0, 0], max: [1, 1, 1] }, 0));
  assert.throws(() => perspectiveFit({ min: [0, 0, 0], max: [1, 1, 1] }, 1, 180));
});

test('roofed scenes open to their interior without hiding ordinary table or machine geometry', () => {
  const roof = { id: 'roof', kind: 'box', size: [20, 16, .2], position: [0, 0, 5] };
  const spec = { bounds: [20, 16, 5], objects: [roof] }, before = structuredClone(spec);
  assert.equal(initialCutaway(spec), true);
  assert.deepEqual(spec, before);
  assert.equal(initialCutaway({ ...spec, objects: [{ ...roof, id: 'table', position: [0, 0, 1.5] }] }), false);
  assert.equal(initialCutaway({ ...spec, objects: [{ ...roof, visible: false }] }), false);
  const beam = { id: 'roof_beam_1', kind: 'box', size: [19, .2, .2], position: [0, 0, 4.7] };
  assert.equal(isInspectionShell(beam, spec.bounds), true);
  assert.equal(isInspectionShell({ ...beam, position: [0, 0, 1] }, spec.bounds), false);
  assert.equal(isInspectionShell({ ...beam, parent_id: 'machine' }, spec.bounds), false);
});
