// Provider-backed profile authoring for the Cookbook.
//
// Outis owns the authoring experience; the external ProfileService owns the
// profile schema, validation, path resolution, and persistence. Every field
// rendered here is discovered from the service's form document — no profile
// field is named in this file or in its model.
//
// Contract normalization and the pure editor state machine live in
// frontend/cookbookProfileEditorModel.ts. Keep DOM work here.

import {
  applyConflict,
  applyPreview,
  applySaved,
  applyWriteFailure,
  artifactRefFor,
  authorityAccepted,
  beginDraft,
  beginEdit,
  canDelete,
  canSubmit,
  clearEditor,
  createEditorState,
  fieldAllowed,
  fieldConstraints,
  fieldItemOptions,
  fieldKind,
  fieldLabel,
  formatFieldValue,
  formLayout,
  isDirty,
  localFieldHints,
  nextPreviewToken,
  parseFormDocument,
  profileFromEnvelope,
  profileSummaries,
  resolveConflictWithLocal,
  resolveConflictWithRemote,
  setFieldValue,
  submissionValues,
  widgetForField,
} from './generated/cookbookProfileEditorModel.js';

export {
  artifactRefFor,
  formLayout,
  parseFormDocument,
  profileSummaries,
  widgetForField,
};

const PREFIX = '/api/cookbook/profile-service';

// Long enough that ordinary typing does not fire a request per keystroke,
// short enough that feedback still feels attached to the edit.
const PREVIEW_DEBOUNCE_MS = 600;

let _available = false;
let _provider = null;
let _service = null;
let _form = null;
let _profiles = [];
let _state = createEditorState();
let _artifactContext = null;
// Describes the artifact the OPEN profile is bound to, which is not the same
// thing as the inventory's current selection and must never be re-derived from
// it: the binding is fixed when the draft is seeded or the profile is read.
let _boundArtifact = null;
let _loaded = false;
let _busy = false;
let _previewTimer = null;
// Which write was refused, so the conflict offers the right choice: a delete
// has no local edits to weigh against the provider's version.
let _conflictOperation = 'Save';
// Field specs in render order, so a control can be resolved from its index
// without interpolating a provider-supplied id into a DOM id.
let _fieldsByIndex = [];

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function _el(id) {
  return document.getElementById(id);
}

// ── transport ──

async function _request(method, path, { body, ifMatch, fetchImpl = globalThis.fetch } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  // Only ever an explicit expected-version. A wildcard would turn a stale
  // draft into a silent overwrite of someone else's newer profile.
  if (ifMatch) headers['If-Match'] = ifMatch;
  const init = { method, headers, credentials: 'same-origin' };
  if (body !== undefined) init.body = JSON.stringify(body);
  const response = await fetchImpl(`${PREFIX}${path}`, init);
  let payload = null;
  try { payload = await response.json(); } catch {}
  return {
    ok: response.ok,
    status: response.status,
    body: payload,
    etag: response.headers?.get?.('ETag') || null,
  };
}

function _transportMessage(error) {
  return error?.message || 'The profile service could not be reached.';
}

// ── rendering ──

