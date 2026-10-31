import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = (await readFile(new URL('../src/robot_worlds/static/app.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gm, '').replace('\nstart();', '\n');
const html = await readFile(new URL('../src/robot_worlds/static/index.html', import.meta.url), 'utf8');
class Element {
  constructor() {
    this.value = ''; this.hidden = false; this.checked = false; this.children = []; this.style = {}; this.attrs = {};
    this.firstChild = { textContent: '' }; const classes = new Set();
    this.classList = { add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value), toggle(value, flag) { if (flag) classes.add(value); else classes.delete(value); } };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attrs[name] = value; }
  focus() {}
  set innerHTML(value) { throw new Error('Untrusted HTML assignment'); }
}
function fixture() {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size);
  const nodes = new Map(ids.map(id => [id, new Element()])), shell = new Element(), events = new Map(), calls = [];
  class Viewer {
    constructor() { this.shellCount = 2; this.cutaway = false; calls.push('create'); }
    setSpec(spec, assetBase) { this.spec = spec; this.assetBase = assetBase; calls.push('spec'); }
    setActive(active) { this.active = active; calls.push(['active', active]); }
    setCutaway(enabled) { this.cutaway = enabled; }
    select(object) { this.selected = object; }
    fit(object, direction) { calls.push(['fit', object?.id, direction]); }
    dispose() { calls.push('dispose'); }
  }
  const context = vm.createContext({
    document: { getElementById: id => nodes.get(id), querySelector: () => shell, querySelectorAll: () => [], createElement: () => new Element() },
    window: { innerWidth: 1400, addEventListener: (name, callback) => events.set(name, callback) },
    WorldViewer: Viewer, URLSearchParams, location: { search: '' }, history: { replaceState() {} },
    fetch() { throw new Error('No network/model requests allowed in these interactions'); }, console,
  });
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  run("state.key=true;state.worldId='version';state.world={spec:{bounds:[8,8,3],robot:'tiago++',objects:[{id:'part',label:'Part',kind:'box',size:[.2,.2,.2],position:[0,0,.1],mass:1}],regions:[],rubric:[]},manifest:{validation:{status:'ready'}}};");
  nodes.get('report-area').hidden = true;
  return { run, nodes, calls, events, context, shell };
}

test('prepared asset progress names reuse without claiming a fresh search or physics pass', () => {
  const {run} = fixture();
  assert.equal(run("eventLabel({type:'asset_loading',message:'Reusing google-porcelain-bowl'})"), 'Reusing google-porcelain-bowl');
  assert.equal(run("eventLabel({type:'asset_loaded',message:'Reused source; original geometry retained'})"), 'Reused source; original geometry retained');
});

test('whole-object scope includes physical siblings, jointed descendants and cavity only after a click', () => {
  const {run, nodes} = fixture();
  run(`state.world.spec.objects = [
    {id:'body',label:'Body',kind:'box',position:[1,0,1],size:[1,1,1]},
    {id:'visual',label:'Microwave',kind:'mesh',collision:false,parent_id:'body',position:[1,0,1],size:[1,1,1]},
    {id:'door',label:'Door',kind:'box',parent_id:'body',joint:{type:'hinge',axis:[0,0,1]},position:[1,0,1],size:[1,.1,1]},
    {id:'handle',label:'Handle',kind:'box',parent_id:'door',position:[1,0,1],size:[.1,.1,.1]},
    {id:'unrelated',label:'Other object',kind:'box',position:[3,0,1],size:[1,1,1]}];
    state.world.spec.regions = [{id:'cavity',interior_of:'body'}, {id:'destination'}];
    selectObject(state.world.spec.objects[1]);`);
  assert.deepEqual(Array.from(run('state.scope')), ['visual']);
  const whole = nodes.get('inspector-body').children.find(node => node.textContent === 'Include whole object');
  assert(whole);
  whole.onclick();
  assert.deepEqual(Array.from(run('state.scope')).sort(), ['body','cavity','door','handle','visual'].sort());
  assert.match(nodes.get('notice').textContent, /complete object: 5/);
});

test('complete compound selection shows its name with inspectable exact IDs', () => {
  const {run, nodes} = fixture();
  run(`state.world.spec.objects = [{id:'root',label:'Bowl',kind:'box'}, ...Array.from({length:10},(_,i)=>({id:'piece_'+i,parent_id:'root'}))];
    state.selectedObject=state.world.spec.objects[0];state.scope=wholeObjectScope(state.selectedObject).ids;renderScope();`);
  const chips = nodes.get('scope-chips').children;
  assert.equal(chips.length, 2);
  assert.equal(chips[0].textContent, 'Bowl · 11 included');
  assert.equal(chips[1].children[0].textContent, 'Inspect included entities');
  assert.equal(chips[1].children.length, 12);
});

test('large scopes remain fully included with a collapsed remainder', () => {
  const {run, nodes} = fixture();
  run("state.scope=Array.from({length:159},(_,i)=>'piece_'+i);renderScope();");
  const chips = nodes.get('scope-chips').children;
  assert.equal(chips.length, 9);
  assert.equal(chips[8].children[0].textContent, 'Show 151 more included entities');
  assert.equal(chips[8].children.length, 152);
  assert.equal(run('state.scope.length'), 159);
});

test('saved geometry previews do not offer a nonexistent native frame', () => {
  const {run, nodes} = fixture();
  run("state.world.manifest.validation={status:'preview',checks:[{check:'schema',passed:true}]};displaySpec(state.world.spec);");
  assert.equal(nodes.get('physical-tab').disabled, true);
  assert.equal(nodes.get('view-state').textContent, 'CANDIDATE LAYOUT');
  assert.equal(run('state.viewer.assetBase'), '/api/worlds/version/files/');
  run("state.world.manifest.validation={status:'unvalidated',checks:[{check:'render',passed:true}]};displaySpec(state.world.spec);");
  assert.equal(nodes.get('physical-tab').disabled, false);
  run("state.worldId=null;state.viewerSpec=null;displaySpec(state.world.spec,true);");
  assert.equal(run('state.viewer.assetBase'), null);
});

test('source visual inspector does not report placeholder mass as physical mass', () => {
  const {run, nodes} = fixture();
  run("selectObject({...currentSpec().objects[0],kind:'mesh',collision:false,texture_path:'assets/texture.png',parent_id:'body'});");
  const text = node => [node.textContent || '', ...node.children.map(text)].join(' ');
  const details = text(nodes.get('inspector-body'));
  assert.match(details, /Appearance only/);
  assert.match(details, /Bundled source texture/);
  assert.doesNotMatch(details, /1 kg/);
});

test('source collision pieces stay out of the default layers but remain inspectable', () => {
  const {run, nodes} = fixture();
  run("state.world.spec.objects.push({id:'hidden_collider',label:'Collision piece',visible:false});renderLayers();");
  assert.equal(nodes.get('layer-count').textContent, 1);
  const toggle = nodes.get('layer-list').children.at(-1);
  assert.match(toggle.textContent, /Show collision components \(1\)/);
  toggle.onclick(); assert.equal(nodes.get('layer-count').textContent, 2);
  nodes.get('layer-list').children.at(-1).onclick();
  nodes.get('layer-filter').value = 'hidden_collider'; nodes.get('layer-filter').oninput();
  assert.equal(nodes.get('layer-count').textContent, 1);
  assert.equal(nodes.get('layer-list').children[0].title, 'Collision piece · hidden_collider');
});

test('live camera updates pause WebGL, show actual time, and keep provisional milestones separate', () => {
  const { run, nodes, calls } = fixture();
  run("setView('layout');showLiveFrame({run_id:'a'.repeat(32),episode:0,sequence:4,time:2.5,path:'episode-000/live.png'});");
  assert.deepEqual(calls.at(-1), ['active', false]);
  assert.equal(nodes.get('view-state').textContent, 'LIVE ROBOT');
  assert.equal(nodes.get('live-time').textContent, '2.5 sim s');
  assert.match(nodes.get('physical-image').src, /episode-000\/live.png\?frame=4$/);
  const image = nodes.get('physical-image').src;
  run("showLiveFrame({run_id:'a'.repeat(32),sequence:5,time:3,path:'../../secret'});");
  assert.equal(nodes.get('physical-image').src, image);
  run("showLiveEvent({type:'policy_started',step:2});");
  assert.equal(nodes.get('live-phase').textContent, 'Thinking');
  run("showLiveEvent({type:'task_progress',criteria:[{description:'Carry part',satisfied:true},{description:'Deliver part',satisfied:false}]});");
  assert.match(nodes.get('inspector-body').children[1].textContent, /Final conditions/);
  assert.equal(nodes.get('inspector-body').children[2].children[0].textContent, '✓');
  assert.equal(nodes.get('inspector-body').children[3].children[0].textContent, '○');
  assert.equal(nodes.get('live-progress-bar').max, 2);
  assert.equal(nodes.get('live-progress-bar').value, 1);
  assert.match(nodes.get('live-progress-label').textContent, /1 \/ 2 checks observed · provisional/);
  run("showLiveEvent({type:'task_progress',criteria:[{description:'Carry part',satisfied:false},{description:'Deliver part',satisfied:false}]});");
  assert.equal(nodes.get('live-progress-bar').value, 0); // Final-state regression is visible.
  run("showLiveEvent({type:'run_finished',summary:{successes:0}});");
  assert.equal(nodes.get('live-phase').textContent, 'Saving result');
  assert.doesNotMatch(nodes.get('live-action').textContent, /success|passed/);
});

test('native frame inspection never starts WebGL; layout does, then native frame pauses it', () => {
  const { run, calls, nodes } = fixture();
  run("setView('physical');"); assert.equal(calls.length, 0);
  assert.equal(nodes.get('view-state').textContent, 'SAVED NATIVE FRAME');
  assert.equal(nodes.get('fit-view').disabled, true);
  run("setView('layout');"); assert.equal(calls.filter(value => value === 'create').length, 1);
  assert.equal(nodes.get('view-state').textContent, 'LAYOUT · NOT A ROLLOUT');
  run("setView('physical');"); assert.deepEqual(calls.at(-1), ['active', false]);
});

test('cutaway state is disclosed and selection focus is disabled outside the layout', () => {
  const { run, nodes, calls } = fixture();
  run("setView('layout');selectObject(currentSpec().objects[0]);");
  nodes.get('cutaway-view').onclick();
  assert.equal(nodes.get('cutaway-view').attrs['aria-pressed'], 'true');
  assert.match(nodes.get('viewport-caption').textContent, /2 inferred wall\/roof objects hidden/);
  assert.equal(nodes.get('focus-object').disabled, false); nodes.get('focus-object').onclick();
  assert.deepEqual(calls.at(-1), ['fit', 'part', undefined]);
  run("setView('physical');"); assert.equal(nodes.get('focus-object').disabled, true);
});

test('reset and page exit release the viewer and embedded replay', () => {
  const { run, nodes, calls, events } = fixture();
  run("setView('layout');resetWorkspace();");
  assert.equal(calls.filter(value => value === 'dispose').length, 1);
  assert.equal(nodes.get('report-frame').src, 'about:blank');
  events.get('pagehide')(); assert.equal(calls.filter(value => value === 'dispose').length, 1);
});

test('first-use factory action only creates an editable draft', () => {
  const { run, nodes, calls } = fixture();
  run('state.lineage=[];state.worlds=[];state.jobContext=null;renderAgentHistory();');
  const welcome = nodes.get('agent-history').children[0];
  const draft = welcome.children.find(node => node.textContent === 'Draft a factory mission');
  draft.onclick(); assert.match(nodes.get('prompt').value, /factory/); assert.equal(calls.length, 0);
});

test('small-screen object inspection opens a dismissible inspector, not an overflowing grid', () => {
  const { run, context, shell, nodes } = fixture(); context.window.innerWidth = 600;
  run('selectObject(currentSpec().objects[0]);'); assert(shell.classList.contains('show-inspector'));
  assert.equal(nodes.get('inspector-toggle').attrs['aria-expanded'], 'true');
  nodes.get('close-inspector').onclick(); assert(!shell.classList.contains('show-inspector'));
  nodes.get('close-left').onclick(); assert(!shell.classList.contains('show-left'));
});

test('generation phases distinguish measured elapsed time from successful validation', () => {
  const {run} = fixture();
  assert.equal(run("eventLabel({type:'generation_phase_started',phase:'model',attempt:2})"), 'Design scene · attempt 2 · started');
  assert.equal(run("eventLabel({type:'generation_phase_finished',phase:'physical_validation',attempt:2,duration_seconds:12.34,status:'finished'})"), 'Check simulation and save world · attempt 2 · 12.3 s · finished');
  assert.equal(run("eventLabel({type:'generation_phase_finished',phase:'model',attempt:1,duration_seconds:8,status:'failed',error_type:'RuntimeError'})"), 'Design scene · attempt 1 · 8.0 s · interrupted');
  assert.doesNotMatch(run("eventLabel({type:'generation_phase_finished',phase:'preview',attempt:1,duration_seconds:NaN})"), /NaN|passed|ready|success/);
});

test('online asset stages show discovery and cache reuse separately from world validation', () => {
  const {run} = fixture();
  assert.equal(run("eventLabel({type:'asset_requests',requests:[{},{}]})"), 'Find 2 source assets');
  assert.equal(run("eventLabel({type:'asset_search_started',message:'Searching Fuel for painted object'})"), 'Searching Fuel for painted object');
  assert.equal(run("eventLabel({type:'asset_resolution_reused',message:'Reused prepared source for scan'})"), 'Reused prepared source for scan');
  assert.equal(run("eventLabel({type:'generation_phase_started',phase:'source_assets',attempt:1})"), 'Find and prepare source assets · attempt 1 · started');
  assert.equal(run("eventLabel({type:'asset_cache_catalog',message:'Added 1 prepared sources from previous generations'})"), 'Added 1 prepared sources from previous generations');
  assert.equal(run("eventLabel({type:'asset_cache_unavailable',message:'Recent source unavailable; files unchanged'})"), 'Recent source unavailable; files unchanged');
  assert.equal(run("eventLabel({type:'asset_creation_available',message:'No candidates in the recorded catalog'})"), 'No candidates in the recorded catalog');
  assert.equal(run("eventLabel({type:'asset_created_after_search',message:'Created fixture after search; physics checks follow'})"), 'Created fixture after search; physics checks follow');
});
