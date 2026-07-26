// Provider-backed, read-only model inventory for the Cookbook.

import {
  artifactDisplayLabels,
  artifactPathVariants,
  artifactPathVariantValue,
  artifactSplitLabel,
  filterInventoryArtifacts,
  formatArtifactBytes,
  inventorySourceIssues,
  parseInventoryDocument,
  sortInventoryArtifacts,
} from './generated/cookbookInventoryModel.js';

export {
  artifactDisplayLabels,
  artifactPathVariantValue,
  artifactSplitLabel,
  filterInventoryArtifacts,
  formatArtifactBytes,
  inventorySourceIssues,
  parseInventoryDocument,
  sortInventoryArtifacts,
};

let _document = null;
let _loading = false;
let _available = false;
let _provider = null;

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

export function artifactPathVariantsHtml(artifact) {
  const rows = artifactPathVariants(artifact).map((variant, index) => `
      <div class="cookbook-inventory-path-variant">
        <span class="cookbook-inventory-path-label">${esc(variant.label)}</span>
        <code>${esc(variant.value)}</code>
        <button type="button" class="hwfit-gpu-btn cookbook-inventory-path-copy" data-path-variant-index="${index}" title="Copy ${esc(variant.label)} path" aria-label="Copy ${esc(variant.label)} path">Copy</button>
      </div>`).join('');
  return rows
    ? `<div class="cookbook-inventory-paths"><h3>Concrete paths</h3>${rows}</div>`
    : '';
}

function _dateLabel(value) {
  const date = new Date(value || '');
  if (!Number.isFinite(date.getTime())) return 'Unknown';
  return date.toLocaleString();
}

function _artifactHtml(artifact, sortMode) {
  const observed = artifact?.observed || {};
  const labels = artifactDisplayLabels(artifact, sortMode);
  const quant = observed.quantization || 'Unknown quant';
  const state = observed.state || 'unknown';
  const splitLabel = artifactSplitLabel(artifact);
  const fileRows = (Array.isArray(artifact?.files) ? artifact.files : []).map(file => (
    `<div class="cookbook-inventory-file"><span>${esc(file.filename)}</span><span>${esc(formatArtifactBytes(file.size_bytes))}</span></div>`
  )).join('');
  const pathVariants = artifactPathVariantsHtml(artifact);
  return `
    <article class="cookbook-inventory-item" data-artifact-id="${esc(artifact.id)}" tabindex="0" role="button" aria-expanded="false">
      <div class="cookbook-inventory-summary">
        <div class="cookbook-inventory-name-wrap">
          <div class="cookbook-inventory-name">${esc(labels.primary)}</div>
          ${labels.secondary ? `<div class="cookbook-inventory-location">${esc(labels.secondary)}</div>` : ''}
        </div>
        <div class="cookbook-inventory-meta">
          <span class="cookbook-inventory-chip">GGUF</span>
          <span class="cookbook-inventory-chip">${esc(quant)}</span>
          ${splitLabel ? `<span class="cookbook-inventory-chip">${esc(splitLabel)}</span>` : ''}
          <span class="cookbook-inventory-chip cookbook-inventory-state-${esc(state)}">${esc(state)}</span>
          <span class="cookbook-inventory-size">${esc(formatArtifactBytes(observed.size_bytes))}</span>
        </div>
      </div>
      <div class="cookbook-inventory-detail" hidden>
        <dl>
          <div><dt>Artifact ID</dt><dd>${esc(artifact.id || '')}</dd></div>
          <div><dt>Source ID</dt><dd>${esc(artifact.source_id || '')}</dd></div>
          <div><dt>Logical path</dt><dd>${esc(artifact.logical_path || '')}</dd></div>
          <div><dt>Observation</dt><dd>${esc(artifact.observation || '')}</dd></div>
          <div><dt>Modified</dt><dd>${esc(_dateLabel(observed.modified_at))}</dd></div>
          <div><dt>Observed format</dt><dd>GGUF</dd></div>
          <div><dt>Quantisation</dt><dd>${esc(quant)}</dd></div>
        </dl>
        ${pathVariants}
        ${fileRows ? `<div class="cookbook-inventory-files">${fileRows}</div>` : ''}
      </div>
    </article>`;
}

async function _copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}