function _controlHtml(field, index, value) {
  const widget = widgetForField(field, _form);
  const domId = `cookbook-profile-field-${index}`;
  const describedBy = `${domId}-help ${domId}-feedback`;
  const common = `id="${domId}" data-field-index="${index}" aria-describedby="${esc(describedBy)}"`;
  const nullable = field.nullable !== false;

  if (widget === 'toggle' || fieldKind(field) === 'boolean') {
    return `<input type="checkbox" class="cookbook-profile-toggle" ${common}${value === true ? ' checked' : ''} />`;
  }
  if (widget === 'select' || (fieldKind(field) === 'enum' && fieldAllowed(field).length)) {
    const options = fieldAllowed(field);
    const current = value === null || value === undefined ? '' : String(value);
    const blank = nullable || !options.includes(current)
      ? `<option value=""${current === '' ? ' selected' : ''}>—</option>`
      : '';
    const rendered = options.map(option => (
      `<option value="${esc(option)}"${option === current ? ' selected' : ''}>${esc(option)}</option>`
    )).join('');
    return `<select class="cookbook-profile-select" ${common}>${blank}${rendered}</select>`;
  }
  if (widget === 'chips' || fieldKind(field) === 'list') {
    const selected = (Array.isArray(value) ? value : []).map(item => String(item));
    const options = fieldItemOptions(field);
    if (options.length) {
      const boxes = options.map((option, optionIndex) => `
        <label class="cookbook-profile-chip">
          <input type="checkbox" data-field-index="${index}" data-chip-value="${esc(option)}"
                 id="${domId}-${optionIndex}"${selected.includes(option) ? ' checked' : ''} />
          <span>${esc(option)}</span>
        </label>`).join('');
      // A group of checkboxes is not a labelable control, so it is named by
      // the field's label rather than pointed at by a `for` attribute.
      return `<div class="cookbook-profile-chips" role="group" aria-labelledby="${domId}-label" aria-describedby="${esc(describedBy)}" data-field-index="${index}">${boxes}</div>`;
    }
    return `<input type="text" class="cookbook-profile-input" ${common} value="${esc(selected.join(', '))}" placeholder="comma separated" />`;
  }
  if (widget === 'number' || fieldKind(field) === 'integer') {
    const constraints = fieldConstraints(field);
    const min = constraints.min === undefined ? '' : ` min="${esc(constraints.min)}"`;
    const max = constraints.max === undefined ? '' : ` max="${esc(constraints.max)}"`;
    return `<input type="number" class="cookbook-profile-input" ${common}${min}${max} value="${esc(formatFieldValue(field, value))}" />`;
  }
  // Anything else, including a widget this editor does not recognise, stays
  // editable as text rather than disappearing from a lossy replace.
  return `<input type="text" class="cookbook-profile-input" ${common} value="${esc(formatFieldValue(field, value))}" />`;
}

function _isChipGroup(field) {
  const widget = widgetForField(field, _form);
  return (widget === 'chips' || fieldKind(field) === 'list') && fieldItemOptions(field).length > 0;
}

function _fieldHtml(field, index, value) {
  const domId = `cookbook-profile-field-${index}`;
  const help = typeof field.help === 'string' && field.help ? field.help : '';
  const required = field.nullable === false
    ? '<span class="cookbook-profile-required" aria-hidden="true">*</span>'
    : '';
  const naming = _isChipGroup(field) ? '' : ` for="${domId}"`;
  return `
    <div class="cookbook-profile-field" data-field-index="${index}">
      <label class="cookbook-profile-label" id="${domId}-label"${naming}>${esc(fieldLabel(field))}${required}</label>
      ${_controlHtml(field, index, value)}
      <div class="cookbook-profile-help" id="${domId}-help">${esc(help)}</div>
      <div class="cookbook-profile-feedback" id="${domId}-feedback"></div>
    </div>`;
}

function _renderForm() {
  const host = _el('cookbook-profile-form');
  if (!host) return;
  _fieldsByIndex = [];
  if (_state.mode === 'idle' || !_form) {
    host.innerHTML = '';
    return;
  }
  const groups = formLayout(_form);
  let index = 0;
  const sections = groups.map(group => {
    const fields = group.fields.map(field => {
      const html = _fieldHtml(field, index, _state.values[field.id] ?? null);
      _fieldsByIndex.push(field);
      index += 1;
      return html;
    }).join('');
    return `
      <section class="cookbook-profile-group">
        <h3>${esc(group.label)}</h3>
        <div class="cookbook-profile-group-fields">${fields}</div>
      </section>`;
  }).join('');
  host.innerHTML = sections;
  _renderFeedback();
}

function _renderFeedback() {
  const hints = _form ? localFieldHints(_form, _state.values) : {};
  _fieldsByIndex.forEach((field, index) => {
    const node = _el(`cookbook-profile-field-${index}-feedback`);
    if (!node) return;
    // The provider is authoritative: its message replaces the local hint for
    // that field rather than stacking with it.
    const errors = _state.fieldErrors[field.id];
    const messages = errors && errors.length ? errors : (hints[field.id] || []);
    const isError = Boolean(errors && errors.length);
    node.className = `cookbook-profile-feedback${messages.length ? (isError ? ' is-error' : ' is-hint') : ''}`;
    node.textContent = messages.join(' ');
    const control = _el(`cookbook-profile-field-${index}`);
    if (control) control.setAttribute('aria-invalid', isError ? 'true' : 'false');
  });
  _renderBanner();
  _renderActions();
}

