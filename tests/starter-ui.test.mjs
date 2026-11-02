import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

// Run the actual app handlers against a small DOM and API fixture. No browser,
// rendering context, native simulator, generation endpoint or model is started.
const source = (await readFile(new URL('../src/robot_worlds/static/app.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gm, '').replace('\nstart();', '\n');
const html = await readFile(new URL('../src/robot_worlds/static/index.html', import.meta.url), 'utf8');
const catalog = ['warehouse', 'house', 'hospital', 'bookstore'].map(id => ({
  id, name: `Fixture ${id}`, description: 'Explicitly fake UI fixture', available: true,
  preview_available: true, license: 'MIT-0', source_url: `https://example.invalid/${id}`,
  bounds: [24, 18, 5], components: 2,
}));

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.dataset = {};
    this.value = ''; this.hidden = false; this.disabled = false; this.open = false;
    this.style = {}; this.firstChild = { textContent: '' };
    const classes = new Set();
    this.classList = {
      add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
    };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attrs[key] = value; }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { if (!this.disabled) return this.onclick?.(); }
  set innerHTML(value) { throw new Error('Untrusted markup must not be assigned'); }
}

function fixture({ add, status = 'completed', error = null, missing = false } = {}) {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const shell = new Element(), created = [], requests = [], timers = [], views = [], history = [];
  const worldId = 'c'.repeat(64), jobId = 'a'.repeat(32);
  const world = {
    spec: { name: 'Imported source layout', description: 'A source geometry fixture', task: 'Reach the marked square',
      bounds: [24, 18, 5], robot: 'tiago++',
      objects: [{ id: 'upstream-rack', label: 'Original rack', kind: 'box', size: [1, 1, 2], position: [4, 0, 1] }],
      regions: [], rubric: [], assumptions: [],
    },
    manifest: { id: worldId, validation: { status: 'ready', checks: [{ check: 'render', passed: true }] } },
  };
  const entries = catalog.map((entry, index) => ({ ...entry, available: !missing || index !== 1 }));
  class Viewer {
    constructor() { this.shellCount = 0; }
    setSpec(spec, assetBase) { views.push({ spec, assetBase }); }
    select() {}
    setActive() {}
  }
  const response = (data, ok = true) => ({ ok, json: async () => data });
  const context = vm.createContext({
    document: {
      getElementById: id => nodes.get(id), querySelector: () => shell,
      querySelectorAll: selector => selector === '.starter-add'
        ? created.filter(node => node.className?.split(' ').includes('starter-add')) : [],
      createElement: tag => { const node = new Element(tag); created.push(node); return node; },
    },
    window: { innerWidth: 1400, addEventListener() {} }, WorldViewer: Viewer, URLSearchParams,
    location: { search: '' }, history: { replaceState: (_, __, path) => history.push(path) },
    setTimeout: (callback, delay) => timers.push({ callback, delay }), console,
    fetch: async (path, options = {}) => {
      requests.push({ path, method: options.method || 'GET', body: options.body });
      if (path.endsWith('/add')) return add ? add(path, options) : response({ id: jobId, status: 'queued', kind: 'starter' });
      if (path === `/api/jobs/${jobId}`) return response({
        id: jobId, kind: 'starter', status, error,
        world_id: status === 'completed' ? worldId : undefined,
        events: [{ type: 'starter_import', message: 'Verifying source bundle' }],
      });
      if (path === '/api/worlds') return response(status === 'completed' ? [{ id: worldId, name: world.spec.name, validation: { status: 'ready' } }] : []);
      if (path === '/api/runs' || path === '/api/references') return response([]);
      if (path === '/api/starters') return response(entries);
      if (path === `/api/worlds/${worldId}`) return response(world);
      throw new Error(`Unexpected request: ${path}`);
    },
  });
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  context.entries = entries;
  nodes.get('report-area').hidden = true;
  run('state.key=false;state.starters=entries;renderStarters();controls();');
  const buttons = () => created.filter(node => node.className?.split(' ').includes('starter-add'));
  return { nodes, run, requests, timers, views, history, world, worldId, buttons };
}

async function settle() {
  // Handlers intentionally launch job polling without awaiting its completion.
  // Drain that Promise chain without advancing fake polling timers.
  for (let index = 0; index < 25; index++) await Promise.resolve();
}

test('all four starter cards can be chosen without a key or a generation form', async () => {
  const { nodes, buttons, requests, run } = fixture({ status: 'queued' });
  assert.equal(nodes.get('generate-button').disabled, true);
  assert.equal(nodes.get('evaluate-button').disabled, true);
  assert.equal(nodes.get('open-starters').disabled, false);
  assert.equal(nodes.get('empty-starters').disabled, false);
  assert.equal(buttons().length, 4);
  assert(buttons().every(button => !button.disabled));
  assert.deepEqual(buttons().map(button => button.attrs['aria-label']), catalog.map(entry => `Add ${entry.name} to workspace`));
  nodes.get('empty-starters').click();
  assert.equal(nodes.get('starter-dialog').open, true);
  await buttons()[0].click();
  assert.equal(nodes.get('starter-dialog').open, false);
  assert.equal(run('state.jobId'), 'a'.repeat(32));
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
  assert.equal(requests[0].path, '/api/starters/warehouse/add');
  assert.deepEqual(JSON.parse(requests[0].body), {});
  assert(requests.every(request => !request.path.includes('/generate') && !request.path.includes('/evaluate')));
});

test('a completed starter job opens its full source scene and assets in the layout viewer', async () => {
  const { buttons, requests, views, nodes, worldId, world, history, run } = fixture();
  await buttons()[2].click();
  await settle();
  assert.equal(run('state.jobId'), null);
  assert.equal(run('state.worldId'), worldId);
  assert.equal(nodes.get('world-title').textContent, world.spec.name);
  assert.equal(nodes.get('empty-state').hidden, true);
  assert.equal(nodes.get('scene-canvas').hidden, false);
  assert.equal(nodes.get('work-area').hidden, false);
  assert.equal(views.length, 1);
  assert.deepEqual(views[0].spec, world.spec);
  assert.equal(views[0].assetBase, `/api/worlds/${worldId}/files/`);
  assert.equal(history.at(-1), `/?world=${worldId}`);
  assert(requests.some(request => request.path === `/api/worlds/${worldId}`));
  assert.equal(nodes.get('evaluate-button').disabled, true); // Import did not invent a key.
});

test('a slow add request suppresses duplicate submissions and restores controls after failure', async () => {
  let reject;
  const { buttons, nodes, requests, run } = fixture({ add: () => new Promise((_, fail) => { reject = fail; }) });
  nodes.get('open-starters').click();
  const click = buttons()[0].click();
  assert.equal(run('state.submitting'), true);
  assert(buttons().every(button => button.disabled));
  await buttons()[1].click();
  assert.equal(requests.length, 1);
  reject(new Error('Bundle is not installed'));
  await click;
  assert.equal(run('state.submitting'), false);
  assert.equal(nodes.get('starter-dialog').open, true);
  assert.equal(nodes.get('starter-error').hidden, false);
  assert.match(nodes.get('starter-error').textContent, /Bundle is not installed/);
  assert(buttons().every(button => !button.disabled));
});

test('uninstalled scenes remain unavailable while installed scenes and dialog close work', () => {
  const { buttons, nodes, requests } = fixture({ missing: true });
  assert.equal(buttons()[1].disabled, true);
  assert.equal(buttons()[1].textContent, 'Not installed');
  buttons()[1].click();
  assert.equal(requests.length, 0);
  nodes.get('open-starters').click();
  nodes.get('close-starters').click();
  assert.equal(nodes.get('starter-dialog').open, false);
});

test('closing the picker while a request fails keeps the error visible', async () => {
  let reject;
  const { buttons, nodes } = fixture({ add: () => new Promise((_, fail) => { reject = fail; }) });
  nodes.get('open-starters').click();
  const click = buttons()[0].click();
  nodes.get('close-starters').click();
  reject(new Error('Cannot read starter bundle'));
  await click;
  assert.equal(nodes.get('starter-dialog').open, false);
  assert.equal(nodes.get('notice').hidden, false);
  assert.match(nodes.get('notice').textContent, /Cannot read starter bundle/);
  assert(buttons().every(button => !button.disabled));
});

test('a failed import leaves the existing scene intact and shows the actual error', async () => {
  const { buttons, run, views, nodes } = fixture({ status: 'failed', error: { message: 'Bundle checksum mismatch' } });
  run("state.worldId='existing';state.world={spec:{name:'Existing scene',objects:[]},manifest:{validation:{status:'ready'}}};");
  await buttons()[0].click();
  await settle();
  assert.equal(run('state.worldId'), 'existing');
  assert.equal(run('state.jobId'), null);
  assert.equal(views.length, 0);
  assert.equal(nodes.get('notice').hidden, false);
  assert.match(nodes.get('notice').textContent, /Bundle checksum mismatch/);
  assert(buttons().every(button => !button.disabled));
});