export function inventoryPanelHtml({ available = false, provider = null } = {}) {
  const providerText = provider ? `Provider: ${esc(provider)}` : 'No external ArtifactStore configured';
  return `
    <div class="cookbook-group hidden" data-backend-group="Inventory">
      <div class="admin-card cookbook-inventory-card">
        <div class="cookbook-inventory-heading">
          <div>
            <h2>Inventory <span id="cookbook-inventory-count" class="memory-count"></span></h2>
            <p class="memory-desc doclib-desc">Read-only GGUF artifacts reported by an external provider.</p>
          </div>
          <button type="button" class="hwfit-gpu-btn" id="cookbook-inventory-refresh" title="Refresh inventory" aria-label="Refresh inventory"${available ? '' : ' disabled'}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10"/><path d="M3.51 15a9 9 0 0 0 14.85 3.36L23 14"/></svg>
          </button>
        </div>
        <div class="cookbook-inventory-toolbar">
          <input type="search" class="memory-search-input" id="cookbook-inventory-search" placeholder="Search local GGUFs…" />
          <label class="cookbook-inventory-sort" for="cookbook-inventory-sort">
            <span>Sort</span>
            <select id="cookbook-inventory-sort" aria-label="Sort inventory">
              <option value="path">Path</option>
              <option value="filename">Filename</option>
            </select>
          </label>
          <span id="cookbook-inventory-provider">${providerText}</span>
        </div>
        <div id="cookbook-inventory-status" class="cookbook-inventory-status">${available ? 'Open this tab to load the inventory.' : 'Configure an external ArtifactStore provider to enumerate local GGUFs.'}</div>
        <div id="cookbook-inventory-issues" class="cookbook-inventory-issues"></div>
        <div id="cookbook-inventory-list" class="cookbook-inventory-list"></div>
      </div>
    </div>`;
}

// Providers may explain why a source is degraded. Surfacing the state without
// the reason leaves an operator with nothing actionable, so render each one.
function _renderSourceIssues(issues) {
  const host = document.getElementById('cookbook-inventory-issues');
  if (!host) return;
  host.innerHTML = (issues || []).map(issue => `
      <div class="cookbook-inventory-issue">
        <span class="cookbook-inventory-issue-source">${esc(issue.label)}</span>
        <span class="cookbook-inventory-chip cookbook-inventory-state-${esc(issue.state)}">${esc(issue.state)}</span>
        ${issue.error ? `<span class="cookbook-inventory-issue-error">${esc(issue.error)}</span>` : ''}
      </div>`).join('');
}

function _render() {
  const list = document.getElementById('cookbook-inventory-list');
  const status = document.getElementById('cookbook-inventory-status');
  const count = document.getElementById('cookbook-inventory-count');
  if (!list || !status || !count) return;
  if (!_available) {
    list.innerHTML = '';
    count.textContent = '';
    _renderSourceIssues([]);
    status.textContent = 'Configure an external ArtifactStore provider to enumerate local GGUFs.';
    status.classList.add('is-empty');
    return;
  }
  if (_loading) {
    status.textContent = 'Loading inventory…';
    status.classList.remove('is-error', 'is-empty');
    return;
  }
  if (!_document) return;
  const query = document.getElementById('cookbook-inventory-search')?.value || '';
  const sortMode = document.getElementById('cookbook-inventory-sort')?.value || 'path';
  const all = Array.isArray(_document.artifacts) ? _document.artifacts : [];
  const artifacts = sortInventoryArtifacts(filterInventoryArtifacts(all, query), sortMode);
  count.textContent = String(all.length);
  list.innerHTML = artifacts.map(artifact => _artifactHtml(artifact, sortMode)).join('');
  const providerName = _document.provider?.name || _document.provider?.id || _provider;
  const providerEl = document.getElementById('cookbook-inventory-provider');
  if (providerEl) providerEl.textContent = providerName ? `Provider: ${providerName}` : 'External provider';
  const providerState = _document.status?.state || 'ready';
  const unavailableSources = inventorySourceIssues(_document);
  _renderSourceIssues(unavailableSources);
  if (providerState !== 'ready') {
    status.textContent = unavailableSources.length
      ? `${artifacts.length} shown · ${unavailableSources.length} source${unavailableSources.length === 1 ? '' : 's'} unavailable or partial.`
      : `${artifacts.length} shown · provider status: ${providerState}.`;
    status.classList.add('is-error');
    status.classList.remove('is-empty');
  } else if (!artifacts.length) {
    status.textContent = query ? 'No GGUF artifacts match this search.' : 'The provider reported no GGUF artifacts.';
    status.classList.add('is-empty');
    status.classList.remove('is-error');
  } else {
    status.textContent = `${artifacts.length} GGUF artifact${artifacts.length === 1 ? '' : 's'} shown.`;
    status.classList.remove('is-error', 'is-empty');
  }
}

