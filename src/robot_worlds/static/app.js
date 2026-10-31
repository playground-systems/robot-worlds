import { WorldViewer } from './scene.js';

const $ = id => document.getElementById(id);
const state = { world: null, navigation: 0, worldId: null, worlds: [], runs: [], tab: 'scene', panel: 'agent', selectedObject: null, layerLimit: 120, mode: 'physical', jobId: null, eventSeen: new Set(), key: false, editing: false, isPreview: false, viewer: null, viewerSpec: null, reference: false, editMode: null, scope: [], lineage: [], lineageLoading: false, lineageError: '', submitting: false, jobContext: null, assets: [] };
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const pretty = value => JSON.stringify(value, null, 2);
const shorten = (value, n = 18) => String(value || '').slice(0, n);
const message = error => typeof error === 'string' ? error : error?.message || error?.detail?.message || (typeof error?.detail === 'string' ? error.detail : pretty(error));
function notice(text, good = false) { if (text && !good) setPanel('agent'); $('notice').hidden = !text; $('notice').textContent = text || ''; $('notice').classList.toggle('good', good); }
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
  const busy = !!state.jobId || state.submitting, ready = state.worldId && statusOf(state.world) === 'ready' && $('report-area').hidden;
  $('generate-button').disabled = busy || !state.key;
  $('evaluate-button').disabled = busy || !ready || !state.key;
  $('refine-button').disabled = busy || !state.worldId || !state.key || !$('report-area').hidden;
  $('repair-button').disabled = $('refine-button').disabled;
  $('new-world').disabled = busy;
  for (const id of ['scope-input', 'add-scope', 'allow-task-changes', 'allow-physics-changes', 'leave-edit']) $(id).disabled = busy;
  document.querySelectorAll('.item-list button, .scope-chip button, .version-card button, .parent-actions button, .edit-actions button, .agent-history button').forEach(button => { button.disabled = busy; });
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
    item.disabled = !!state.jobId || state.submitting;
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
function disposeViewer() { state.viewer?.dispose(); state.viewer = null; state.viewerSpec = null; }
window.addEventListener('pagehide', disposeViewer);
window.addEventListener('pageshow', event => { if (!event.persisted || !currentSpec()) return; if (state.reference) { const id = new URLSearchParams(location.search).get('reference'); if (id) openReference(id).catch(error => notice(error.message)); } else if ($('report-area').hidden) setView(state.mode); });
function getViewer() {
  if (state.viewer) return state.viewer;
  try { state.viewer = new WorldViewer($('scene-canvas'), $('scene-labels'), object => selectObject(object)); }
  catch (error) { notice(`3D layout is unavailable in this browser. The native physical image is still available. ${error.message}`); }
  return state.viewer;
}
function displaySpec(spec, preview = false) {
  state.isPreview = preview || statusOf(state.world) !== 'ready'; state.reference = false;
  const hasNativePreview = !preview && (statusOf(state.world) === 'ready' || state.world?.manifest?.validation?.checks?.some(check => check.check === 'render' && check.passed));
  document.querySelector('.app-shell').classList.remove('showing-report');
  $('report-frame').src = 'about:blank';
  $('report-area').hidden = true; $('work-area').hidden = false; $('viewport').classList.remove('empty'); $('empty-state').hidden = true;
  $('world-title').textContent = spec.name || 'Generating world'; $('workspace-eyebrow').textContent = preview ? 'Candidate · checking physics' : `Robot Worlds · ${shorten(state.worldId, 8)}`;
  $('viewport-info').textContent = `${spec.bounds?.[0] || '?'} × ${spec.bounds?.[1] || '?'} m · ${spec.objects?.length || 0} objects · ${spec.robot || 'robot pending'}`;
  $('physical-tab').disabled = !hasNativePreview; $('layout-tab').disabled = false; $('fit-view').disabled = false;
  $('world-status').hidden = false; const status = preview ? 'preview' : statusOf(state.world); $('world-status').className = 'badge ' + status; $('world-status').textContent = status;
  renderLayers();
  renderInspector();
  if (!hasNativePreview) setView('layout');
  else { $('physical-image').src = `/api/worlds/${encodeURIComponent(state.worldId)}/preview.png`; setView('layout'); }
  controls();
}
function syncViewControls() {
  const layout = state.mode === 'layout', viewer = state.viewer, interactive = layout && !!viewer;
  $('fit-view').disabled = !interactive; $('focus-object').disabled = !interactive || !state.selectedObject || state.reference;
  $('top-view').disabled = !interactive || state.reference; $('cutaway-view').disabled = !interactive || !viewer?.shellCount || state.reference;
  $('cutaway-view').setAttribute('aria-pressed', String(!!viewer?.cutaway));
  $('cutaway-view').textContent = viewer?.cutaway ? 'Show walls' : 'Cutaway';
  $('cutaway-view').title = viewer?.shellCount ? `Hide ${viewer.shellCount} inferred fixed wall/roof objects in the layout only. Collision geometry is unchanged.` : 'No fixed wall or roof objects detected';
  $('orbit-hint').hidden = !interactive;
  $('view-state').hidden = !currentSpec();
  $('view-state').textContent = state.reference ? 'STATIC REFERENCE' : layout ? (state.isPreview ? 'CANDIDATE LAYOUT' : 'LAYOUT · NOT A ROLLOUT') : 'SAVED NATIVE FRAME';
  $('viewport-caption').textContent = state.reference ? 'Frozen source visuals · lighting may differ · no simulated behavior.' : layout ? (viewer?.cutaway ? `${viewer.shellCount} inferred wall/roof objects hidden for inspection. ` : '') + 'Reference configuration · robot start marker · joints have not been simulated. ' + (viewer?.appearanceNotice || '') : 'Saved MuJoCo validation image · not a live simulation or evidence of task success.';
}
function setView(mode) {
  $('live-status').hidden = mode !== 'live';
  const layout = mode === 'layout';
  if (layout && currentSpec()) {
    const viewer = getViewer();
    if (viewer && state.viewerSpec !== currentSpec()) {
      viewer.setSpec(currentSpec(), state.worldId ? `/api/worlds/${encodeURIComponent(state.worldId)}/files/` : null);
      state.viewerSpec = currentSpec(); viewer.select(state.selectedObject);
    }
    if (!viewer && !state.isPreview && state.worldId) { setView('physical'); return; }
  }
  state.mode = mode;
  $('scene-canvas').hidden = !layout; $('scene-labels').hidden = !layout; $('physical-image').hidden = layout;
  $('physical-tab').classList.toggle('active', !layout); $('layout-tab').classList.toggle('active', layout);
  $('physical-tab').setAttribute('aria-pressed', String(!layout)); $('layout-tab').setAttribute('aria-pressed', String(layout));
  $('viewport-caption').hidden = false; state.viewer?.setActive(layout); syncViewControls();
  if (layout && !state.viewer) $('viewport-caption').textContent = '3D layout requires WebGL. Saved native frames remain available when present.';
}
$('physical-image').onerror = () => {
  if (!state.worldId) return;
  if (state.mode === 'physical') { setView('layout'); notice('No native preview is available for this version. The layout is a candidate view; inspect validation before running.'); }
};
async function openWorld(id) {
  if (state.jobId || state.submitting) return;
  const navigation = ++state.navigation;
  const world = await api(`/api/worlds/${encodeURIComponent(id)}`);
  if (navigation !== state.navigation) return;
  $('layer-filter').value = ''; state.layerLimit = 120; state.collisionLayers = false;
  state.world = world; state.worldId = id; state.editing = false; state.selectedObject = null; state.scope = []; state.assets = []; renderAssets();
  $('selection-label').hidden = true;
  setEditMode('edit', { activate: false });
  displaySpec(world.spec); renderLists(); history.replaceState(null, '', `/?world=${encodeURIComponent(id)}`);
  loadLineage(id, world);
}
function openReport(id) {
  if (state.jobId || state.submitting) return;
  state.navigation++; setPanel('runs'); state.viewer?.setActive(false);
  $('live-status').hidden = true;
  document.querySelector('.app-shell').classList.add('showing-report');
  $('work-area').hidden = true; $('report-area').hidden = false; $('world-title').textContent = `Evaluation ${shorten(id, 10)}`; $('workspace-eyebrow').textContent = 'RECORDED BEHAVIOR / LOCAL GRADING'; $('world-status').hidden = true;
  const path = `/api/runs/${encodeURIComponent(id)}/report`;
  $('report-frame').src = path; $('report-new-tab').href = path;
  history.replaceState(null, '', `/?run=${encodeURIComponent(id)}`);
  controls();
}
function currentSpec() { return state.world?.spec; }