function _renderBanner() {
  const host = _el('cookbook-profile-banner');
  if (!host) return;
  const parts = [];
  if (_state.conflict) {
    // A delete conflict has no local edits to weigh, so the choice is only
    // about which version the retry acts on.
    const deleting = _conflictOperation === 'Delete';
    const remoteLabel = deleting ? 'Load their version' : 'Discard my edits, load theirs';
    const localLabel = deleting ? 'Keep mine and retry' : 'Keep my edits and overwrite';
    parts.push(`
      <div class="cookbook-profile-conflict">
        <p>${esc(_state.conflict.message)}</p>
        <div class="cookbook-profile-conflict-actions">
          <button type="button" class="hwfit-gpu-btn" id="cookbook-profile-take-remote">${esc(remoteLabel)}</button>
          <button type="button" class="hwfit-gpu-btn" id="cookbook-profile-take-local">${esc(localLabel)}</button>
        </div>
      </div>`);
  }
  for (const error of _state.formErrors) {
    const where = error.pointer && error.pointer !== '/' ? `${error.pointer}: ` : '';
    parts.push(`<div class="cookbook-profile-message is-error">${esc(where)}${esc(error.message)}</div>`);
  }
  for (const warning of _state.warnings) {
    parts.push(`<div class="cookbook-profile-message is-warning">${esc(warning.message)}</div>`);
  }
  host.innerHTML = parts.join('');
}

function _renderActions() {
  const save = _el('cookbook-profile-save');
  const revert = _el('cookbook-profile-revert');
  const remove = _el('cookbook-profile-delete');
  const dirty = isDirty(_state);
  if (save) {
    save.disabled = _busy || !canSubmit(_state);
    save.textContent = _state.mode === 'new' ? 'Create profile' : 'Save changes';
  }
  if (revert) revert.disabled = _busy || !dirty;
  if (remove) {
    // Hidden rather than disabled while drafting: there is nothing persisted
    // for it to act on, so offering it at all would be misleading.
    remove.hidden = _state.mode !== 'editing';
    remove.disabled = _busy || !canDelete(_state);
  }
}

function _setStatus(message, kind = '') {
  const node = _el('cookbook-profile-status');
  if (!node) return;
  node.textContent = message;
  node.classList.toggle('is-error', kind === 'error');
  node.classList.toggle('is-empty', kind === 'empty');
}

function _artifactLabel(artifact) {
  return artifact.filename || artifact.logical_path || artifact.id;
}

function _refLabel(ref) {
  if (!ref || typeof ref.artifact_id !== 'string' || !ref.artifact_id) return null;
  return ref.artifact_id;
}

function _renderContext() {
  const node = _el('cookbook-profile-context');
  const create = _el('cookbook-profile-new');
  if (!node) return;
  // A profile exists to launch one specific artifact, so while one is open the
  // line names the artifact THAT profile is bound to. Showing the inventory's
  // current selection here would misreport the single most load-bearing fact
  // about the draft the moment the operator browsed anywhere else.
  if (_state.mode !== 'idle') {
    node.textContent = _boundArtifact
      ? `Profile for: ${_boundArtifact}`
      : 'This profile records no artifact.';
    node.classList.toggle('is-error', !_boundArtifact);
    if (create) create.disabled = _busy || !_artifactContext || !_form;
    return;
  }
  if (!_artifactContext || !_form) {
    node.textContent = 'Select an artifact in the Inventory tab to start a profile.';
    node.classList.remove('is-error');
    if (create) create.disabled = true;
    return;
  }
  const { artifact, provider } = _artifactContext;
  const name = artifact.filename || artifact.logical_path || artifact.id;
  const authority = provider?.id || '';
  const accepted = authorityAccepted(_service, authority);
  node.textContent = accepted
    ? `Selected artifact: ${name}`
    : `Selected artifact: ${name} — this service does not list "${authority}" among the authorities it accepts.`;
  node.classList.toggle('is-error', !accepted);
  if (create) create.disabled = _busy;
}

