import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/robot_worlds/static/scene-inspection.js', import.meta.url));
const { boundsCorners, objectBounds, perspectiveFit, sceneBounds, isInspectionShell, initialCutaway, hideInspectionShell } = await import(`data:text/javascript;base64,${source.toString('base64')}`);
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

test('cutaway retains far boundary walls and follows the camera without changing geometry', () => {
  const bounds=[20,16,5], camera=[12,-12,10];
  const east={size:[.2,16,5],position:[10,0,2.5]}, west={...east,position:[-10,0,2.5]};
  const south={size:[20,.2,5],position:[0,-8,2.5]}, north={...south,position:[0,8,2.5]};
  assert.equal(hideInspectionShell(east,bounds,camera),true);
  assert.equal(hideInspectionShell(south,bounds,camera),true);
  assert.equal(hideInspectionShell(west,bounds,camera),false);
  assert.equal(hideInspectionShell(north,bounds,camera),false);
  assert.equal(hideInspectionShell(west,bounds,[-12,12,10]),true);
  assert.equal(hideInspectionShell({size:[20,16,.2],position:[0,0,5]},bounds,camera),true);
 });

test('an inset facade is still an enclosure while its articulated children stay visible', () => {
  const facade = {id:'south_center',label:'Facade between receiving and shipping portals',kind:'box',position:[0,-5.9,2.5],size:[8.4,.2,5]};
  assert.equal(isInspectionShell(facade,[20,16,5]),true);
  assert.equal(hideInspectionShell(facade,[20,16,5],[12,-12,10]),true);
  assert.equal(isInspectionShell({...facade,parent_id:'door'},[20,16,5]),false);
 });

function sourceCeiling() {
  const root = { id: 'source_collision', kind: 'mesh', collision: true, visible: false, movable: false };
  const ceiling = { id: 'source_visual', label: 'aws_robomaker_hospital_floor_01_ceiling',
    kind: 'mesh', collision: false, visible: true, parent_id: root.id,
    position: [0, 0, 3], size: [25, 56, .08], mesh_path: 'files/original.obj' };
  return { root, ceiling, spec: { bounds: [27, 58, 5], objects: [root, ceiling] } };
}

test('fixed imported ceiling appearances open the interior while preserving source data', () => {
  const { root, ceiling, spec } = sourceCeiling(), original = structuredClone(spec);
  assert.equal(isInspectionShell(ceiling, spec.bounds, spec.objects), true);
  assert.equal(initialCutaway(spec), true);
  assert.equal(hideInspectionShell(ceiling, spec.bounds, [30, -40, 35]), true);
  assert.equal(hideInspectionShell(ceiling, spec.bounds, [-30, 40, 35]), true);
  assert.equal(isInspectionShell(root, spec.bounds, spec.objects), false);
  assert.deepEqual(spec, original);
});

test('imported ceiling inference requires the complete fixed ancestry', () => {
  const { root, ceiling, spec } = sourceCeiling();
  const frame = { id: 'fixed_frame', parent_id: root.id, movable: false };
  const child = { ...ceiling, parent_id: frame.id };
  assert.equal(isInspectionShell(child, spec.bounds, [child, frame, root]), true);
  for (const change of [{ movable: true }, { joint: { type: 'hinge' } }]) {
    assert.equal(isInspectionShell(child, spec.bounds, [child, frame, { ...root, ...change }]), false);
    assert.equal(initialCutaway({ ...spec, objects: [child, { ...frame, ...change }, root] }), false);
    assert.equal(isInspectionShell({ ...child, ...change }, spec.bounds, [child, frame, root]), false);
  }
  assert.equal(isInspectionShell(ceiling, spec.bounds), false);
  assert.equal(isInspectionShell(ceiling, spec.bounds, [ceiling]), false);
  assert.equal(isInspectionShell(child, spec.bounds, [child, frame]), false);
  assert.equal(isInspectionShell(child, spec.bounds, [child, frame, { ...root, parent_id: frame.id }]), false);
  assert.equal(isInspectionShell({ ...ceiling, parent_id: ceiling.id }, spec.bounds, [root, ceiling]), false);
  assert.equal(isInspectionShell(ceiling, spec.bounds, [root, { ...root, movable: true }, ceiling]), false);
});

test('source cutaway excludes arbitrary props, physical meshes and broad wall meshes', () => {
  const { root, ceiling, spec } = sourceCeiling();
  for (const change of [
    { id: 'work_surface', label: 'Conference table' },
    { collision: true },
    { parent_id: undefined },
    { size: [25, .2, 3] },
    { size: [25, 56, 3] },
    { position: [0, 0, 0] },
    { size: [.4, .4, .2] },
  ]) {
    const candidate = { ...ceiling, ...change };
    assert.equal(isInspectionShell(candidate, spec.bounds, [root, candidate]), false);
    assert.equal(initialCutaway({ ...spec, objects: [root, candidate] }), false);
  }
  assert.equal(initialCutaway({ ...spec, objects: [root, { ...ceiling, visible: false }] }), false);
});