function setPanel(panel) {
  state.panel = panel; const shell = document.querySelector('.app-shell'); shell.classList.add('show-left'); shell.classList.remove('show-inspector'); $('inspector-toggle').setAttribute('aria-expanded', 'false');
  document.querySelectorAll('[data-workspace-panel]').forEach(node => { node.hidden = node.dataset.workspacePanel !== panel; });
  document.querySelectorAll('[data-panel]').forEach(button => { const active = button.dataset.panel === panel; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
}
document.querySelectorAll('[data-panel]').forEach(button => { button.onclick = () => { setPanel(button.dataset.panel); if (button.dataset.panel === 'agent') $('prompt').focus(); }; });
$('close-left').onclick = () => document.querySelector('.app-shell').classList.remove('show-left');
$('inspector-toggle').onclick = () => { const shell = document.querySelector('.app-shell'); shell.classList.add('show-inspector'); shell.classList.remove('show-left'); $('inspector-toggle').setAttribute('aria-expanded', 'true'); };
$('close-inspector').onclick = () => { document.querySelector('.app-shell').classList.remove('show-inspector'); $('inspector-toggle').setAttribute('aria-expanded', 'false'); };
$('assets-to-agent').onclick = () => { setPanel('agent'); $('prompt').focus(); };
$('layer-filter').oninput = () => { state.layerLimit = 120; renderLayers(); };
function renderLayers() {
  const root = $('layer-list'), objects = currentSpec()?.objects || [], query = $('layer-filter').value.trim().toLowerCase(); root.replaceChildren();
  const collisions = objects.filter(object => object.visible === false);
  const matches = objects.filter(object => (query || state.collisionLayers || object.visible !== false) && `${object.id} ${object.label || ''}`.toLowerCase().includes(query));
  $('layer-count').textContent = matches.length;
  for (const object of matches.slice(0, state.layerLimit)) {
    const item = el('button', `layer-item${state.selectedObject?.id === object.id ? ' selected' : ''}`); item.type = 'button'; item.setAttribute('aria-pressed', String(state.selectedObject?.id === object.id)); item.title = `${object.label || object.id} · ${object.id}`;
    item.append(el('span', 'layer-icon', object.movable ? '◇' : '▱'), el('span', '', object.label || object.id)); item.onclick = () => selectObject(object); root.append(item);
  }
  if (!matches.length) root.append(el('p', 'sidebar-empty', objects.length ? 'No matching objects.' : 'Your world’s objects will appear here.'));
  if (matches.length > state.layerLimit) { const more = el('button', 'text-button layer-more', `Show more (${state.layerLimit}/${matches.length})`); more.onclick = () => { state.layerLimit += 120; renderLayers(); }; root.append(more); }
  if (collisions.length && !query) {
    const toggle = el('button', 'text-button layer-more', state.collisionLayers ? 'Hide collision components' : `Show collision components (${collisions.length})`);
    toggle.title = 'Collision geometry stays active. This only changes the layers list.';
    toggle.setAttribute('aria-expanded', String(!!state.collisionLayers));
    toggle.onclick = () => { state.collisionLayers = !state.collisionLayers; state.layerLimit = 120; renderLayers(); };
    root.append(toggle);
  }
}
function renderAgentHistory() {
  const root = $('agent-history'); root.replaceChildren();
  if (!state.lineage.length && !state.jobContext) {
    const welcome = el('div', 'agent-welcome'); welcome.append(el('span', 'agent-symbol', '✧'), el('h2', '', state.worlds.length ? 'What’s next?' : 'Create your first world'), el('p', '', 'Describe the place, the robot’s task and what must happen in order. Review the checks before evaluating a policy.'));
    const draft = el('button', 'button welcome-action', 'Draft a factory mission'); draft.onclick = () => { $('prompt').value = 'Create a factory with a parts shelf, an inspection bench and a shipping table connected by clear aisles. A TIAGo++ robot must pick up one identified rigid part, carry it to inspection, then place it on the shipping table and release it. Keep the stations physically reachable and grade identity, ordering and stable final placement.'; $('prompt').focus(); }; welcome.append(draft);
    if (state.worlds.length) { const browse = el('button', 'text-button welcome-action', 'Browse saved worlds →'); browse.onclick = () => setPanel('world'); welcome.append(browse); }
    welcome.append(el('p', 'small muted', 'The draft is editable. Sending it uses your configured model budget.')); root.append(welcome); return;
  }
  for (const entry of [...state.lineage].reverse()) {
    const metadata = entry.world?.manifest?.edit, spec = entry.world?.spec, turn = el('article', 'agent-turn');
    if (metadata?.scope?.length) { const scope = el('div', 'message-context'); for (const id of metadata.scope.slice(0, 3)) scope.append(el('span', '', id)); if (metadata.scope.length > 3) scope.append(el('span', '', `+${metadata.scope.length - 3}`)); turn.append(scope); }
    const prompt = metadata?.prompt || spec?.source_prompt || spec?.task;
    if (prompt) turn.append(el('div', 'user-message', prompt));
    const validation = entry.world?.manifest?.validation || {}, checks = validation.checks || [], activity = el('details', 'activity');
    activity.append(el('summary', '', `Validation · ${checks.filter(check => check.passed).length}/${checks.length} checks passed`));
    const log = el('ul');
    for (const check of checks) { const row = el('li', '', `${check.passed ? 'Pass' : 'Unresolved'} · ${String(check.check || check.name || 'check').replaceAll('_', ' ')}`); for (const error of check.errors || []) row.append(el('p', '', message(error))); if (check.error) row.append(el('p', '', message(check.error))); log.append(row); }
    if (!checks.length) log.append(el('li', '', 'No recorded validation checks.'));
    activity.append(log); turn.append(activity);
    const result = el('div', 'agent-result');
    if (metadata) {
      const counts = metadata.counts || {};
      const parts = ['changed', 'added', 'removed'].filter(key => Number.isFinite(counts[key]) && counts[key]).map(key => `${counts[key]} ${key}`);
      result.append(el('span', '', parts.length ? `${parts.join(' · ')} entities.` : 'No entity changes recorded.'));
      if (Number.isFinite(counts.preserved)) result.append(el('span', '', ` ${counts.preserved} preserved.`));
      const ids = [...new Set((metadata.changes || []).map(change => change.entity_id))].filter(Boolean);
      const entities = [...(spec?.objects || []), ...(spec?.regions || [])];
      const links = el('div');
      for (const id of ids.slice(0, 3)) { const entity = entities.find(item => item.id === id), button = el('button', 'inline-entity', entity?.label || id); button.disabled = !!state.jobId || state.submitting; button.onclick = async () => { try { if (state.worldId !== entry.id) await openWorld(entry.id); const current = currentSpec()?.objects?.find(object => object.id === id); if (current) selectObject(current); else { state.tab = 'changes'; syncTabs(); renderInspector(); } } catch (error) { notice(error.message); } }; links.append(button); }
      result.append(links);
    } else result.append(el('span', '', `Created ${spec?.objects?.length || 0} scene components. ${spec?.robot || 'Robot'} world.`));
    result.append(el('div', 'conversation-status', validation.status === 'ready' ? 'Recorded checks passed. Task success still needs evaluation.' : validation.status === 'preview' ? 'Preview saved. Native validation has not run.' : 'Validation unresolved. Inspect checks or request a repair.'));
    turn.append(result);
    const parent = parentOf(entry.world);
    if (parent) { const previous = versionButton(parent, '↶ Previous version'); previous.classList.add('previous-version'); turn.append(previous); }
    root.append(turn);
  }
  if (state.jobContext?.prompt) { const pending = el('article', 'agent-turn'); pending.append(el('div', 'user-message', state.jobContext.prompt)); root.append(pending); }
  if (state.lineageLoading) root.append(el('p', 'small muted', 'Loading earlier changes…'));
  if (state.lineageError) root.append(el('p', 'small muted', state.lineageError));
  $('agent-scroll').scrollTop = $('agent-scroll').scrollHeight;
}

function renderInspector() {
  const root = $('inspector-body'); root.replaceChildren(); const spec = currentSpec();
  if (!spec) { root.append(el('h3', '', 'World'), el('p', 'small muted', 'Create a world in the Agent panel, then select an object to inspect its properties.')); return; }
  if (state.tab === 'task') {
    root.append(el('h3', '', 'The mission'), el('p', 'task-copy', spec.task));
    const label = el('div', 'inspector-label', 'EXECUTABLE RUBRIC'), edit = el('button', 'text-button', state.editing ? 'Close JSON' : 'Edit JSON');
    edit.disabled = !state.worldId || !!state.jobId; edit.onclick = () => { state.editing = !state.editing; renderInspector(); }; label.append(edit); root.append(label);
    if (state.editing) {
      root.append(el('p', 'small muted', 'Save as a new immutable world version. Existing runs keep their original rubric.'));
      const editor = el('textarea', 'json-editor'); editor.value = pretty(spec.rubric || []); editor.setAttribute('aria-label', 'Editable rubric JSON');
      const actions = el('div', 'edit-actions'), save = el('button', 'button primary', 'Validate & save version');
      save.onclick = async () => { if (state.jobId || state.submitting) return; try { const rubric = JSON.parse(editor.value); if (!Array.isArray(rubric)) throw new Error('Rubric JSON must be an array of criteria.'); const job = await post(`/api/worlds/${encodeURIComponent(state.worldId)}/rubric`, { rubric }); trackJob(job, 'Validating rubric'); } catch (error) { notice(error.message); } };
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
    renderObjectProperties(root, state.selectedObject);
  } else if (state.tab === 'changes') {
    renderChanges(root);
  } else {
    const validation = state.world?.manifest?.validation || { status: 'preview', checks: [] };
    root.append(el('h3', '', 'Validation status'), el('p', 'task-copy', validation.status === 'ready' ? 'Runnable under the recorded checks. Validation does not prove the policy can complete the mission.' : 'This version is not ready for a graded run. Review the unresolved checks below.'));
    for (const check of validation.checks || []) { const item = el('div', 'validation-item'); item.append(el('span', `badge ${check.passed ? 'ready' : 'unvalidated'}`, check.passed ? 'Pass' : 'Unresolved'), el('strong', '', String(check.check || check.name || 'check').replaceAll('_', ' '))); for (const error of check.errors || []) item.append(el('p', '', message(error))); if (check.error) item.append(el('p', '', message(check.error))); root.append(item); }
    if (state.worldId) { const repair = el('button', 'button', 'Repair unresolved checks'); repair.disabled = !!state.jobId || !state.key; repair.onclick = () => setEditMode('repair'); root.append(repair); }
    for (const limitation of validation.limitations || []) root.append(el('p', 'small muted', limitation));
    root.append(el('div', 'inspector-label', 'VERSION'), el('p', 'mono muted', state.worldId || 'Not yet saved'));
    if (state.world?.manifest?.parent_id) root.append(el('p', 'mono muted', `Parent: ${state.world.manifest.parent_id}`));
  }
}
function selectObject(object) {
  state.selectedObject = object; state.viewer?.select(object); syncViewControls();
  if (window.innerWidth <= 900) { const shell = document.querySelector('.app-shell'); shell.classList.add('show-inspector'); shell.classList.remove('show-left'); $('inspector-toggle').setAttribute('aria-expanded', 'true'); }
  state.tab = 'scene'; syncTabs(); renderInspector(); renderLayers();
  $('selection-label').hidden = false; $('selection-label').textContent = `${object.label || object.id} · ${object.id}`;
  if (state.worldId && !state.jobId && !state.submitting) {
    setEditMode('edit', { preserveDraft: true, activate: false }); state.scope = [object.id]; renderScope();
  }
}
function renderObjectProperties(root, object) {
  const spec = currentSpec(), heading = el('div', 'object-heading');
  heading.append(el('strong', '', object?.label || object?.id || 'World'), el('small', '', object ? `${object.kind} · ${object.id}` : spec?.name || 'No selection')); root.append(heading);
  function section(title) { const block = el('section', 'property-section'); block.append(el('h4', '', title)); root.append(block); return block; }
  function dimensions(block, labels, values, unit = 'm') {
    const grid = el('div', 'property-grid'); labels.forEach((label, index) => { const cell = el('div', 'property-cell'), value = values?.[index]; cell.append(el('span', '', label), el('output', '', Number.isFinite(value) ? `${Number(value.toFixed(3))} ${unit}` : '—')); grid.append(cell); }); block.append(grid);
  }
  function row(block, label, value) { const line = el('div', 'property-row'); line.append(el('span', '', label), el('output', '', value == null ? '—' : String(value))); block.append(line); }
  if (!object) {
    dimensions(section('Dimensions'), ['W', 'D', 'H'], spec?.bounds);
    const world = section('Environment'); row(world, 'Robot', spec?.robot); row(world, 'Objects', spec?.objects?.length || 0); row(world, 'Validation', statusOf(state.world));
    root.append(el('p', 'small muted', 'Select an object in the canvas or World layers. Changes are made through the agent.')); return;
  }
  dimensions(section('Position'), ['X', 'Y', 'Z'], object.position);
  dimensions(section('Dimensions'), ['W', 'D', 'H'], object.size);
  const physical = section('Physics'); row(physical, 'Body', object.collision === false ? 'Appearance only' : object.joint ? 'Articulated' : object.movable ? 'Movable' : 'Fixed');
  if (object.collision !== false) row(physical, 'Mass', object.mass == null ? null : `${object.mass} kg`);
  if (object.parent_id) row(physical, 'Parent', object.parent_id);
  if (object.joint) { row(physical, 'Joint', object.joint.type || object.joint.kind); row(physical, 'Axis', object.joint.axis?.join(', ')); }
  if (object.collision !== false && object.friction) row(physical, 'Friction', Array.isArray(object.friction) ? object.friction.join(' / ') : object.friction);
  const appearance = section('Appearance'), color = object.color || [.5, .5, .5], colorRow = el('div', 'color-value'), swatch = el('span', 'object-swatch');
  const components = color.map(value => Math.round(Math.max(0, Math.min(1, value)) * 255)); swatch.style.backgroundColor = `rgb(${components.join(',')})`;
  colorRow.append(swatch, el('output', '', '#' + components.map(value => value.toString(16).padStart(2, '0')).join('').toUpperCase())); appearance.append(colorRow);
  if (object.texture_path) row(appearance, 'Surface', 'Bundled source texture');
  if (object.visible === false) row(appearance, 'Display', 'Collision component; appearance supplied by its assembly');
  if (object.kind === 'mesh') root.append(el('p', 'small muted', object.collision === false ? 'Source visual mesh. Physical contact uses the attached collision components; this mesh adds no mass.' : 'Bundled OBJ geometry is displayed when available. Native contact uses its convex hull.'));
  if (state.worldId) { const edit = el('button', 'button selected-edit', 'Ask agent to change this object'); edit.disabled = !!state.jobId || !state.key; edit.onclick = () => { setEditMode('edit', { preserveDraft: true }); state.scope = [object.id]; renderScope(); }; root.append(edit); }
  root.append(el('p', 'small muted', 'Read-only properties · edit through a text request.'));
}

function syncTabs() { document.querySelectorAll('[data-inspect]').forEach(button => button.classList.toggle('active', button.dataset.inspect === state.tab)); }
document.querySelectorAll('[data-inspect]').forEach(button => { button.onclick = () => { state.tab = button.dataset.inspect; syncTabs(); renderInspector(); }; });
$('physical-tab').onclick = () => setView('physical'); $('layout-tab').onclick = () => setView('layout'); $('fit-view').onclick = () => state.viewer?.fit();
$('focus-object').onclick = () => { if (state.selectedObject) state.viewer?.fit(state.selectedObject); };
$('top-view').onclick = () => state.viewer?.fit(null, [0, -.001, 1]);
$('cutaway-view').onclick = () => { state.viewer?.setCutaway(!state.viewer.cutaway); syncViewControls(); };
function resetWorkspace() {
  $('live-status').hidden = true;
  state.navigation++; disposeViewer(); state.reference = false; $('report-frame').src = 'about:blank';
  document.querySelector('.app-shell').classList.remove('showing-report');
  $('layer-filter').value = ''; state.layerLimit = 120;
  state.world = null; state.worldId = null; state.editing = false; state.selectedObject = null; state.editMode = null; state.selectedObject = null; state.scope = []; state.lineage = []; state.assets = []; renderAssets(); $('edit-context').hidden = true; $('prompt').required = true;
  $('work-area').hidden = false; $('report-area').hidden = true; $('viewport').classList.add('empty'); $('empty-state').hidden = false;
  for (const id of ['scene-canvas', 'scene-labels', 'physical-image', 'viewport-caption', 'selection-label', 'world-status', 'view-state']) $(id).hidden = true;
  for (const id of ['physical-tab', 'layout-tab', 'fit-view', 'focus-object', 'top-view', 'cutaway-view']) $(id).disabled = true;
  $('world-title').textContent = 'Untitled world'; $('workspace-eyebrow').textContent = 'Robot Worlds · local workspace'; $('viewport-info').textContent = 'No environment loaded';
  $('prompt-label').textContent = 'What should the robot do, and where?'; $('generate-button').firstChild.textContent = 'Generate world '; $('prompt').value = ''; $('prompt').placeholder = 'Describe a world and a task…'; $('generate-button').setAttribute('aria-label', 'Generate world'); $('scene-profile').disabled = false;
  setPanel('agent'); renderLayers(); renderAgentHistory(); controls(); renderInspector(); renderLists(); history.replaceState(null, '', '/'); $('prompt').focus();
}
$('new-world').onclick = resetWorkspace;
$('refine-button').onclick = () => setEditMode('edit');
$('repair-button').onclick = () => setEditMode('repair');
$('leave-edit').onclick = resetWorkspace;
$('add-scope').onclick = () => addScope($('scope-input').value.trim());
$('scope-input').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); addScope(event.target.value.trim()); } };
$('generate-form').onsubmit = async event => {
  event.preventDefault(); if (state.jobId || state.submitting) return;
  let prompt = $('prompt').value.trim();
  if (!prompt && state.editMode !== 'repair') return;
  if (!prompt) prompt = 'Fix unresolved validation failures while preserving the task and physical properties.';
  const mode = state.editMode, parentId = state.worldId;
  if (mode && !parentId) { notice('Select a saved parent version before editing.'); return; }
  state.submitting = true; controls();
  try {
    notice('');
    const path = mode ? `/api/worlds/${encodeURIComponent(parentId)}/${mode === 'repair' ? 'repair' : 'refine'}` : '/api/generate';
    const assetNote = state.assets.length ? '\nAttached mesh names: ' + state.assets.map(a => `${a.name}: ${a.id}`).join('; ') : '';
    const payload = { prompt: prompt + assetNote, ...settings(), max_cost_usd: Number($('generation-cost').value), max_model_seconds: Number($('generation-seconds').value), max_repairs: 2, asset_ids: state.assets.map(a => a.id) };
    if (mode) Object.assign(payload, { scope: state.scope.length ? [...state.scope] : null, allow_task_changes: mode === 'edit' && $('allow-task-changes').checked, allow_physics_changes: mode === 'edit' && $('allow-physics-changes').checked });
    else Object.assign(payload, { scene_profile: $('scene-profile').value, min_objects: $('scene-profile').value === 'large' ? 500 : 0 });
    const job = await post(path, payload);
    state.jobContext = { mode, parentId, prompt };
    trackJob(job, mode === 'repair' ? 'Repairing validation failures' : mode === 'edit' ? 'Applying scoped changes' : 'Generating world');
  } catch (error) { notice(error.message); }
  finally { state.submitting = false; controls(); }
};

