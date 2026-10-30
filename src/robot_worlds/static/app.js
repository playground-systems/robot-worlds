import { WorldViewer } from './scene.js';

const $ = id => document.getElementById(id);
const state = { world: null, worldId: null, worlds: [], runs: [], tab: 'task', mode: 'physical', jobId: null, eventSeen: new Set(), key: false, editing: false, isPreview: false, viewer: null, refine: false, assets: [] };
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const pretty = value => JSON.stringify(value, null, 2);
const shorten = (value, n = 18) => String(value || '').slice(0, n);
const message = error => typeof error === 'string' ? error : error?.message || error?.detail?.message || (typeof error?.detail === 'string' ? error.detail : pretty(error));
function notice(text, good = false) { $('notice').hidden = !text; $('notice').textContent = text || ''; $('notice').classList.toggle('good', good); }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(message(data.detail || data.error || data) || `Request failed (${response.status})`);
  return data;
}
const post = (path, data) => api(path, { method: 'POST', body: JSON.stringify(data) });
function settings() { return { model: $('model').value.trim() || 'gpt-6-astra', reasoning: $('reasoning').value }; }
function statusOf(world) { const value = world?.manifest?.validation || world?.validation || {}; return typeof value === 'string' ? value : value.status || 'preview'; }
function controls() {
  const busy = !!state.jobId, ready = state.worldId && statusOf(state.world) === 'ready' && $('report-area').hidden;
  $('generate-button').disabled = busy || !state.key;
  $('evaluate-button').disabled = busy || !ready || !state.key;
  $('refine-button').disabled = busy || !state.worldId || !state.key || !$('report-area').hidden;
  $('new-world').disabled = busy;
  $('prompt').disabled = busy; $('asset-files').disabled = busy;
}
async function refreshLists() {
  const [worlds, runs, references] = await Promise.all([api('/api/worlds'), api('/api/runs'), api('/api/references')]);
  state.references = references;
  state.worlds = Array.isArray(worlds) ? worlds : worlds.worlds || [];
  state.runs = Array.isArray(runs) ? runs : runs.runs || [];
  renderLists();
}
function renderLists() {
  $('world-count').textContent = state.worlds.length; $('run-count').textContent = state.runs.length;
  $('world-list').replaceChildren(); $('run-list').replaceChildren();
  $('reference-list').replaceChildren(); $('reference-section').hidden = !state.references?.length;
  for (const reference of state.references || []) { const item = el('button', 'list-item'), copy = el('span'); copy.append(el('strong', '', reference.name), el('small', '', 'Source geometry · static study')); item.append(el('span', 'item-icon', '◈'), copy); item.onclick = () => openReference(reference.id).catch(e => notice(e.message)); $('reference-list').append(item); }
  if (!state.worlds.length) $('world-list').append(el('p', 'sidebar-empty', 'No generated worlds yet.'));
  for (const world of state.worlds) {
    const item = el('button', `list-item${state.worldId === world.id ? ' selected' : ''}`), text = el('span');
    item.title = world.task || world.name || world.id;
    text.append(el('strong', '', world.name || 'Untitled world'), el('small', '', `${statusOf(world)} · ${shorten(world.id, 8)}`));
    item.append(el('span', 'item-icon', '◇'), text); item.onclick = () => openWorld(world.id).catch(e => notice(e.message)); $('world-list').append(item);
  }
  if (!state.runs.length) $('run-list').append(el('p', 'sidebar-empty', 'Your policy runs will appear here.'));
  for (const run of state.runs) {
    const item = el('button', 'list-item'), text = el('span');
    const summary = run.summary || {}, label = summary.valid ? `${summary.successes || 0}/${summary.valid} completed` : String(run.status || 'recorded').replaceAll('_', ' ');
    text.append(el('strong', '', run.name || run.world_name || `Run ${shorten(run.id, 8)}`), el('small', '', label));
    item.append(el('span', 'item-icon', '↗'), text); item.onclick = () => openReport(run.id); $('run-list').append(item);
  }
}
function getViewer() {
  if (state.viewer) return state.viewer;
  try { state.viewer = new WorldViewer($('scene-canvas'), $('scene-labels'), object => selectObject(object)); }
  catch (error) { notice(`3D layout is unavailable in this browser. The native physical image is still available. ${error.message}`); }
  return state.viewer;
}
function displaySpec(spec, preview = false) {
  state.isPreview = preview;
  $('report-area').hidden = true; $('work-area').hidden = false; $('viewport').classList.remove('empty'); $('empty-state').hidden = true;
  $('world-title').textContent = spec.name || 'Generating world'; $('workspace-eyebrow').textContent = preview ? 'CANDIDATE / VALIDATION PENDING' : 'IMMUTABLE WORLD VERSION';
  $('viewport-info').textContent = `${spec.bounds?.[0] || '?'} × ${spec.bounds?.[1] || '?'} m · ${spec.objects?.length || 0} objects · ${spec.robot || 'robot pending'}`;
  $('physical-tab').disabled = preview; $('layout-tab').disabled = false; $('fit-view').disabled = false;
  $('world-status').hidden = false; const status = preview ? 'preview' : statusOf(state.world); $('world-status').className = 'badge ' + status; $('world-status').textContent = status;
  getViewer()?.setSpec(spec, state.worldId && !preview ? `/api/worlds/${encodeURIComponent(state.worldId)}/files/` : null); renderInspector();
  if (preview) setView('layout');
  else { $('physical-image').src = `/api/worlds/${encodeURIComponent(state.worldId)}/preview.png`; setView('physical'); }
  controls();
}
function setView(mode) {
  state.mode = mode; const layout = mode === 'layout';
  $('scene-canvas').hidden = !layout; $('scene-labels').hidden = !layout; $('physical-image').hidden = layout;
  $('physical-tab').classList.toggle('active', !layout); $('layout-tab').classList.toggle('active', layout);
  $('viewport-caption').hidden = false;
  $('viewport-caption').textContent = layout ? 'Interactive layout · drag to orbit, scroll to zoom · robot start marker; custom meshes use declared bounds.' : 'Native MuJoCo render · physical scene and installed robot model.';
  if (layout) { state.viewer?.invalidate(); if (!state.viewer) $('viewport-caption').textContent = '3D layout requires WebGL. Use the physical view when available.'; }
}
$('physical-image').onerror = () => {
  if (!state.worldId) return;
  if (state.mode === 'physical') { setView('layout'); notice('No native preview is available for this version. The layout is a candidate view; inspect validation before running.'); }
};
async function openWorld(id) {
  const world = await api(`/api/worlds/${encodeURIComponent(id)}`);
  state.world = world; state.worldId = id; state.editing = false; state.refine = false;
  $('prompt-label').textContent = 'Describe a new environment and task'; $('generate-button').firstChild.textContent = 'Generate world ';
  displaySpec(world.spec); renderLists(); history.replaceState(null, '', `/?world=${encodeURIComponent(id)}`);
}
function openReport(id) {
  $('work-area').hidden = true; $('report-area').hidden = false; $('world-title').textContent = `Evaluation ${shorten(id, 10)}`; $('workspace-eyebrow').textContent = 'RECORDED BEHAVIOR / LOCAL GRADING'; $('world-status').hidden = true;
  const path = `/api/runs/${encodeURIComponent(id)}/report`;
  $('report-frame').src = path; $('report-new-tab').href = path;
  history.replaceState(null, '', `/?run=${encodeURIComponent(id)}`);
  controls();
}
function currentSpec() { return state.world?.spec; }
function renderInspector() {
  const root = $('inspector-body'); root.replaceChildren(); const spec = currentSpec();
  if (!spec) { root.append(el('p', 'task-copy', 'The task, objects and validation checks will appear as the world is generated.')); return; }
  if (state.tab === 'task') {
    root.append(el('h3', '', 'The mission'), el('p', 'task-copy', spec.task));
    const label = el('div', 'inspector-label', 'EXECUTABLE RUBRIC'), edit = el('button', 'text-button', state.editing ? 'Close JSON' : 'Edit JSON');
    edit.disabled = !state.worldId || !!state.jobId; edit.onclick = () => { state.editing = !state.editing; renderInspector(); }; label.append(edit); root.append(label);
    if (state.editing) {
      root.append(el('p', 'small muted', 'Save as a new immutable world version. Existing runs keep their original rubric.'));
      const editor = el('textarea', 'json-editor'); editor.value = pretty(spec.rubric || []); editor.setAttribute('aria-label', 'Editable rubric JSON');
      const actions = el('div', 'edit-actions'), save = el('button', 'button primary', 'Validate & save version');
      save.onclick = async () => { try { const rubric = JSON.parse(editor.value); if (!Array.isArray(rubric)) throw new Error('Rubric JSON must be an array of criteria.'); const job = await post(`/api/worlds/${encodeURIComponent(state.worldId)}/rubric`, { rubric }); trackJob(job, 'Validating rubric'); } catch (error) { notice(error.message); } };
      actions.append(save); root.append(editor, actions);
    } else for (const c of spec.rubric || []) {
      const item = el('div', 'rubric-item'); item.append(el('h4', '', c.description || c.id));
      const badge = el('span', 'badge', c.required ? 'Required' : 'Optional'); item.append(badge);
      item.append(el('div', 'rubric-meta', `${c.predicate} · ${c.mode || 'final'}${c.duration ? ` · hold ${c.duration} s` : ''}`), el('div', 'rubric-meta', [c.object_id, c.target_id && `→ ${c.target_id}`].filter(Boolean).join(' ')));
      if (c.after?.length) item.append(el('div', 'rubric-meta', `After: ${c.after.join(', ')}`));
      root.append(item);
    }
    if (spec.process_cycles?.length) { root.append(el('div', 'inspector-label', 'MODELED PROCESS CYCLES')); for (const p of spec.process_cycles) root.append(el('p', 'small muted', `${p.label || p.id}: ${p.duration} simulated seconds → ${p.output_state}. Internal processing is a state transition; handling and loading remain physical.`)); }
  } else if (state.tab === 'scene') {
    root.append(el('h3', '', `${spec.objects?.length || 0} physical objects`), el('p', 'small muted', 'Select an object in the 3D layout to inspect its declared properties.'));
    for (const object of spec.objects || []) { const item = el('div', 'object-item'), swatch = el('span', 'object-swatch'), text = el('div'); swatch.style.backgroundColor = `rgb(${(object.color || [.5, .6, .7]).map(x => Math.max(0, Math.min(255, x * 255))).join(',')})`; text.append(el('strong', '', object.label || object.id), el('small', '', `${object.id} · ${object.kind} · ${object.movable ? 'movable' : 'fixed'}${object.joint ? ' · articulated' : ''}`)); item.append(swatch, text); item.onclick = () => selectObject(object); root.append(item); }
    for (const assumption of spec.assumptions || []) root.append(el('p', 'small muted', assumption));
  } else {
    const validation = state.world?.manifest?.validation || { status: 'preview', checks: [] };
    root.append(el('h3', '', 'Validation status'), el('p', 'task-copy', validation.status === 'ready' ? 'Runnable under the recorded checks. Validation does not prove the policy can complete the mission.' : 'This version is not ready for a graded run. Review the unresolved checks below.'));
    for (const check of validation.checks || []) { const item = el('div', 'validation-item'); item.append(el('span', `badge ${check.passed ? 'ready' : 'unvalidated'}`, check.passed ? 'Pass' : 'Unresolved'), el('strong', '', String(check.check || check.name || 'check').replaceAll('_', ' '))); for (const error of check.errors || []) item.append(el('p', '', message(error))); if (check.error) item.append(el('p', '', message(check.error))); root.append(item); }
    for (const limitation of validation.limitations || []) root.append(el('p', 'small muted', limitation));
    root.append(el('div', 'inspector-label', 'VERSION'), el('p', 'mono muted', state.worldId || 'Not yet saved'));
    if (state.world?.manifest?.parent_id) root.append(el('p', 'mono muted', `Parent: ${state.world.manifest.parent_id}`));
  }
}
function selectObject(object) {
  state.tab = 'scene'; syncTabs(); renderInspector();
  $('selection-label').hidden = false; $('selection-label').textContent = `${object.label || object.id} · ${object.id}`;
  const detail = el('div', 'object-detail');
  detail.append(el('strong', '', object.label || object.id), el('p', '', `Position: ${object.position.join(', ')} m`), el('p', '', `Size: ${object.size.join(' × ')} m`), el('p', '', `Mass: ${object.mass} kg · ${object.movable ? 'movable' : 'fixed'}`));
  if (object.kind === 'mesh') detail.append(el('p', '', 'The 3D layout shows the declared mesh bounds. The native view uses the physical mesh.'));
  $('inspector-body').prepend(detail);
}
function syncTabs() { document.querySelectorAll('[data-inspect]').forEach(button => button.classList.toggle('active', button.dataset.inspect === state.tab)); }
document.querySelectorAll('[data-inspect]').forEach(button => { button.onclick = () => { state.tab = button.dataset.inspect; syncTabs(); renderInspector(); }; });
$('physical-tab').onclick = () => setView('physical'); $('layout-tab').onclick = () => setView('layout'); $('fit-view').onclick = () => state.viewer?.fit();
function resetWorkspace() {
  state.world = null; state.worldId = null; state.editing = false; state.refine = false; state.assets = []; renderAssets();
  $('work-area').hidden = false; $('report-area').hidden = true; $('viewport').classList.add('empty'); $('empty-state').hidden = false;
  for (const id of ['scene-canvas', 'scene-labels', 'physical-image', 'viewport-caption', 'selection-label', 'world-status']) $(id).hidden = true;
  $('physical-tab').disabled = $('layout-tab').disabled = $('fit-view').disabled = true;
  $('world-title').textContent = 'Build a world worth testing.'; $('workspace-eyebrow').textContent = 'TEXT → PHYSICS → EVIDENCE'; $('viewport-info').textContent = 'No environment loaded';
  $('prompt-label').textContent = 'What should the robot do, and where?'; $('generate-button').firstChild.textContent = 'Generate world '; $('prompt').value = '';
  controls(); renderInspector(); renderLists(); history.replaceState(null, '', '/'); $('prompt').focus();
}
$('new-world').onclick = resetWorkspace;
$('refine-button').onclick = () => { state.refine = true; $('prompt').value = ''; $('prompt-label').textContent = 'What should change in this world?'; $('generate-button').firstChild.textContent = 'Refine world '; $('prompt').focus(); };
$('generate-form').onsubmit = async event => {
  event.preventDefault(); if (state.jobId) return; const prompt = $('prompt').value.trim(); if (!prompt) return;
  try { notice(''); const path = state.refine ? `/api/worlds/${encodeURIComponent(state.worldId)}/refine` : '/api/generate'; const assetNote = state.assets.length ? '\nAttached mesh names: ' + state.assets.map(a => `${a.name}: ${a.id}`).join('; ') : ''; const job = await post(path, { prompt: prompt + assetNote, ...settings(), scene_profile: $('scene-profile').value, min_objects: $('scene-profile').value === 'large' ? 500 : 0, max_cost_usd: Number($('generation-cost').value), max_model_seconds: Number($('generation-seconds').value), max_repairs: 2, asset_ids: state.assets.map(a => a.id) }); trackJob(job, state.refine ? 'Refining world' : 'Generating world'); }
  catch (error) { notice(error.message); }
};
function eventLabel(event) {
  const type = String(event.type || event.phase || 'update').replaceAll('_', ' ');
  const detail = event.message || (event.errors?.length ? event.errors.map(message).join('; ') : event.error ? message(event.error) : '');
  return detail ? `${type}: ${detail}` : type;
}
function trackJob(job, label) {
  const id = job.id || job.job_id; if (!id) { notice('The server returned no job identifier.'); return; }
  state.jobId = id; state.eventSeen = new Set(); $('job-panel').hidden = false; $('job-panel').classList.remove('done'); $('job-title').textContent = label; $('job-state').textContent = job.status || 'queued'; $('job-events').replaceChildren(); $('cancel-job').hidden = false; $('cancel-job').textContent = 'Cancel'; controls(); pollJob(id);
}
async function pollJob(id) {
  if (state.jobId !== id) return;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`); $('job-state').textContent = job.status;
    for (const event of job.events || []) {
      const signature = JSON.stringify(event); if (state.eventSeen.has(signature)) continue; state.eventSeen.add(signature);
      $('job-events').append(el('li', '', eventLabel(event)));
      if (event.type === 'preview' && event.spec) { state.worldId = null; state.world = { spec: event.spec, manifest: { validation: { status: 'preview', checks: [] } } }; displaySpec(event.spec, true); }
    }
    $('job-events').scrollTop = $('job-events').scrollHeight;
    if (['completed', 'completed_with_errors', 'succeeded', 'done', 'failed', 'error', 'cancelled', 'interrupted'].includes(job.status)) {
      state.jobId = null; $('job-panel').classList.add('done'); $('cancel-job').hidden = true; controls();
      if (job.error) notice(message(job.error));
      await refreshLists();
      if (job.run_id) openReport(job.run_id);
      else if (job.world_id) { await openWorld(job.world_id); if (statusOf(state.world) !== 'ready') { state.tab = 'validation'; syncTabs(); renderInspector(); notice('A world version was saved, with unresolved validation checks. Inspect them before evaluation.'); } }
      else if (job.status === 'cancelled') notice('Job cancelled. Any saved artifacts remain available.');
      return;
    }
  } catch (error) { notice(`Unable to read job status: ${error.message}`); }
  setTimeout(() => pollJob(id), 1300);
}
$('cancel-job').onclick = async () => { if (!state.jobId) return; try { await post(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, {}); $('cancel-job').textContent = 'Cancellation requested'; } catch (error) { notice(error.message); } };
$('evaluate-button').onclick = () => { $('policy-model-label').textContent = `${settings().model} · ${settings().reasoning} reasoning`; $('evaluate-dialog').showModal(); };
$('close-evaluate').onclick = () => $('evaluate-dialog').close();
$('episodes').oninput = () => { const n = Number($('episodes').value); $('start-evaluate').textContent = `Evaluate ${n || 1} episode${n === 1 ? '' : 's'} ↗`; };
$('evaluate-form').onsubmit = async event => {
  event.preventDefault();
  try { const job = await post(`/api/worlds/${encodeURIComponent(state.worldId)}/evaluate`, { ...settings(), episodes: Number($('episodes').value), max_steps: Number($('max-steps').value), max_sim_seconds: Number($('max-sim-seconds').value), seed: Number($('seed').value), max_cost_usd: Number($('policy-cost').value), max_model_seconds: Number($('policy-seconds').value) }); $('evaluate-dialog').close(); notice(''); trackJob(job, 'Evaluating policy'); }
  catch (error) { notice(error.message); }
};
async function start() {
  try { const health = await api('/api/health'); state.key = !!health.key_configured; $('connection').textContent = state.key ? '● Local runtime · key configured' : 'API key required'; $('connection').classList.toggle('error', !state.key); if (!state.key) notice('Set OPENAI_API_KEY in the server environment, then restart the local server to generate worlds or run the agent. Saved worlds and reports remain available.'); if (health.model) $('model').value = health.model; await refreshLists(); const params = new URLSearchParams(location.search); if (params.get('reference')) await openReference(params.get('reference'));
    else if (params.get('run')) openReport(params.get('run')); else if (params.get('world')) await openWorld(params.get('world')); }
  catch (error) { notice(error.message); $('connection').textContent = 'Local server unavailable'; $('connection').classList.add('error'); }
  controls();
}
start();

$('asset-files').onchange = async event => {
  const files = [...event.target.files];
  if (files.length + state.assets.length > 10) { notice('Attach at most 10 meshes to one generation.'); return; }
  $('generate-button').disabled = true;
  try {
    for (const file of files) {
      if (file.size > 20 * 1024 * 1024) throw new Error(`${file.name} exceeds 20 MiB.`);
      const asset = await api(`/api/assets/${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
      state.assets.push(asset);
    }
    renderAssets(); notice('Meshes attached. Specify their intended sizes and roles in your prompt.', true);
  } catch (error) { notice(error.message); }
  finally { event.target.value = ''; controls(); }
};
function renderAssets() {
  $('asset-list').replaceChildren();
  state.assets.forEach((asset, index) => {
    const item = el('li', '', asset.name + ' '), remove = el('button', 'text-button', 'Remove');
    remove.type = 'button'; remove.setAttribute('aria-label', `Remove ${asset.name}`);
    remove.onclick = () => { state.assets.splice(index, 1); renderAssets(); };
    item.append(remove); $('asset-list').append(item);
  });
}