function _renderProfileList() {
  const host = _el('cookbook-profile-list');
  if (!host) return;
  if (!_profiles.length) {
    host.innerHTML = '<div class="cookbook-profile-empty">No profiles yet.</div>';
    return;
  }
  host.innerHTML = _profiles.map(profile => `
    <button type="button" class="cookbook-profile-entry${profile.id === _state.profileId ? ' is-active' : ''}"
            data-profile-id="${esc(profile.id)}">${esc(profile.label)}</button>`).join('');
}

function _renderEditorShell() {
  const editor = _el('cookbook-profile-editor');
  const heading = _el('cookbook-profile-editing');
  if (heading) {
    heading.textContent = _state.mode === 'new'
      ? 'New profile'
      : (_state.mode === 'editing' ? `Editing ${_state.profileId}` : '');
  }
  if (editor) editor.hidden = _state.mode === 'idle';
}

function _renderAll() {
  _renderContext();
  _renderProfileList();
  _renderEditorShell();
  _renderForm();
}

// ── loading ──

export async function loadProfiles({ force = false, fetchImpl = globalThis.fetch } = {}) {
  if (!_available) {
    _setStatus('Configure an external ProfileService provider to author profiles.', 'empty');
    return null;
  }
  if (_loaded && !force) {
    _renderAll();
    return _form;
  }
  _setStatus('Loading profile service…');
  const refresh = _el('cookbook-profile-refresh');
  refresh?.classList.add('spinning');
  if (refresh) refresh.disabled = true;
  try {
    const service = await _request('GET', '', { fetchImpl });
    if (!service.ok) throw new Error(_envelopeMessage(service.body) || `Discovery failed (HTTP ${service.status})`);
    _service = service.body;

    const form = await _request('GET', '/form', { fetchImpl });
    if (!form.ok) throw new Error(_envelopeMessage(form.body) || `Form request failed (HTTP ${form.status})`);
    const parsed = parseFormDocument(form.body);
    if (!parsed) throw new Error('The profile service returned an unusable form document.');
    _form = parsed;

    await _loadProfileList({ fetchImpl });
    _loaded = true;
    const providerName = _service?.provider_name || _provider;
    const providerNode = _el('cookbook-profile-provider');
    if (providerNode) providerNode.textContent = providerName ? `Provider: ${providerName}` : 'External provider';
    _setStatus(`${_profiles.length} profile${_profiles.length === 1 ? '' : 's'}.`);
    _renderAll();
    return _form;
  } catch (error) {
    _loaded = false;
    _setStatus(_transportMessage(error), 'error');
    return null;
  } finally {
    refresh?.classList.remove('spinning');
    if (refresh) refresh.disabled = !_available;
  }
}

async function _loadProfileList({ fetchImpl = globalThis.fetch } = {}) {
  const listed = await _request('GET', '/profiles', { fetchImpl });
  if (!listed.ok) throw new Error(_envelopeMessage(listed.body) || `Profile list failed (HTTP ${listed.status})`);
  _profiles = profileSummaries(listed.body);
  _renderProfileList();
  _announceProfileCoverage();
}

/** Tell the Inventory island how many profiles each artifact has.
 *
 * An artifact with no profile is downloaded weight that nothing can launch,
 * and the inventory is where that is noticed or forgotten. The count travels
 * as an event because the two islands stay independent; inventory renders
 * nothing until it hears this, so an unconfigured provider never paints
 * "No profile" over artifacts it knows nothing about.
 */
function _announceProfileCoverage() {
  const counts = {};
  for (const profile of _profiles) {
    const id = _refLabel(profile.artifact_ref);
    if (id) counts[id] = (counts[id] || 0) + 1;
  }
  document.dispatchEvent(new CustomEvent('cookbook:profile-coverage', { detail: { counts } }));
}

function _envelopeMessage(body) {
  const errors = Array.isArray(body?.errors) ? body.errors : [];
  return errors.map(entry => entry?.message).filter(Boolean).join(' ');
}

// ── editing ──