function scopeEntities() { const spec = currentSpec(); return [...(spec?.objects || []), ...(spec?.regions || []), ...(spec?.rubric || []), ...(spec?.process_cycles || [])]; }
function setEditMode(mode, { preserveDraft = false, activate = true } = {}) {
  if (!state.worldId || state.jobId || state.submitting) return;
  const draft = $('prompt').value;
  state.editMode = mode; if (!preserveDraft) state.scope = [];
  $('edit-context').hidden = false;
  $('edit-operation').textContent = mode === 'repair' ? 'REPAIR' : 'EDIT';
  $('edit-parent').textContent = `From version ${shorten(state.worldId, 12)}`;
  $('edit-context-copy').textContent = mode === 'repair' ? 'Repair unresolved checks in a new version. The task, rubric and physical properties stay fixed.' : 'Describe the next change. A new version keeps the parent available and records the actual property differences.';
  $('edit-permissions').hidden = mode === 'repair';
  $('allow-task-changes').checked = false; $('allow-physics-changes').checked = false;
  $('prompt').value = preserveDraft ? draft : ''; $('prompt').required = mode !== 'repair';
  $('prompt').placeholder = mode === 'repair' ? 'Optional: name the failing check or describe the repair you want.' : 'For example: move the inspection table 0.5 m to the left and keep the other workcells in place.';
  $('prompt-label').textContent = mode === 'repair' ? 'What should the repair focus on?' : 'What should change in this version?';
  $('generate-button').firstChild.textContent = mode === 'repair' ? 'Repair ' : 'Send edit ';
  $('generate-button').setAttribute('aria-label', mode === 'repair' ? 'Repair world' : 'Save edited world version');
  $('scene-profile').disabled = true;
  $('scope-options').replaceChildren();
  for (const entity of scopeEntities()) { const option = el('option'); option.value = entity.id; option.label = entity.label || entity.id; $('scope-options').append(option); }
  renderScope(); if (activate) { setPanel('agent'); $('prompt').focus(); } controls();
}
function addScope(id) {
  if (state.jobId || state.submitting || !id) return;
  const entities = scopeEntities();
  if (!entities.some(entity => entity.id === id)) { notice('Choose an existing object, region, criterion or process ID from this version.'); return; }
  if (!state.scope.includes(id)) { if (state.scope.length >= 1000) { notice('Limit one edit to at most 1,000 entity IDs.'); return; } state.scope.push(id); }
  $('scope-input').value = ''; renderScope(); notice('');
}
function renderScope() {
  $('scope-chips').replaceChildren();
  if (!state.scope.length) $('scope-chips').append(el('span', 'small muted', 'Whole world'));
  for (const id of state.scope) { const chip = el('span', 'scope-chip', id), remove = el('button', '', '×'); remove.type = 'button'; remove.setAttribute('aria-label', `Remove ${id} from edit scope`); remove.onclick = () => { state.scope = state.scope.filter(value => value !== id); renderScope(); }; chip.append(remove); $('scope-chips').append(chip); }
}
function parentOf(world) { return world?.manifest?.edit?.parent_world_id || world?.manifest?.parent_id; }
async function loadLineage(id, world) {
  state.lineage = [{ id, world }]; state.lineageLoading = true; state.lineageError = '';
  renderAgentHistory();
  if (state.tab === 'changes') renderInspector();
  const lineage = [{ id, world }], seen = new Set([id]);
  try {
    let parent = parentOf(world);
    while (parent && lineage.length < 12 && !seen.has(parent)) {
      const previous = await api(`/api/worlds/${encodeURIComponent(parent)}`);
      if (state.worldId !== id) return;
      seen.add(parent); lineage.push({ id: parent, world: previous }); parent = parentOf(previous);
    }
    if (parent && lineage.length >= 12) state.lineageError = 'Showing the latest 12 versions. Open the oldest listed version to continue.';
  } catch (error) { if (state.worldId === id) state.lineageError = `Earlier version unavailable: ${error.message}`; }
  if (state.worldId !== id) return;
  state.lineage = lineage; state.lineageLoading = false; renderAgentHistory();
  if (state.tab === 'changes') renderInspector();
}
function versionButton(id, label) {
  const button = el('button', 'text-button', label); button.disabled = !!state.jobId || state.submitting;
  button.onclick = () => openWorld(id).catch(error => notice(error.message)); return button;
}
function renderChanges(root) {
  const edit = state.world?.manifest?.edit, parent = parentOf(state.world);
  root.append(el('h3', '', 'Changes in this version'));
  if (!state.worldId || state.isPreview) { root.append(el('p', 'edit-empty', 'The candidate is still being checked. The saved version will include its computed changes.')); return; }
  if (parent) { const actions = el('div', 'parent-actions'); actions.append(versionButton(parent, '← Open parent version')); root.append(actions); }
  if (!edit) root.append(el('p', 'edit-empty', parent ? 'This version has no recorded edit diff. Its parent remains available; no computed preservation claim is made.' : 'This is the starting version. Edit or repair it to create a version with a computed change record.'));
  else {
    root.append(el('p', 'small muted', `${edit.kind || 'edit'} · attempt ${edit.attempt ?? '—'} · ${statusOf(state.world)}`));
    if (edit.prompt) root.append(el('p', 'task-copy', edit.prompt));
    if (edit.scope?.length) root.append(el('p', 'small muted', `Requested scope: ${edit.scope.join(', ')}`));
    const counts = edit.counts || {}, summary = el('div', 'changes-summary');
    for (const key of ['changed', 'added', 'removed', 'preserved']) if (Number.isFinite(counts[key])) summary.append(el('span', 'badge', `${counts[key]} ${key}`));
    root.append(summary, el('p', 'small muted', 'Entity counts and property differences are computed from the saved versions. Validation is a separate result.'));
    if (edit.assembly_materialized) root.append(el('p', 'small muted', 'Repeated assemblies were expanded into editable entities for this version.'));
    if (edit.protected) root.append(el('p', 'small muted', 'Preservation constraints: ' + (typeof edit.protected === 'string' ? edit.protected : pretty(edit.protected))));
    const changes = Array.isArray(edit.changes) ? edit.changes : [], list = el('div'); root.append(list);
    if (!changes.length) list.append(el('p', 'edit-empty', 'No property changes were recorded.'));
    let shown = 0;
    const more = el('button', 'text-button');
    function appendChanges() {
      const end = Math.min(shown + 50, changes.length);
      for (const change of changes.slice(shown, end)) {
        const item = el('div', 'change-item'); item.append(el('strong', '', change.entity_id || 'World'), el('div', 'change-property', change.property || 'value'));
        const values = el('div', 'change-values');
        values.append(el('pre', 'before', 'Before: ' + (change.before === undefined ? '(absent)' : pretty(change.before))), el('pre', 'after', 'After: ' + (change.after === undefined ? '(absent)' : pretty(change.after)))); item.append(values); list.append(item);
      }
      shown = end; more.hidden = shown >= changes.length; more.textContent = `Show next ${Math.min(50, changes.length - shown)} changes (${shown}/${changes.length})`;
    }
    more.onclick = appendChanges; appendChanges(); root.append(more);
  }
  root.append(el('div', 'inspector-label', 'VERSION HISTORY'), el('p', 'small muted', 'Open an earlier version to branch from it. Selecting a version never deletes later work.'));
  const history = el('div', 'version-history'); root.append(history);
  for (const entry of state.lineage) {
    const item = el('div', `version-card${entry.id === state.worldId ? ' current' : ''}`), metadata = entry.world?.manifest?.edit;
    item.append(versionButton(entry.id, `${entry.id === state.worldId ? 'Current · ' : ''}${shorten(entry.id, 12)}`), el('small', '', `${metadata?.kind || 'created'} · ${statusOf(entry.world)}`));
    item.append(el('p', '', metadata?.prompt || entry.world?.spec?.source_prompt || entry.world?.spec?.task || 'World version')); history.append(item);
  }
  if (state.lineageLoading) root.append(el('p', 'small muted', 'Loading earlier versions…'));
  if (state.lineageError) root.append(el('p', 'small muted', state.lineageError));
}

