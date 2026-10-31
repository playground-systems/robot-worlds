import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const resourceSource = await readFile(new URL('../src/robot_worlds/static/scene-resources.js', import.meta.url));
const resources = await import(`data:text/javascript;base64,${resourceSource.toString('base64')}`);
const code = (await readFile(new URL('../src/robot_worlds/static/scene.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gm, '').replace('export class WorldViewer', 'globalThis.WorldViewer = class WorldViewer');

function fixture() {
  const calls = [], document = new EventTarget(), context = vm.createContext({
    ...resources, document, AbortController, cancelAnimationFrame: id => calls.push(['cancel', id]),
    requestAnimationFrame: () => { calls.push(['frame']); return 17; },
  });
  vm.runInContext(code, context);
  const viewer = Object.create(context.WorldViewer.prototype), canvas = new EventTarget(), controls = new EventTarget();
  controls.dispose = () => calls.push(['controls']);
  Object.assign(viewer, { active: true, disposed: false, animation: 4, loads: new Set(), pendingLoads: [], canvas, controls,
    resizeObserver: { disconnect: () => calls.push(['observer']) }, labels: { replaceChildren: () => calls.push(['labels']) },
    scene: { traverse: callback => callback({}), clear: () => calls.push(['scene']) },
    renderer: { shadowMap: {}, dispose: () => calls.push(['renderer']), forceContextLoss: () => calls.push(['context']) },
    onPointerDown: () => calls.push(['pointer']), onPointerUp() {}, onVisibility: () => calls.push(['visibility']), onControlChange() {}, onControlStart() {},
  });
  canvas.addEventListener('pointerdown', viewer.onPointerDown); document.addEventListener('visibilitychange', viewer.onVisibility);
  return { viewer, calls, canvas, document };
}

test('disposing a viewer cancels work, aborts assets and removes event listeners exactly once', () => {
  const { viewer, calls, canvas, document } = fixture(), request = new AbortController(); viewer.loads.add(request);
  viewer.assets = { dispose: () => request.abort() };
  viewer.pendingLoads.push(['later asset']); viewer.dispose(); viewer.dispose();
  canvas.dispatchEvent(new Event('pointerdown')); document.dispatchEvent(new Event('visibilitychange')); viewer.invalidate();
  assert(request.signal.aborted); assert.equal(viewer.pendingLoads.length, 0);
  assert.deepEqual(calls, [['cancel', 4], ['observer'], ['controls'], ['scene'], ['labels'], ['renderer'], ['context']]);
  assert.equal(viewer.disposed, true);
});

test('late results from a replaced scene cannot attach objects to the current scene', async () => {
  const { viewer } = fixture();
  const controller = new AbortController();
  let resolve, attached = 0;
  viewer.assets = { controller, dispose: () => controller.abort(), load: () => new Promise(done => { resolve = done; }) };
  viewer.group = { add: () => attached++ }; viewer.objects = []; viewer.loadGeneration = 1;
  const pending = viewer.loadOBJ({ id: 'old' }, '/old/', 1);
  assert.equal(viewer.loads.size, 1);
  viewer.abortLoads(); viewer.loadGeneration = 2;
  resolve({ traverse(callback) { callback({ isMesh: true }); } });
  await pending;
  assert(controller.signal.aborted);
  assert.equal(attached, 0); assert.equal(viewer.objects.length, 0); assert.equal(viewer.loads.size, 0);
});

test('inactive view pauses frames and queued mesh starts; reactivation caps concurrent loads at four', () => {
  const { viewer, calls } = fixture(); viewer.loadOBJ = value => viewer.loads.add(value);
  viewer.pendingLoads = [[1], [2], [3], [4], [5], [6]]; viewer.setActive(false); viewer.invalidate(); viewer.pumpLoads();
  assert.equal(viewer.loads.size, 0); assert.deepEqual(calls, [['cancel', 4]]);
  viewer.setActive(true); assert.equal(viewer.loads.size, 4); assert.equal(viewer.pendingLoads.length, 2);
  assert.deepEqual(calls, [['cancel', 4], ['frame']]);
});

test('cutaway only changes renderer visibility and leaves physical object data intact', () => {
  const { viewer } = fixture(), object = { id: 'wall', position: [1, 2, 3], size: [1, 2, 3] };
  viewer.shellNodes = [{ visible: true, userData: { objects: [object] } }]; viewer.reference = false;
  const frozen = structuredClone(object); viewer.setCutaway(true);
  assert.equal(viewer.shellNodes[0].visible, false); assert.deepEqual(object, frozen);
  viewer.setCutaway(false); assert.equal(viewer.shellNodes[0].visible, true);
});