export async function loadInventory({ force = false, fetchImpl = globalThis.fetch } = {}) {
  if (!_available || _loading || (_document && !force)) {
    _render();
    return _document;
  }
  _loading = true;
  _render();
  const refresh = document.getElementById('cookbook-inventory-refresh');
  refresh?.classList.add('spinning');
  if (refresh) refresh.disabled = true;
  try {
    const response = await fetchImpl('/api/cookbook/artifacts', { credentials: 'same-origin' });
    let payload = null;
    try { payload = await response.json(); } catch {}
    if (!response.ok) throw new Error(payload?.detail || `Inventory request failed (HTTP ${response.status})`);
    const inventoryDocument = parseInventoryDocument(payload);
    if (!inventoryDocument) {
      throw new Error('Inventory provider returned an unsupported response');
    }
    _document = inventoryDocument;
    return _document;
  } catch (error) {
    _document = null;
    const status = document.getElementById('cookbook-inventory-status');
    const list = document.getElementById('cookbook-inventory-list');
    if (list) list.innerHTML = '';
    _renderSourceIssues([]);
    if (status) {
      status.textContent = error?.message || 'Inventory provider is unavailable.';
      status.classList.add('is-error');
      status.classList.remove('is-empty');
    }
    return null;
  } finally {
    _loading = false;
    refresh?.classList.remove('spinning');
    if (refresh) refresh.disabled = !_available;
    if (_document) _render();
  }
}

export function initInventory({ available = false, provider = null } = {}) {
  if (_provider !== (provider || null)) _document = null;
  _available = available === true;
  _provider = provider || null;
  const search = document.getElementById('cookbook-inventory-search');
  const sort = document.getElementById('cookbook-inventory-sort');
  const refresh = document.getElementById('cookbook-inventory-refresh');
  const list = document.getElementById('cookbook-inventory-list');
  search?.addEventListener('input', _render);
  sort?.addEventListener('change', _render);
  refresh?.addEventListener('click', () => loadInventory({ force: true }));
  const toggle = target => {
    const item = target?.closest?.('.cookbook-inventory-item');
    if (!item) return;
    const detail = item.querySelector('.cookbook-inventory-detail');
    const expanded = item.getAttribute('aria-expanded') === 'true';
    item.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    item.classList.toggle('expanded', !expanded);
    if (detail) detail.hidden = expanded;
    if (!expanded) {
      const artifact = (_document?.artifacts || []).find(candidate => candidate.id === item.dataset.artifactId);
      if (artifact) {
        document.dispatchEvent(new CustomEvent('cookbook:artifact-selected', {
          detail: { provider: _document?.provider || null, artifact },
        }));
      }
    }
  };
  list?.addEventListener('click', event => {
    const copyButton = event.target?.closest?.('[data-path-variant-index]');
    if (copyButton) {
      event.preventDefault();
      event.stopPropagation();
      const item = copyButton.closest('.cookbook-inventory-item');
      const artifact = (_document?.artifacts || []).find(candidate => candidate.id === item?.dataset.artifactId);
      const value = artifactPathVariantValue(artifact, copyButton.dataset.pathVariantIndex);
      if (value !== null) {
        _copyText(value).then(() => {
          copyButton.textContent = 'Copied';
          window.setTimeout(() => { copyButton.textContent = 'Copy'; }, 1200);
        }).catch(() => {});
      }
      return;
    }
    toggle(event.target);
  });
  list?.addEventListener('keydown', event => {
    if (event.target?.closest?.('[data-path-variant-index]')) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggle(event.target);
  });
  _render();
}

export function activateInventory() {
  return loadInventory();
}
