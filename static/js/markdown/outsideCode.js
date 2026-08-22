// static/js/markdown/outsideCode.js
// Apply a text transform to everything except code.

/**
 * Fenced blocks and inline code spans, in source order. An unterminated fence
 * runs to the end of the string: mid-stream, the tail of a code block that has
 * not closed yet is still code, and treating it as prose is what lets a
 * half-arrived snippet get rewritten under the user.
 */
const CODE_SPAN_RE = /```[\s\S]*?(?:```|$)|`[^`\n]+`/g;

/**
 * Run `transform` over the parts of `text` that are not fenced blocks or inline
 * code spans, and stitch the code back in untouched.
 *
 * For display-time scrubbing that matches word-shaped patterns — a leaked role
 * marker, a line of tool narration — the same match is noise in prose and
 * content in a code block. `stdout:` is a good example: narration when the
 * model says it, a log line when the user asked to see one.
 *
 * @param {string} text
 * @param {(chunk: string) => string} transform
 * @returns {string}
 */
export function mapOutsideCode(text, transform) {
  const src = String(text ?? '');
  let out = '';
  let last = 0;
  let match;
  CODE_SPAN_RE.lastIndex = 0;
  while ((match = CODE_SPAN_RE.exec(src)) !== null) {
    out += transform(src.slice(last, match.index)) + match[0];
    last = match.index + match[0].length;
  }
  return out + transform(src.slice(last));
}