async function _startDraft({ fetchImpl = globalThis.fetch } = {}) {
  if (!_artifactContext || !_form) return;
  // Seeding replaces whatever is open, so it asks first for the same reason
  // switching profiles does.
  if (isDirty(_state) && !(await _confirmDiscard())) return;
  const ref = artifactRefFor(_artifactContext.provider, _artifactContext.artifact);
  if (!ref) {
    _setStatus('That artifact does not carry the identity the service needs.', 'error');
    return;
  }
  _busy = true;
  _renderActions();
  try {
    const draft = await _request('POST', '/draft', { body: { artifact_ref: ref }, fetchImpl });
    if (!draft.ok) {
      _setStatus(_envelopeMessage(draft.body) || `Draft failed (HTTP ${draft.status})`, 'error');
      return;
    }
    _state = beginDraft(_state, _form, draft.body, ref);
    _boundArtifact = _artifactLabel(_artifactContext.artifact);
    _setStatus('Draft seeded from the selected artifact. Nothing is saved yet.');
    _renderAll();
  } catch (error) {
    _setStatus(_transportMessage(error), 'error');
  } finally {
    _busy = false;
    _renderActions();
  }
}

async function _confirmDiscard() {
  const message = 'Discard your unsaved changes to this profile?';
  const styled = window.styledConfirm;
  if (styled) return await styled(message, { confirmText: 'Discard', cancelText: 'Keep editing' });
  return window.confirm ? window.confirm(message) : true;
}

async function _openProfile(profileId, { fetchImpl = globalThis.fetch } = {}) {
  if (!_form) return;
  // Switching profiles is the one place a draft can be lost, so it is the one
  // place that asks first.
  if (isDirty(_state) && !(await _confirmDiscard())) return;
  _busy = true;
  _renderActions();
  try {
    const read = await _request('GET', `/profiles/${encodeURIComponent(profileId)}`, { fetchImpl });
    if (!read.ok) {
      _setStatus(_envelopeMessage(read.body) || `Could not read ${profileId} (HTTP ${read.status})`, 'error');
      return;
    }
    const profile = profileFromEnvelope(read.body);
    if (!profile) {
      _setStatus('The profile service returned an unusable profile.', 'error');
      return;
    }
    _state = beginEdit(_state, _form, profile, read.etag);
    _boundArtifact = _refLabel(profile.artifact_ref);
    _setStatus(`Editing ${profile.id}.`);
    _renderAll();
  } catch (error) {
    _setStatus(_transportMessage(error), 'error');
  } finally {
    _busy = false;
    _renderActions();
  }
}

function _schedulePreview() {
  if (_previewTimer) window.clearTimeout(_previewTimer);
  _previewTimer = window.setTimeout(() => { _runPreview(); }, PREVIEW_DEBOUNCE_MS);
}

async function _runPreview({ fetchImpl = globalThis.fetch } = {}) {
  if (_state.mode === 'idle' || !_form) return;
  _state = nextPreviewToken(_state);
  const token = _state.previewToken;
  try {
    const preview = await _request('POST', '/preview', {
      body: { values: submissionValues(_form, _state.values, _state.baseline) },
      fetchImpl,
    });
    // A rejected preview is still a 200 carrying errors; a transport or
    // configuration fault is not preview feedback and must not be rendered
    // as though the user's values caused it.
    if (preview.status !== 200) return;
    _state = applyPreview(_state, token, preview.body);
    _renderFeedback();
  } catch {
    // Validation is a convenience; an unreachable service surfaces on save.
  }
}

async function _save({ fetchImpl = globalThis.fetch } = {}) {
  if (!canSubmit(_state) || !_form || _busy) return;
  _busy = true;
  _renderActions();
  const values = submissionValues(_form, _state.values, _state.baseline);
  try {
    const response = _state.mode === 'new'
      ? await _request('POST', '/profiles', {
        body: { artifact_ref: _state.artifactRef, values },
        fetchImpl,
      })
      : await _request('PUT', `/profiles/${encodeURIComponent(_state.profileId)}`, {
        body: { values },
        ifMatch: _state.etag,
        fetchImpl,
      });

    if (response.status === 412) {
      await _enterConflict({ fetchImpl });
      return;
    }
    if (!response.ok) {
      _state = applyWriteFailure(_state, response.body);
      _setStatus(_envelopeMessage(response.body) || `Save failed (HTTP ${response.status})`, 'error');
      _renderFeedback();
      return;
    }
    const profile = profileFromEnvelope(response.body);
    _state = applySaved(_state, _form, profile, response.etag);
    await _loadProfileList({ fetchImpl });
    _setStatus(`Saved ${_state.profileId}.`);
    _renderAll();
  } catch (error) {
    // The draft is untouched: a provider outage must not cost the user their
    // edits.
    _setStatus(`${_transportMessage(error)} Your draft is still here.`, 'error');
  } finally {
    _busy = false;
    _renderActions();
  }
}