function eventLabel(event) {
  if (event.type === 'asset_loading') return event.message || 'Loading prepared asset';
  if (event.type === 'asset_loaded') return event.message || 'Prepared asset ready';
  if (event.type === 'policy_started') return `Choosing action ${event.step + 1}`;
  if (event.type === 'action_started') return `Action ${event.step + 1}: ${(event.action?.tool || 'move').replaceAll('_', ' ')}`;
  if (event.type === 'step') return `Feedback: ${(event.result?.status || 'executed').replaceAll('_', ' ')}`;
  if (event.type === 'task_progress') return `${(event.criteria || []).filter(c => c.satisfied).length}/${(event.criteria || []).length} checks observed`;
  const labels = { edit_started: 'Planning targeted changes', repair_started: 'Diagnosing unresolved checks', candidate: 'Candidate ready for checks', preview: 'Candidate preview', validation: 'Checking geometry and physics', edit_validated: 'Verifying preserved entities', edit_completed: 'Saved version and computed changes', generation_incomplete: 'Budget exhausted · saved unresolved candidate', generation_cancelled: 'Cancelled · preserving available evidence' };
  const type = labels[event.type] || String(event.type || event.phase || 'update').replaceAll('_', ' ');
  const detail = event.message || (event.errors?.length ? event.errors.map(message).join('; ') : event.error ? message(event.error) : '');
  return detail ? `${type}: ${detail}` : type;
}
function trackJob(job, label) {
  const id = job.id || job.job_id; if (!id) { notice('The server returned no job identifier.'); return; }
  setPanel('agent'); renderAgentHistory();
  state.jobId = id; state.eventSeen = new Set(); $('job-panel').hidden = false; $('job-panel').classList.remove('done', 'failed'); $('job-title').textContent = label; $('job-state').textContent = job.status || 'queued'; $('job-events').replaceChildren(); $('cancel-job').hidden = false; $('cancel-job').textContent = 'Cancel'; controls(); history.replaceState(null, '', `/?job=${encodeURIComponent(id)}`); pollJob(id);
}
function showLiveFrame(frame) {
  if (!/^[a-f0-9]{32}$/.test(frame.run_id || '') || !/^episode-\d{3}\/live\.png$/.test(frame.path || '') || !Number.isFinite(frame.sequence) || !Number.isFinite(frame.time)) return;
  if (state.liveFrameKey === `${frame.run_id}:${frame.episode}:${frame.sequence}`) return;
  if (state.mode !== 'live') {
    $('live-progress').hidden = true;
    $('agent-history').replaceChildren(el('h2', '', 'Your robot is on the job.'), el('p', 'small muted', 'Watch its actions and measured task progress. You can cancel the run at any time.'));
    if (!state.world) $('world-title').textContent = 'Live robot mission';
    $('workspace-eyebrow').textContent = 'AUTONOMOUS EXECUTION';
    $('connection').textContent = '● Watching run'; $('connection').classList.remove('error');
  }
  state.liveFrameKey = `${frame.run_id}:${frame.episode}:${frame.sequence}`;
  state.viewer?.setActive(false); state.mode = 'live';
  $('work-area').hidden = false; $('report-area').hidden = true;
  document.querySelector('.app-shell').classList.remove('showing-report');
  $('viewport').classList.remove('empty'); $('empty-state').hidden = true;
  $('scene-canvas').hidden = true; $('scene-labels').hidden = true; $('selection-label').hidden = true;
  $('physical-image').hidden = false;
  $('physical-image').src = `/api/runs/${frame.run_id}/files/${frame.path}?frame=${frame.sequence}`;
  $('live-status').hidden = false; $('live-time').textContent = `${frame.time.toFixed(1)} sim s`;
  $('view-state').hidden = false; $('view-state').textContent = 'LIVE ROBOT';
  $('viewport-info').textContent = 'Live simulation · spectator camera';
  $('viewport-caption').hidden = false; $('viewport-caption').textContent = 'Live spectator camera · simulation advances between model decisions · final success is independently graded.';
  for (const id of ['physical-tab', 'layout-tab', 'fit-view', 'focus-object', 'top-view', 'cutaway-view']) $(id).disabled = true;
}
function showLiveEvent(event) {
  if (event.type === 'run_finished') {
    $('live-phase').textContent = 'Saving result';
    $('live-action').textContent = 'Execution finished · preparing the recorded report';
  } else if (event.type === 'policy_started') {
    $('live-phase').textContent = 'Thinking';
    $('live-action').textContent = `Choosing action ${event.step + 1} from camera and robot observations`;
  } else if (event.type === 'action_started') {
    const names = {drive: 'Driving', move_arm: 'Moving arm', gripper: 'Moving gripper', wait: 'Waiting', look: 'Looking', finish: 'Checking the result'};
    $('live-phase').textContent = names[event.action?.tool] || 'Acting';
    const args = event.action?.arguments || {}, hand = args.hand || 'robot';
    const detail = event.action?.tool === 'drive' ? `Moving ${(args.distance || 0).toFixed(2)} m · turning ${Math.round((args.turn || 0)*180/Math.PI)}°` : event.action?.tool === 'move_arm' ? `Reaching with the ${hand} arm` : event.action?.tool === 'gripper' ? `${args.opening > 0 ? 'Opening' : 'Closing'} the ${hand} gripper` : event.action?.tool === 'wait' ? `Waiting ${args.seconds || 0} simulated seconds` : 'Following the task using measured feedback';
    $('live-action').textContent = detail;
  } else if (event.type === 'step') {
    $('live-action').textContent = `${event.action?.tool || 'Action'} · ${event.result?.status || 'executed'}`;
  } else if (event.type === 'task_progress') {
    const criteria = event.criteria || [], observed = criteria.filter(criterion => criterion.satisfied === true).length;
    $('live-progress').hidden = !criteria.length;
    $('live-progress-bar').max = criteria.length || 1; $('live-progress-bar').value = observed;
    $('live-progress-label').textContent = `${observed} / ${criteria.length} checks observed · provisional`;
    const panel = $('inspector-body'); panel.replaceChildren(el('div', 'inspector-label', 'TASK PROGRESS'), el('p', 'small muted', 'Observed milestones. Final conditions are checked again when the run ends.'));
    for (const criterion of criteria) {
      const card = el('div', `live-goal${criterion.satisfied ? ' observed' : ''}`);
      card.append(el('span', 'live-goal-icon', criterion.satisfied ? '✓' : '○'), el('span', '', criterion.description));
      panel.append(card);
    }
  }
}
async function pollJob(id) {
  if (state.jobId !== id) return;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`); $('job-state').textContent = job.status;
    if (job.phase === 'generation') $('job-title').textContent = 'Generating world';
    else if (job.phase === 'execution') $('job-title').textContent = 'Running robot';
    else if (job.phase === 'finalizing') $('job-title').textContent = 'Saving result';
    if (job.live_frame) showLiveFrame(job.live_frame);
    for (const event of job.events || []) {
      const signature = JSON.stringify(event); if (state.eventSeen.has(signature)) continue; state.eventSeen.add(signature);
      const row = el('li', event.error || event.errors?.length ? 'stage-error' : '', eventLabel(event)); $('job-events').append(row);
      showLiveEvent(event);
      if (event.type === 'preview' && event.spec) { state.worldId = null; state.world = { spec: event.spec, manifest: { validation: { status: 'preview', checks: [] } } }; displaySpec(event.spec, true); }
    }
    $('job-events').scrollTop = $('job-events').scrollHeight;
    if (['completed', 'completed_with_errors', 'succeeded', 'done', 'failed', 'error', 'cancelled', 'interrupted'].includes(job.status)) {
      const context = state.jobContext; state.jobContext = null;
      state.jobId = null; $('job-panel').classList.add('done'); $('job-panel').classList.toggle('failed', ['failed', 'error', 'interrupted'].includes(job.status)); $('cancel-job').hidden = true; controls();
      if (job.error) notice(message(job.error));
      await refreshLists();
      if (job.run_id) openReport(job.run_id);
      else if (job.world_id) { await openWorld(job.world_id); state.tab = context?.mode ? 'changes' : statusOf(state.world) !== 'ready' ? 'validation' : state.tab; syncTabs(); renderInspector(); if (statusOf(state.world) !== 'ready') notice('A version was saved with unresolved checks. Review Changes and Validation, then request a targeted repair.'); }
      else { if (context?.parentId) { await openWorld(context.parentId); setEditMode(context.mode || 'edit'); $('prompt').value = context.prompt || ''; } if (job.status === 'cancelled') notice('Job cancelled. Your parent version and any saved candidates remain available.'); }
      if (job.error) notice(message(job.error));
      return;
    }
  } catch (error) { notice(`Unable to read job status: ${error.message}`); }
  setTimeout(() => pollJob(id), state.mode === 'live' ? 250 : 1000);
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
  try { const health = await api('/api/health'); state.key = !!health.key_configured; $('connection').textContent = state.key ? '● Connected' : 'Key required'; $('connection').title = state.key ? 'Local runtime · model key configured' : 'Configure OPENAI_API_KEY on the server'; $('connection').classList.toggle('error', !state.key); if (!state.key) notice('Set OPENAI_API_KEY in the server environment, then restart the local server to generate worlds or run the agent. Saved worlds and reports remain available.'); if (health.model) $('model').value = health.model; await refreshLists(); const params = new URLSearchParams(location.search); if (params.get('reference')) await openReference(params.get('reference'));
    else if (params.get('job')) { notice(''); const job = await api(`/api/jobs/${encodeURIComponent(params.get('job'))}`); trackJob(job, 'Live robot mission'); }
    else if (params.get('run')) openReport(params.get('run')); else if (params.get('world')) await openWorld(params.get('world')); }
  catch (error) { notice(error.message); $('connection').textContent = 'Local server unavailable'; $('connection').classList.add('error'); }
  if (!state.world && !state.jobId) renderAgentHistory();
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
  if (state.jobId || state.submitting) return;
  const navigation = ++state.navigation;
  notice('Loading the source geometry and materials…');
  const [manifest, data, spec, environment] = await Promise.all([api(base + 'reference-manifest.json'), api(base + 'visual-scene.json'), api(base + 'world.json'), api(base + 'environment-scene.json').catch(() => null)]);
  if (navigation !== state.navigation) return;
  state.world = { spec, manifest: { validation: { status: 'reference', checks: [] } } }; state.worldId = null; state.editing = false; state.editMode = null; state.scope = []; state.lineage = []; renderAgentHistory(); $('edit-context').hidden = true; $('prompt').required = true; $('scene-profile').disabled = false;
  $('prompt-label').textContent = 'Describe a new environment and task'; $('generate-button').firstChild.textContent = 'Generate world ';
  displaySpec(spec, true); await getViewer().setReference(data, manifest, environment); state.reference = true; syncViewControls(); notice('');
  $('world-title').textContent = 'Airsup production line'; $('workspace-eyebrow').textContent = 'Static reference study';
  $('world-status').textContent = 'Reference study'; $('world-status').className = 'badge preview';
  $('viewport-caption').textContent = 'Source geometry at the original camera angle · frozen reference state · lighting differs.';
  $('viewport-info').textContent = `${manifest.counts?.visible_meshes || manifest.components?.length || ''} source meshes · MIT`;
  $('physical-tab').disabled = true; state.tab = 'scene'; syncTabs(); renderInspector();
  const note = el('div', 'notice', 'Source-derived geometry, including visual-only screens and effects. This view is not a policy rollout or a physics-validation result.'); $('inspector-body').prepend(note);
  controls(); history.replaceState(null, '', `/?reference=${encodeURIComponent(id)}`);
}
