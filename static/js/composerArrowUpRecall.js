/**
 * ArrowUp on the composer recalls previous user messages from this chat.
 *
 * Which keys drive the recall is a preference (Settings > Shortcuts), because
 * ArrowUp is also caret movement: readline habits reach for Ctrl+P/Ctrl+N and
 * want the arrows left alone.
 */

/** Recall key modes, in the order the settings picker offers them. */
export const RECALL_KEY_MODES = ['arrows', 'ctrl', 'both'];
export const DEFAULT_RECALL_KEY_MODE = 'arrows';

/** @param {unknown} value @returns {'arrows'|'ctrl'|'both'} */
export function normalizeRecallKeyMode(value) {
  return RECALL_KEY_MODES.includes(value) ? value : DEFAULT_RECALL_KEY_MODE;
}

/**
 * Which way through history this keystroke walks, if any.
 *
 * Ctrl+P and Ctrl+N are the readline pair. Ctrl+N is reserved by most browsers
 * (new window) and never reaches the page there, so it only works in the
 * desktop app; Ctrl+P arrives normally once preventDefault stops the print
 * dialog. That is why 'arrows' stays the default and 'both' exists.
 *
 * @param {KeyboardEvent} e
 * @param {string} [mode]
 * @returns {'older'|'newer'|null}
 */
export function recallDirection(e, mode) {
  const resolved = normalizeRecallKeyMode(mode);
  if (resolved !== 'arrows' && e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey) {
    const key = String(e.key || '').toLowerCase();
    if (key === 'p') return 'older';
    if (key === 'n') return 'newer';
  }
  if (resolved === 'ctrl') return null;
  if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return null;
  if (e.key === 'ArrowUp') return 'older';
  if (e.key === 'ArrowDown') return 'newer';
  return null;
}

/**
 * User bubbles in the active chat surface (#chat-history), newest first, using
 * dataset.raw (same source as resend/regenerate in chat.js).
 *
 * @param {Document | Element} [root=document]
 * @returns {string[]}
 */
export function getUserMessagesFromChatHistory(root = document) {
  const chatBox =
    root && root.id === 'chat-history' && typeof root.querySelectorAll === 'function'
      ? root
      : (root.getElementById ? root.getElementById('chat-history') : null);
  if (!chatBox) return [];

  const users = chatBox.querySelectorAll('.msg-user');
  const prompts = [];
  for (let i = users.length - 1; i >= 0; i--) {
    const msg = users[i];
    const bodyEl = msg.querySelector('.body');
    const text = msg.dataset?.raw || (bodyEl ? bodyEl.textContent : '') || '';
    if (text) prompts.push(text);
  }
  return prompts;
}

/**
 * Last user bubble in the active chat surface (#chat-history).
 *
 * @param {Document | Element} [root=document]
 * @returns {string}
 */
export function getLastUserMessageFromChatHistory(root = document) {
  return getUserMessagesFromChatHistory(root)[0] || '';
}

/**
 * @param {HTMLTextAreaElement} composer
 * @param {() => string|string[]} getUserMessages
 * @param {{ autoResize?: (el: HTMLTextAreaElement) => void,
 *           keys?: (() => string) | string }} [options]
 * @returns {boolean} true when wired (or already wired)
 */
