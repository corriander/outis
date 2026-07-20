// Cookbook deployment capabilities are backend policy, not frontend guesses.
// The safe fallback keeps public catalogue browsing available while disabling
// every host-mutating native action.

const FALLBACK = Object.freeze({
  schema_version: 1,
  mode: 'external',
  capabilities: {
    catalogue: { provider: 'huggingface', browse: true, inspect: true },
    artifact_store: { provider: null, list: false, acquire: false, delete: false },
    profile_service: { provider: null, read: false, write: false },
    runtime_controller: { provider: null, status: false, start: false, stop: false, logs: false },
  },
});

let current = FALLBACK;

export function cookbookUiPolicy(document) {
  const capabilities = document?.capabilities || FALLBACK.capabilities;
  const artifactStore = capabilities.artifact_store || {};
  const profiles = capabilities.profile_service || {};
  const runtime = capabilities.runtime_controller || {};
  return {
    browse: capabilities.catalogue?.browse !== false,
    download: artifactStore.acquire === true,
    profiles: profiles.write === true,
    launch: runtime.start === true,
    nativeSettings: document?.mode === 'native',
  };
}

export function cookbookCapabilityDocument() {
  return current;
}

export function currentCookbookUiPolicy() {
  return cookbookUiPolicy(current);
}

export async function loadCookbookCapabilities(fetchImpl = globalThis.fetch) {
  if (typeof fetchImpl !== 'function') {
    current = FALLBACK;
    return current;
  }
  try {
    const response = await fetchImpl('/api/hwfit/capabilities', { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const document = await response.json();
    if (!document || document.schema_version !== 1 || !document.capabilities) {
      throw new Error('invalid capability document');
    }
    current = document;
  } catch (error) {
    console.warn('[cookbook] capability discovery failed closed', error);
    current = FALLBACK;
  }
  return current;
}