async function openReference(id) {
  const base = `/api/references/${encodeURIComponent(id)}/`;
  notice('Loading the source geometry and materials…');
  const [manifest, data, spec, environment] = await Promise.all([api(base + 'reference-manifest.json'), api(base + 'visual-scene.json'), api(base + 'world.json'), api(base + 'environment-scene.json').catch(() => null)]);
  state.world = { spec, manifest: { validation: { status: 'reference', checks: [] } } }; state.worldId = null; state.editing = false;
  displaySpec(spec, true); await getViewer().setReference(data, manifest, environment); notice('');
  $('world-title').textContent = 'Airsup production line'; $('workspace-eyebrow').textContent = 'SOURCE REPRODUCTION / STATIC REFERENCE';
  $('world-status').textContent = 'Reference study'; $('world-status').className = 'badge preview';
  $('viewport-caption').textContent = 'Source geometry at the original camera angle · frozen reference state · lighting differs.';
  $('viewport-info').textContent = `${manifest.counts?.visible_meshes || manifest.components?.length || ''} source meshes · MIT`;
  $('physical-tab').disabled = true; state.tab = 'scene'; syncTabs(); renderInspector();
  const note = el('div', 'notice', 'Source-derived geometry, including visual-only screens and effects. This view is not a policy rollout or a physics-validation result.'); $('inspector-body').prepend(note);
  controls(); history.replaceState(null, '', `/?reference=${encodeURIComponent(id)}`);
}
