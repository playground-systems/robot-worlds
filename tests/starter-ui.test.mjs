import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

// Run the actual app handlers against a small DOM and API fixture. No browser,
// rendering context, native simulator, generation endpoint or model is started.
const source = (await readFile(new URL('../src/robot_worlds/static/app.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gm, '').replace('\nstart();', '\n');
const html = await readFile(new URL('../src/robot_worlds/static/index.html', import.meta.url), 'utf8');
const catalog = ['warehouse', 'house', 'hospital', 'bookstore', 'fixture-kitchen', 'fixture-factory', 'fixture-living-room'].map(id => ({
  id, name: `Fixture ${id}`, description: 'Explicitly fake UI fixture', available: true,
  preview_available: true, license: 'MIT-0', source_url: `https://example.invalid/${id}`,
  bounds: [24, 18, 5], components: 2,
  task_count: { 'fixture-kitchen': 16, 'fixture-factory': 15, 'fixture-living-room': 13 }[id],
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

function fixture({ add, status = 'completed', error = null, missing = false, compatibility = false,
  revalidate, revalidationStatus = 'completed', revalidationError = null, childValidation = 'ready' } = {}) {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const shell = new Element(), created = [], requests = [], timers = [], views = [], history = [];
  const worldId = 'c'.repeat(64), jobId = 'a'.repeat(32), childId = 'd'.repeat(64), validationJob = 'b'.repeat(32);
  const world = {
    spec: { name: 'Imported source layout', description: 'A source geometry fixture', task: 'Reach the marked square',
      bounds: [24, 18, 5], robot: 'tiago++',
      objects: [{ id: 'upstream-rack', label: 'Original rack', kind: 'box', size: [1, 1, 2], position: [4, 0, 1] }],
      regions: [], rubric: [], assumptions: [],
    },
    manifest: { id: worldId, validation: { status: 'ready', checks: [{ check: 'render', passed: true }] } },
    runtime_compatibility: { requires_native_revalidation: compatibility },
  };
  const child = structuredClone(world);
  child.manifest.id = childId; child.manifest.parent_id = worldId;
  child.manifest.validation.status = childValidation;
  child.runtime_compatibility.requires_native_revalidation = false;
  let validationSubmitted = false;
  const entries = catalog.map((entry, index) => ({ ...entry, available: !missing || index !== 1 }));
  const inspectTabs = [...html.matchAll(/<button data-inspect="([^"]+)"/g)].map(match => {
    const node = new Element('button'); node.dataset.inspect = match[1]; return node;
  });
  class Viewer {
    constructor() { this.shellCount = 0; }
    setSpec(spec, assetBase) { views.push({ spec, assetBase }); }
    select() {}
    setActive() {}
  }
  const response = (data, ok = true) => ({ ok, json: async () => structuredClone(data) });
  const context = vm.createContext({
    document: {
      getElementById: id => nodes.get(id), querySelector: () => shell,
      querySelectorAll: selector => {
        if (selector === '[data-inspect]') return inspectTabs;
        if (selector === '.starter-add' || selector === '.validate-local') {
          return created.filter(node => node.className?.split(' ').includes(selector.slice(1)));
        }
        return [];
      },
      createElement: tag => { const node = new Element(tag); created.push(node); return node; },
    },
    window: { innerWidth: 1400, addEventListener() {} }, WorldViewer: Viewer, URLSearchParams,
    location: { search: '' }, history: { replaceState: (_, __, path) => history.push(path) },
    setTimeout: (callback, delay) => timers.push({ callback, delay }), console,
    fetch: async (path, options = {}) => {
      requests.push({ path, method: options.method || 'GET', body: options.body });
      if (path.endsWith('/add')) return add ? add(path, options) : response({ id: jobId, status: 'queued', kind: 'starter' });
      if (path === `/api/worlds/${worldId}/revalidate`) {
        validationSubmitted = true;
        return revalidate ? revalidate(path, options) : response({ id: validationJob, status: 'queued', kind: 'revalidate' });
      }
      if (path === `/api/jobs/${validationJob}`) return response({
        id: validationJob, kind: 'revalidate', status: revalidationStatus, error: revalidationError,
        world_id: revalidationStatus === 'completed' ? childId : undefined,
        events: [{ type: 'validation_started', message: 'Running mocked validation; no native or model calls' }],
      });
      if (path === `/api/jobs/${jobId}`) return response({
        id: jobId, kind: 'starter', status, error,
        world_id: status === 'completed' ? worldId : undefined,
        events: [{ type: 'starter_import', message: 'Verifying source bundle' }],
      });
      if (path === '/api/worlds') return response([
        ...(status === 'completed' ? [{ id: worldId, name: world.spec.name, validation: { status: 'ready' } }] : []),
        ...(validationSubmitted && revalidationStatus === 'completed' ? [{ id: childId, name: child.spec.name, validation: { status: childValidation } }] : []),
      ]);
      if (path === '/api/runs' || path === '/api/references') return response([]);
      if (path === '/api/starters') return response(entries);
      if (path === `/api/worlds/${worldId}`) return response(world);
      if (path === `/api/worlds/${childId}`) return response(child);
      throw new Error(`Unexpected request: ${path}`);
    },
  });
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  context.entries = entries;
  nodes.get('report-area').hidden = true;
  run('state.key=false;state.starters=entries;renderStarters();controls();');
  const buttons = () => created.filter(node => node.className?.split(' ').includes('starter-add'));
  const validateButton = () => nodes.get('inspector-body').children.find(node => node.className?.split(' ').includes('validate-local'));
  return { nodes, run, requests, timers, views, history, world, worldId, childId, buttons, validateButton, inspectTabs };
}

async function settle() {
  // Handlers intentionally launch job polling without awaiting its completion.
  // Drain that Promise chain without advancing fake polling timers.
  for (let index = 0; index < 25; index++) await Promise.resolve();
}

test('starter cards distinguish manipulation task suites from static references', () => {
  const { nodes } = fixture();
  const text = node => [node.textContent || '', ...node.children.map(text)].join(' ');
  for (const [index, card] of nodes.get('starter-cards').children.entries()) {
    const expected = catalog[index].task_count;
    assert(text(card).includes(expected ? `${expected} task variants` : 'Static reference'));
  }
});

test('additive starter cards can all be chosen without a key or a generation form', async () => {
  const { nodes, buttons, requests, run } = fixture({ status: 'queued' });
  assert.equal(nodes.get('generate-button').disabled, true);
  assert.equal(nodes.get('evaluate-button').disabled, true);
  assert.equal(nodes.get('open-starters').disabled, false);
  assert.equal(nodes.get('empty-starters').disabled, false);
  assert.equal(buttons().length, catalog.length);
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

test('an appended manipulation starter uses the same keyless import flow', async () => {
  const { buttons, requests, run } = fixture({ status: 'queued' });
  await buttons().at(-1).click();
  assert.equal(requests[0].path, '/api/starters/fixture-living-room/add');
  assert.equal(run('state.jobId'), 'a'.repeat(32));
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
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

test('an incompatible starter offers keyless validation and opens its compatible child', async () => {
  const { run, worldId, childId, nodes, validateButton, inspectTabs, requests, views, history } = fixture({ compatibility: true });
  await run(`openWorld('${worldId}')`);
  assert.equal(run('state.tab'), 'validation');
  assert.equal(inspectTabs.find(tab => tab.dataset.inspect === 'validation').classList.contains('active'), true);
  run('state.key=true;controls();');
  assert.equal(nodes.get('evaluate-button').disabled, true); // A key cannot bypass the mismatch.
  run('state.key=false;controls();');
  assert.equal(validateButton().disabled, false);
  await validateButton().click();
  await settle();
  assert.equal(run('state.worldId'), childId);
  assert.equal(run('state.world.manifest.parent_id'), worldId);
  assert.equal(run('state.world.runtime_compatibility.requires_native_revalidation'), false);
  assert.equal(run('state.jobId'), null);
  assert.equal(views.at(-1).assetBase, `/api/worlds/${childId}/files/`);
  assert.equal(history.at(-1), `/?world=${childId}`);
  const mutations = requests.filter(request => request.method === 'POST');
  assert.equal(mutations.length, 1);
  assert.equal(mutations[0].path, `/api/worlds/${worldId}/revalidate`);
  assert.deepEqual(JSON.parse(mutations[0].body), {});
  assert.equal(nodes.get('evaluate-button').disabled, true); // Local validation does not invent a key.
  run('state.key=true;controls();');
  assert.equal(nodes.get('evaluate-button').disabled, false);
});

test('pending local validation disables its button and a failed request preserves the parent', async () => {
  let reject;
  const { run, worldId, nodes, validateButton, requests } = fixture({ compatibility: true,
    revalidate: () => new Promise((_, fail) => { reject = fail; }),
  });
  await run(`openWorld('${worldId}')`);
  const button = validateButton(), click = button.click();
  assert.equal(run('state.submitting'), true);
  assert.equal(button.disabled, true);
  await button.click();
  reject(new Error('Mocked local validation queue unavailable'));
  await click;
  assert.equal(run('state.submitting'), false);
  assert.equal(run('state.worldId'), worldId);
  assert.equal(button.disabled, false);
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
  assert.match(nodes.get('notice').textContent, /validation queue unavailable/);
});

test('completed local validation with unresolved checks shows the saved child without enabling evaluation', async () => {
  const { run, worldId, childId, validateButton, nodes } = fixture({ compatibility: true, childValidation: 'unvalidated' });
  await run(`openWorld('${worldId}')`);
  await validateButton().click();
  await settle();
  assert.equal(run('state.worldId'), childId);
  assert.equal(run('state.tab'), 'validation');
  assert.equal(run('state.world.manifest.validation.status'), 'unvalidated');
  run('state.key=true;controls();');
  assert.equal(nodes.get('evaluate-button').disabled, true);
  assert.equal(nodes.get('world-status').textContent, 'unvalidated');
  assert.match(nodes.get('notice').textContent, /unresolved checks/);
});