/**
 * A refused precondition, for either write.
 *
 * Resolving the conflict is what adopts the version just read, so the retry --
 * whether a save or a delete -- then acts on a version the user has seen.
 */
async function _enterConflict({ fetchImpl = globalThis.fetch, operation = 'Save' } = {}) {
  _conflictOperation = operation;
  let remote = null;
  try {
    const read = await _request('GET', `/profiles/${encodeURIComponent(_state.profileId)}`, { fetchImpl });
    if (read.ok) {
      const profile = profileFromEnvelope(read.body);
      if (profile) remote = { ...profile, etag: read.etag || profile.etag };
    }
  } catch {
    // Fall through: the conflict is still reportable without their version.
  }
  const kept = operation === 'Delete'
    ? 'Nothing was removed.'
    : 'Your edits are kept below.';
  _state = applyConflict(
    _state,
    remote
      ? `This profile changed since you loaded it. ${kept}`
      : `This profile changed since you loaded it, and the current version could not be read. ${kept}`,
    remote,
  );
  _setStatus(`${operation} refused: the profile changed since you loaded it.`, 'error');
  _renderFeedback();
}

async function _delete({ fetchImpl = globalThis.fetch } = {}) {
  if (!canDelete(_state) || _busy) return;
  const profileId = _state.profileId;
  const message = `Delete the profile "${profileId}"? The service removes it; this cannot be undone from here.`;
  const styled = window.styledConfirm;
  const confirmed = styled
    ? await styled(message, { confirmText: 'Delete', cancelText: 'Cancel' })
    : (window.confirm ? window.confirm(message) : false);
  if (!confirmed) return;
  _busy = true;
  _renderActions();
  try {
    const response = await _request('DELETE', `/profiles/${encodeURIComponent(profileId)}`, {
      ifMatch: _state.etag,
      fetchImpl,
    });
    if (response.status === 412) {
      await _enterConflict({ fetchImpl, operation: 'Delete' });
      return;
    }
    if (response.status !== 204) {
      _setStatus(_envelopeMessage(response.body) || `Delete failed (HTTP ${response.status})`, 'error');
      return;
    }
    _state = clearEditor(_state);
    _boundArtifact = null;
    await _loadProfileList({ fetchImpl });
    _setStatus(`Deleted ${profileId}.`);
    _renderAll();
  } catch (error) {
    _setStatus(_transportMessage(error), 'error');
  } finally {
    _busy = false;
    _renderActions();
  }
}

function _revert() {
  if (!_form) return;
  _state = { ..._state, values: { ..._state.baseline }, fieldErrors: {}, formErrors: [] };
  _renderForm();
  _setStatus('Reverted to the last loaded version.');
}

// ── wiring ──

function _fieldAt(index) {
  const position = Number(index);
  return Number.isInteger(position) ? _fieldsByIndex[position] ?? null : null;
}

function _chipValues(index) {
  const boxes = document.querySelectorAll(
    `.cookbook-profile-chips[data-field-index="${index}"] input[type="checkbox"]`,
  );
  return [...boxes].filter(box => box.checked).map(box => box.dataset.chipValue);
}

function _onFieldInput(event) {
  const target = event.target;
  const index = target?.dataset?.fieldIndex;
  if (index === undefined) return;
  const field = _fieldAt(index);
  if (!field) return;
  const raw = target.dataset.chipValue !== undefined
    ? _chipValues(index)
    : (target.type === 'checkbox' ? target.checked : target.value);
  _state = setFieldValue(_state, field, raw);
  // Invalidate any preview already in flight: its reply describes values the
  // user has since typed past.
  _state = nextPreviewToken(_state);
  _renderFeedback();
  _schedulePreview();
}

