import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

// Execute the real workbench handlers with an API/DOM fixture, without a browser,
// simulator, worker process or model call.
const source = (await readFile(new URL('../src/robot_worlds/static/app.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gm, '').replace('\nstart();', '\n');
const html = await readFile(new URL('../src/robot_worlds/static/index.html', import.meta.url), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.dataset = {};
    this.value = ''; this.hidden = false; this.disabled = false; this.style = {};
    this.firstChild = { textContent: '' }; const classes = new Set();
    this.classList = {
      add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
    };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attrs[key] = value; }
  focus() {}
  set innerHTML(value) { throw new Error('Untrusted task text must not become markup'); }
}

function fixture({ submit, status = 'completed', error = null, catalog = true } = {}) {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const created = [], requests = [], views = [], timers = [], shell = new Element();
  const worldId = 'a'.repeat(64), childId = 'b'.repeat(64), jobId = 'c'.repeat(32);
  const tasks = Array.from({ length: 12 }, (_, index) => ({
    id: `task${index}`, name: index === 1 ? '<script>Carry the part</script>' : `Interaction ${index + 1}`,
    task: `Perform interaction ${index + 1}`,
    rubric: [{ id: 'hold', description: 'Hold the part', predicate: 'holding', object_id: 'part' }],
  }));
  const world = {
    spec: { name: 'Shared physical room', task: tasks[0].task, bounds: [8, 8, 3], robot: 'tiago++',
      objects: [{ id: 'part', label: 'Part', kind: 'box', position: [2, 0, .1], size: [.2, .2, .2], movable: true }],
      regions: [], rubric: tasks[0].rubric },
    manifest: { id: worldId, physical_id: 'd'.repeat(64), validation: { status: 'ready', checks: [{ check: 'render', passed: true }] },
      ...(catalog ? { task_catalog: { tasks, selected_task_id: 'task0' } } : {}) },
  };
  const child = structuredClone(world);
  child.spec.task = tasks[1].task; child.spec.rubric = tasks[1].rubric;
  child.manifest.id = childId; child.manifest.parent_id = worldId;
  child.manifest.validation = { status: 'preview', checks: [{ check: 'schema', passed: true }] };
  child.manifest.edit = { kind: 'select_task', parent_world_id: worldId };
  if (catalog) child.manifest.task_catalog.selected_task_id = 'task1';
  const inspectTabs = ['scene', 'task', 'validation', 'changes'].map(id => {
    const node = new Element('button'); node.dataset.inspect = id; return node;
  });
  class Viewer {
    setSpec(spec, assetBase) { views.push({ spec, assetBase }); }
    select() {}
    setActive() {}
  }
  const response = data => ({ ok: true, json: async () => structuredClone(data) });
  const context = vm.createContext({
    document: {
      getElementById: id => nodes.get(id), querySelector: () => shell,
      querySelectorAll: selector => selector === '[data-inspect]' ? inspectTabs :
        selector === '.task-selector' ? created.filter(node => node.className === 'task-selector') : [],
      createElement: tag => { const node = new Element(tag); created.push(node); return node; },
    },
    window: { innerWidth: 1400, addEventListener() {} }, WorldViewer: Viewer, URLSearchParams,
    location: { search: '' }, history: { replaceState() {} }, setTimeout: callback => timers.push(callback),
    fetch: async (path, options = {}) => {
      requests.push({ path, method: options.method || 'GET', body: options.body });
      if (path === `/api/worlds/${worldId}/tasks/task1/select`) return submit ? submit() : response({ id: jobId, kind: 'task', status: 'queued' });
      if (path === `/api/jobs/${jobId}`) return response({ id: jobId, kind: 'task', status, error,
        ...(status === 'completed' ? { world_id: childId } : {}), events: [{ type: 'world_created', world_id: childId }] });
      if (path === `/api/worlds/${worldId}`) return response(world);
      if (path === `/api/worlds/${childId}`) return response(child);
      if (['/api/worlds', '/api/runs', '/api/references', '/api/starters'].includes(path)) return response([]);
      throw new Error(`Unexpected API request: ${path}`);
    }, console,
  });
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  const selector = () => nodes.get('inspector-body').children.find(node => node.tagName === 'SELECT');
  const open = async () => { await run(`openWorld('${worldId}')`); run("state.tab='task';renderInspector();"); nodes.get('inspector-toggle').onclick(); };
  return { run, open, selector, nodes, shell, requests, views, timers, worldId, childId, jobId, world, tasks, response };
}

const settle = async () => { for (let index = 0; index < 10; index++) await new Promise(resolve => setImmediate(resolve)); };
const text = node => [node.textContent || '', ...node.children.map(text)].join(' ');

test('task tab shows twelve named variants, keeps untrusted labels as text, and needs no model key', async () => {
  const { open, selector, nodes, run, tasks } = fixture();
  await open();
  assert.equal(selector().children.length, 13);
  assert.equal(selector().children[2].textContent, tasks[1].name);
  assert.equal(selector().attrs['aria-label'], 'Task variant');
  assert.equal(selector().value, 'task0');
  assert.equal(selector().disabled, false);
  assert.match(text(nodes.get('inspector-body')), /12 task variants · one physical scene/);
  assert.equal(run('state.key'), false);
  assert.equal(nodes.get('evaluate-button').disabled, true);
});

test('selecting a task opens its preview with unchanged physical objects and an explicit Checks instruction', async () => {
  const { open, selector, nodes, shell, run, requests, worldId, childId, world, views } = fixture();
  await open();
  assert.equal(shell.classList.contains('show-inspector'), true);
  selector().value = 'task1'; await selector().onchange(); await settle();
  assert.equal(run('state.worldId'), childId);
  assert.equal(run('state.world.manifest.physical_id'), world.manifest.physical_id);
  assert.deepEqual(run('state.world.spec.objects'), world.spec.objects);
  assert.equal(run('state.world.spec.task'), 'Perform interaction 2');
  assert.equal(selector().value, 'task1');
  assert.equal(run('state.tab'), 'task');
  assert.equal(shell.classList.contains('show-inspector'), true);
  assert.equal(shell.classList.contains('show-left'), false);
  assert.equal(nodes.get('inspector-toggle').attrs['aria-expanded'], 'true');
  assert.match(text(nodes.get('inspector-body')), /needs native checks before evaluation/);
  assert.match(nodes.get('notice').textContent, /Open Checks and validate on this computer/);
  assert.doesNotMatch(nodes.get('notice').textContent, /repair/);
  assert.equal(nodes.get('evaluate-button').disabled, true);
  assert.equal(nodes.get('physical-tab').disabled, true);
  assert.equal(views.at(-1).assetBase, `/api/worlds/${childId}/files/`);
  const mutations = requests.filter(request => request.method === 'POST');
  assert.equal(mutations.length, 1);
  assert.equal(mutations[0].path, `/api/worlds/${worldId}/tasks/task1/select`);
  assert.deepEqual(JSON.parse(mutations[0].body), {});
});

test('selection suppresses duplicates while submitting and while the task job is running', async () => {
  let resolve;
  const { open, selector, run, requests, response, jobId } = fixture({ status: 'running',
    submit: () => new Promise(done => { resolve = done; }) });
  await open();
  const select = selector(); select.value = 'task1'; const pending = select.onchange();
  assert.equal(select.disabled, true);
  assert.equal(run('state.submitting'), true);
  await select.onchange();
  resolve(response({ id: jobId, kind: 'task', status: 'queued' })); await pending; await settle();
  assert.equal(run('state.jobId'), jobId);
  assert.equal(select.disabled, true);
  await select.onchange();
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
});

test('failed task job preserves the original scene and selected task without entering an edit mode', async () => {
  const { open, selector, run, nodes, shell, worldId, world } = fixture({ status: 'failed', error: { message: 'Task catalog checksum mismatch' } });
  await open();
  selector().value = 'task1'; await selector().onchange(); await settle();
  assert.equal(run('state.worldId'), worldId);
  assert.deepEqual(run('state.world.spec.objects'), world.spec.objects);
  assert.equal(selector().value, 'task0');
  assert.equal(selector().disabled, false);
  assert.equal(run('state.tab'), 'task');
  assert.notEqual(run('state.editMode'), 'task');
  assert.equal(run('state.panel'), 'agent');
  assert.equal(shell.classList.contains('show-left'), true);
  assert.equal(shell.classList.contains('show-inspector'), false);
  assert.equal(nodes.get('inspector-toggle').attrs['aria-expanded'], 'false');
  assert.match(nodes.get('notice').textContent, /checksum mismatch/);
});

test('legacy worlds keep their current mission without an empty task selector', async () => {
  const { open, selector, nodes } = fixture({ catalog: false });
  await open();
  assert.equal(selector(), undefined);
  assert.match(text(nodes.get('inspector-body')), /Perform interaction 1/);
});