export function wireArrowUpRecall(composer, getUserMessages, options = {}) {
  if (!composer) return false;
  if (composer._arrowUpRecallWired) return true;
  composer._arrowUpRecallWired = true;

  const { autoResize, keys } = options;
  const readKeyMode = () =>
    normalizeRecallKeyMode(typeof keys === 'function' ? keys() : keys);
  let recallIndex = -1;
  let applyingRecall = false;
  let lastRecalledValue = '';
  let recallHistory = [];
  // A draft the user was mid-way through when they asked for history
  // explicitly (Ctrl+P). Walking back past the newest prompt restores it
  // rather than clearing the composer.
  let stashedDraft = '';

  const readHistory = () => {
    const value = getUserMessages?.();
    if (Array.isArray(value)) return value.filter(Boolean);
    return value ? [value] : [];
  };
  const norm = (value) => String(value || '').replace(/\r\n/g, '\n').trimEnd();
  const debug = (...args) => {
    try {
      if (localStorage.getItem('odysseusArrowRecallDebug') === '1') {
        console.debug('[arrow-recall]', ...args);
      }
    } catch (_) {}
  };

  composer.addEventListener('input', () => {
    if (applyingRecall) return;
    if (norm(composer.value) === norm(lastRecalledValue)) return;
    recallIndex = -1;
    lastRecalledValue = '';
    recallHistory = [];
    stashedDraft = '';
    try { delete composer.dataset.odysseusRecallIndex; } catch (_) {}
  });

  composer.addEventListener('keydown', (e) => {
    // Prompt history: one direction walks older, the other newer/back to the
    // draft. Which keys those are is the user's preference — see recallDirection.
    const direction = recallDirection(e, readKeyMode());
    if (!direction) return;
    // An explicit Ctrl+P/Ctrl+N is unambiguous, unlike ArrowUp, which is also
    // how you move the caret up a line.
    const explicit = !!e.ctrlKey;
    if (e.isComposing) return;
    if (typeof window !== 'undefined' && window._ghostAutocomplete?.isActive?.()) return;

    const freshHistory = readHistory();
    const history = freshHistory.length ? freshHistory : recallHistory;
    if (!history.length) {
      debug('skip:no-history', { value: composer.value });
      return;
    }

    const rawCurrentValue = String(composer.value || '');
    const currentValue = norm(rawCurrentValue);
    const recalledValue = norm(lastRecalledValue);
    let currentIndex = rawCurrentValue === ''
      ? -1
      : history.findIndex((item) => norm(item) === currentValue);
    if (currentIndex < 0 && currentValue && currentValue === recalledValue) {
      currentIndex = recallIndex;
    }
    if (currentIndex < 0 && currentValue) {
      const markedIndex = Number(composer.dataset?.odysseusRecallIndex);
      if (Number.isInteger(markedIndex) && markedIndex >= 0 && markedIndex < history.length) {
        currentIndex = markedIndex;
      }
    }
    if (rawCurrentValue !== '' && currentIndex < 0) {
      if (!explicit) {
        debug('skip:draft-in-progress', { value: composer.value });
        return;
      }
      // Asked for by name: recall over the draft, but keep it so walking back
      // past the newest prompt hands it straight back.
      stashedDraft = rawCurrentValue;
      debug('stash-draft', { value: rawCurrentValue });
    }
    e.preventDefault();
    e.stopPropagation?.();
    e.stopImmediatePropagation?.();
    if (direction === 'newer') {
      if (currentIndex < 0) return;
      const nextIndex = currentIndex - 1;
      if (nextIndex < 0) {
        const restored = stashedDraft;
        stashedDraft = '';
        recallIndex = -1;
        recallHistory = history;
        applyingRecall = true;
        lastRecalledValue = restored;
        try { delete composer.dataset.odysseusRecallIndex; } catch (_) {}
        composer.value = restored;
        try {
          composer.selectionStart = composer.selectionEnd = restored.length;
        } catch (_) {}
        if (autoResize) autoResize(composer);
        debug('handled-down-clear', { restored, historyLength: history.length });
        setTimeout(() => { applyingRecall = false; }, 0);
        return;
      }
      const recalled = history[nextIndex];
      recallIndex = nextIndex;
      recallHistory = history;
      applyingRecall = true;
      lastRecalledValue = recalled;
      try { composer.dataset.odysseusRecallIndex = String(nextIndex); } catch (_) {}
      composer.value = recalled;
      try { composer.selectionStart = composer.selectionEnd = recalled.length; } catch (_) {}
      if (autoResize) autoResize(composer);
      debug('handled-down', { nextIndex, recalled, historyLength: history.length });
      setTimeout(() => { applyingRecall = false; }, 0);
      return;
    }

    // Walking older. An unmatched draft either returned above (arrows) or was
    // stashed (Ctrl+P), so the caret-navigation case is never hijacked.
    const nextIndex = currentIndex >= 0 ? Math.min(currentIndex + 1, history.length - 1) : 0;
    const recalled = history[nextIndex];
    if (!recalled) {
      debug('skip:no-recalled', { nextIndex, history });
      return;
    }

    recallIndex = nextIndex;
    recallHistory = history;
    applyingRecall = true;
    lastRecalledValue = recalled;
    try { composer.dataset.odysseusRecallIndex = String(nextIndex); } catch (_) {}
    composer.value = recalled;
    try {
      composer.selectionStart = composer.selectionEnd = recalled.length;
    } catch (_) {}
    if (autoResize) autoResize(composer);
    debug('handled', { nextIndex, recalled, historyLength: history.length });
    setTimeout(() => { applyingRecall = false; }, 0);
  }, true);

  return true;
}