export function profilesPanelHtml({ available = false, provider = null } = {}) {
  const providerText = provider ? `Provider: ${esc(provider)}` : 'No external ProfileService configured';
  return `
    <div class="cookbook-group hidden" data-backend-group="Profiles">
      <div class="admin-card cookbook-profile-card">
        <div class="cookbook-profile-heading">
          <div>
            <h2>Profiles</h2>
            <p class="memory-desc doclib-desc">Runtime profiles authored against an external service. The service owns the schema and validation.</p>
          </div>
          <button type="button" class="hwfit-gpu-btn" id="cookbook-profile-refresh" title="Reload form and profiles" aria-label="Reload form and profiles"${available ? '' : ' disabled'}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10"/><path d="M3.51 15a9 9 0 0 0 14.85 3.36L23 14"/></svg>
          </button>
        </div>
        <div class="cookbook-profile-toolbar">
          <span id="cookbook-profile-context" class="cookbook-profile-context">Select an artifact in the Inventory tab to start a profile.</span>
          <button type="button" class="hwfit-gpu-btn" id="cookbook-profile-new" disabled>New profile</button>
          <span id="cookbook-profile-provider">${providerText}</span>
        </div>
        <div id="cookbook-profile-status" class="cookbook-profile-status">${available ? 'Open this tab to load the profile service.' : 'Configure an external ProfileService provider to author profiles.'}</div>
        <div class="cookbook-profile-body">
          <div id="cookbook-profile-list" class="cookbook-profile-list"></div>
          <div id="cookbook-profile-editor" class="cookbook-profile-editor" hidden>
            <div class="cookbook-profile-editor-heading">
              <h3 id="cookbook-profile-editing"></h3>
              <div class="cookbook-profile-actions">
                <button type="button" class="hwfit-gpu-btn cookbook-profile-danger" id="cookbook-profile-delete" hidden disabled>Delete</button>
                <button type="button" class="hwfit-gpu-btn" id="cookbook-profile-revert" disabled>Revert</button>
                <button type="button" class="hwfit-gpu-btn" id="cookbook-profile-save" disabled>Save changes</button>
              </div>
            </div>
            <div id="cookbook-profile-banner" class="cookbook-profile-banner"></div>
            <div id="cookbook-profile-form" class="cookbook-profile-form"></div>
          </div>
        </div>
      </div>
    </div>`;
}

export function initProfiles({ available = false, provider = null } = {}) {
  if (_provider !== (provider || null)) {
    _form = null;
    _service = null;
    _profiles = [];
    _state = createEditorState();
    _boundArtifact = null;
    _loaded = false;
  }
  _available = available === true;
  _provider = provider || null;

  _el('cookbook-profile-refresh')?.addEventListener('click', () => loadProfiles({ force: true }));
  _el('cookbook-profile-new')?.addEventListener('click', () => { _startDraft(); });
  _el('cookbook-profile-save')?.addEventListener('click', () => { _save(); });
  _el('cookbook-profile-revert')?.addEventListener('click', _revert);
  _el('cookbook-profile-delete')?.addEventListener('click', () => { _delete(); });

  const list = _el('cookbook-profile-list');
  list?.addEventListener('click', event => {
    const entry = event.target?.closest?.('[data-profile-id]');
    if (entry) _openProfile(entry.dataset.profileId);
  });

  const form = _el('cookbook-profile-form');
  form?.addEventListener('input', _onFieldInput);
  form?.addEventListener('change', _onFieldInput);

  const banner = _el('cookbook-profile-banner');
  banner?.addEventListener('click', event => {
    if (event.target?.id === 'cookbook-profile-take-remote') {
      _state = resolveConflictWithRemote(_state, _form);
      _setStatus('Loaded the provider’s current version.');
      _renderAll();
    }
    if (event.target?.id === 'cookbook-profile-take-local') {
      _state = resolveConflictWithLocal(_state);
      _setStatus('Kept your edits. Save again to overwrite the provider’s version.');
      _renderAll();
    }
  });

  // The Inventory island announces a selection; this panel only records it as
  // context. Seeding a draft stays an explicit gesture so browsing the
  // inventory can never discard an in-progress draft.
  if (!document._cookbookProfileSelectionBound) {
    document._cookbookProfileSelectionBound = true;
    document.addEventListener('cookbook:artifact-selected', event => {
      const detail = event?.detail || {};
      if (!detail.artifact) return;
      _artifactContext = { provider: detail.provider || null, artifact: detail.artifact };
      _renderContext();
    });
  }

  _renderAll();
}

export function activateProfiles() {
  return loadProfiles();
}
