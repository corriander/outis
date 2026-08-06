/*
 * Typed island for the external ProfileService v1 authoring contract.
 *
 * Odysseus serves handwritten browser modules directly, so the generated
 * JavaScript is committed under static/js/generated. Keep browser DOM work in
 * cookbookProfiles.js; this module owns untrusted-wire normalization, the form
 * vocabulary, and the pure editor state machine.
 *
 * The provider owns the profile schema. Nothing here names a profile field:
 * the vocabulary below (`kind`, `widget`, `group`, `constraints`) is the *form*
 * contract, which is provider-neutral, and every field is discovered from the
 * document the service serves.
 */

/** A declared section of the form. Providers order sections themselves. */
export interface FormGroupSpec {
  id: string;
  label?: string;
  order?: number;
}

export interface FormFieldConstraints {
  min?: number;
  max?: number;
  pattern?: string;
}

/** Element spec for a `list` field, when the provider constrains its items. */
export interface FormFieldItemSpec {
  kind?: string;
  allowed?: unknown[];
}

export interface FormField {
  id: string;
  label?: string;
  kind: string;
  widget?: string;
  group?: string;
  order?: number;
  help?: string;
  constraints?: FormFieldConstraints;
  default?: unknown;
  nullable?: boolean;
  allowed?: unknown[];
  item?: FormFieldItemSpec;
  [key: string]: unknown;
}

export interface FormDocument {
  form_version?: number;
  groups?: FormGroupSpec[];
  fields: FormField[];
  widget_fallbacks?: Record<string, string>;
  [key: string]: unknown;
}

export interface RenderGroup {
  id: string;
  label: string;
  fields: FormField[];
}

export interface ProfileValues {
  [key: string]: unknown;
}

/**
 * The artifact a profile's recorded launch path already names.
 *
 * Derived by the service, never authored. It is what makes an unbound
 * profile bindable without the operator recognising the right file out of an
 * inventory from memory -- a wrong pick silently re-points the profile, so a
 * suggestion the service can prove is the only safe offer.
 */
export interface ArtifactMatch {
  artifact_ref: Record<string, unknown>;
  filename: string;
  logical_path: string;
  matched_on: string;
}

export interface ProfileSummary {
  id: string;
  label: string;
  etag: string | null;
  values: ProfileValues;
  artifact_ref: Record<string, unknown> | null;
  /** The file this profile launches. Null on a profile that records none. */
  model_path: string | null;
  /** Present only while the profile carries no binding of its own. */
  artifact_match: ArtifactMatch | null;
}

/** One provider-authored message, kept with the pointer it was filed against. */
export interface FeedbackMessage {
  pointer: string;
  code: string;
  message: string;
}

export interface ProviderFeedback {
  /** Field id -> messages, for pointers of the form `/values/<id>`. */
  fieldErrors: Record<string, string[]>;
  /** Everything else: profile-level, artifact-level, or unrecognised pointers. */
  formErrors: FeedbackMessage[];
  warnings: FeedbackMessage[];
}

export type EditorMode = "idle" | "new" | "editing";

export interface ConflictState {
  message: string;
  /** The provider's current values, re-read after the rejected write. */
  remoteValues: ProfileValues | null;
  remoteEtag: string | null;
}

export interface EditorState {
  mode: EditorMode;
  /** Null until the provider names the profile in a create response. */
  profileId: string | null;
  /** The version this draft was based on; sent as `If-Match` on every write. */
  etag: string | null;
  artifactRef: Record<string, unknown> | null;
  values: ProfileValues;
  /** Last known persisted (or seeded) values, for the dirty check. */
  baseline: ProfileValues;
  fieldErrors: Record<string, string[]>;
  formErrors: FeedbackMessage[];
  warnings: FeedbackMessage[];
  conflict: ConflictState | null;
  /**
   * Monotonic token for in-flight previews. Debounced validation races: a
   * slow response for older values must never overwrite feedback for newer
   * ones, so the adapter stamps each request and discards stale replies.
   */
  previewToken: number;
}

type UnknownRecord = Record<string, unknown>;

/**
 * Widget of last resort per kind.
 *
 * A provider supplies `widget` per field and a `widget_fallbacks` map; both are
 * consulted first. This table exists only so an unfamiliar or absent widget
 * still renders as *something editable* rather than dropping the field, which
 * on a lossy replace would silently clear the value.
 */
const DEFAULT_WIDGETS: Record<string, string> = {
  string: "text",
  integer: "number",
  boolean: "toggle",
  enum: "select",
  list: "chips",
};

/** Group for fields whose `group` the provider did not declare. */
const UNGROUPED_ID = "__ungrouped__";

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrEmpty(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberOrNull(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function orderOf(value: unknown): number {
  const order = numberOrNull(isRecord(value) ? value.order : null);
  // An undeclared order sorts last but stays stable against its siblings.
  return order === null ? Number.MAX_SAFE_INTEGER : order;
}

export function parseFormDocument(value: unknown): FormDocument | null {
  if (!isRecord(value) || !Array.isArray(value.fields)) return null;
  const usable = value.fields.every(
    (field) => isRecord(field) && typeof field.id === "string" && field.id !== "",
  );
  if (!usable) return null;
  return value as unknown as FormDocument;
}

export function formFields(document: unknown): FormField[] {
  const parsed = parseFormDocument(document);
  return parsed ? parsed.fields.slice() : [];
}

export function fieldKind(field: unknown): string {
  const kind = stringOrEmpty(isRecord(field) ? field.kind : "");
  return kind || "string";
}

/**
 * Which control renders this field: the field's own widget, else the
 * provider's fallback for its kind, else ours. See DEFAULT_WIDGETS.
 */
export function widgetForField(field: unknown, document?: unknown): string {
  const declared = stringOrEmpty(isRecord(field) ? field.widget : "");
  if (declared) return declared;
  const kind = fieldKind(field);
  const fallbacks = isRecord(document) && isRecord(document.widget_fallbacks)
    ? document.widget_fallbacks
    : {};
  const providerFallback = stringOrEmpty(fallbacks[kind]);
  if (providerFallback) return providerFallback;
  return DEFAULT_WIDGETS[kind] ?? "text";
}

export function fieldLabel(field: unknown): string {
  if (!isRecord(field)) return "";
  return stringOrEmpty(field.label) || stringOrEmpty(field.id);
}

export function fieldAllowed(field: unknown): string[] {
  if (!isRecord(field) || !Array.isArray(field.allowed)) return [];
  return field.allowed.map((option) => String(option));
}

/** Allowed item values for a `list` field, when the provider constrains them. */
export function fieldItemOptions(field: unknown): string[] {
  if (!isRecord(field) || !isRecord(field.item)) return [];
  const allowed = field.item.allowed;
  return Array.isArray(allowed) ? allowed.map((option) => String(option)) : [];
}

export function fieldConstraints(field: unknown): FormFieldConstraints {
  if (!isRecord(field) || !isRecord(field.constraints)) return {};
  const constraints = field.constraints;
  const result: FormFieldConstraints = {};
  const min = numberOrNull(constraints.min);
  const max = numberOrNull(constraints.max);
  const pattern = stringOrEmpty(constraints.pattern);
  if (min !== null) result.min = min;
  if (max !== null) result.max = max;
  if (pattern) result.pattern = pattern;
  return result;
}

/**
 * Groups in provider order, each holding its fields in provider order.
 *
 * A field whose group was never declared is collected into a trailing group
 * rather than dropped. Dropping it would hide a value that a replace still
 * submits — or, worse, clears.
 */
export function formLayout(document: unknown): RenderGroup[] {
  const parsed = parseFormDocument(document);
  if (!parsed) return [];
  const declared = Array.isArray(parsed.groups) ? parsed.groups : [];
  const specs = declared
    .filter((group): group is FormGroupSpec => isRecord(group) && typeof group.id === "string")
    .map((group, index) => ({ group, index }))
    .sort((left, right) => (orderOf(left.group) - orderOf(right.group)) || (left.index - right.index))
    .map((entry) => entry.group);

  const groups = new Map<string, RenderGroup>();
  for (const spec of specs) {
    groups.set(spec.id, { id: spec.id, label: stringOrEmpty(spec.label) || spec.id, fields: [] });
  }

  const ordered = parsed.fields
    .map((field, index) => ({ field, index }))
    .sort((left, right) => (orderOf(left.field) - orderOf(right.field)) || (left.index - right.index));

  for (const { field } of ordered) {
    const groupId = stringOrEmpty(field.group);
    const target = groups.get(groupId);
    if (target) {
      target.fields.push(field);
      continue;
    }
    let ungrouped = groups.get(UNGROUPED_ID);
    if (!ungrouped) {
      ungrouped = { id: UNGROUPED_ID, label: "Other", fields: [] };
      groups.set(UNGROUPED_ID, ungrouped);
    }
    ungrouped.fields.push(field);
  }

  return [...groups.values()].filter((group) => group.fields.length > 0);
}

// -- values ---------------------------------------------------------------

/**
 * Coerce one control's raw input into the wire type its field declares.
 *
 * An unparseable number is returned as the trimmed text rather than dropped:
 * the provider owns validation, and its message names the problem far better
 * than a silent discard of what the user typed.
 */
export function coerceFieldValue(field: unknown, raw: unknown): unknown {
  const kind = fieldKind(field);
  const nullable = !isRecord(field) || field.nullable !== false;
  if (kind === "boolean") {
    if (typeof raw === "boolean") return raw;
    const text = stringOrEmpty(raw).toLowerCase();
    return text === "true" || text === "on" || text === "1";
  }
  if (kind === "list") {
    return parseChipInput(raw);
  }
  if (raw === null || raw === undefined) return nullable ? null : "";
  if (kind === "integer") {
    if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
    const text = String(raw).trim();
    if (text === "") return nullable ? null : "";
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : text;
  }
  const text = typeof raw === "string" ? raw : String(raw);
  if (text === "" && nullable) return null;
  return text;
}

/** Split free-text chip entry on commas and newlines, dropping blanks. */
export function parseChipInput(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.map((item) => String(item).trim()).filter(Boolean);
  }
  return String(raw ?? "")
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Render a wire value back into the text a control displays. */
export function formatFieldValue(field: unknown, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (fieldKind(field) === "list" || Array.isArray(value)) {
    return (Array.isArray(value) ? value : []).map((item) => String(item)).join(", ");
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** The form's declared defaults, as a complete values object. */
export function defaultValues(document: unknown): ProfileValues {
  const values: ProfileValues = {};
  for (const field of formFields(document)) {
    values[field.id] = "default" in field ? field.default ?? null : null;
  }
  return values;
}

/** Seed an editable draft: provider-supplied values over form defaults. */
export function seededValues(document: unknown, supplied: unknown): ProfileValues {
  const values = defaultValues(document);
  if (isRecord(supplied)) {
    for (const [key, value] of Object.entries(supplied)) values[key] = value;
  }
  return values;
}

/**
 * The complete values object to submit.
 *
 * A replace is lossy by design: an omitted key is cleared, not left alone. So
 * every declared field is emitted even when untouched, and any key present in
 * the baseline that this form does not declare is carried through — a provider
 * that gained a field since the form was fetched must not have it wiped by an
 * editor that never knew about it.
 */
export function submissionValues(
  document: unknown,
  values: unknown,
  baseline?: unknown,
): ProfileValues {
  const current = isRecord(values) ? values : {};
  const payload: ProfileValues = {};
  if (isRecord(baseline)) {
    for (const [key, value] of Object.entries(baseline)) payload[key] = value;
  }
  for (const field of formFields(document)) {
    payload[field.id] = field.id in current ? current[field.id] : (payload[field.id] ?? null);
  }
  for (const [key, value] of Object.entries(current)) payload[key] = value;
  return payload;
}

export function valuesEqual(left: unknown, right: unknown): boolean {
  const a = isRecord(left) ? left : {};
  const b = isRecord(right) ? right : {};
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    const leftValue = a[key] ?? null;
    const rightValue = b[key] ?? null;
    if (Array.isArray(leftValue) || Array.isArray(rightValue)) {
      const leftItems = Array.isArray(leftValue) ? leftValue : [];
      const rightItems = Array.isArray(rightValue) ? rightValue : [];
      if (leftItems.length !== rightItems.length) return false;
      if (leftItems.some((item, index) => String(item) !== String(rightItems[index]))) return false;
      continue;
    }
    if (leftValue !== rightValue) return false;
  }
  return true;
}

// -- provider feedback ----------------------------------------------------

function feedbackEntries(value: unknown): FeedbackMessage[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!isRecord(entry)) return [];
    const message = stringOrEmpty(entry.message);
    if (!message) return [];
    return [{
      pointer: stringOrEmpty(entry.pointer) || "/",
      code: stringOrEmpty(entry.code) || "error",
      message,
    }];
  });
}

/**
 * The field id a JSON pointer addresses, or null for a non-field pointer.
 *
 * The provider files field faults at `/values/<id>`, sometimes with deeper
 * segments for list elements (`/values/<id>/0`); both belong to `<id>`.
 * Everything else — `/`, `/artifact_ref`, `/artifact_ref/<key>` — is
 * profile-level and is shown with its pointer intact.
 */
export function pointerFieldId(pointer: unknown): string | null {
  const segments = stringOrEmpty(pointer).split("/");
  // A leading "/" yields an empty first segment.
  if (segments.length < 3 || segments[1] !== "values") return null;
  const id = segments[2];
  return id ? decodePointerSegment(id) : null;
}

function decodePointerSegment(segment: string): string {
  // RFC 6901 escaping: ~1 is "/", ~0 is "~", and ~1 must be resolved first.
  return segment.replaceAll("~1", "/").replaceAll("~0", "~");
}

/** Split a provider envelope's errors and warnings by what they address. */
export function providerFeedback(body: unknown): ProviderFeedback {
  const envelope = isRecord(body) ? body : {};
  const fieldErrors: Record<string, string[]> = {};
  const formErrors: FeedbackMessage[] = [];
  for (const entry of feedbackEntries(envelope.errors)) {
    const fieldId = pointerFieldId(entry.pointer);
    if (fieldId === null) {
      formErrors.push(entry);
      continue;
    }
    (fieldErrors[fieldId] ??= []).push(entry.message);
  }
  return { fieldErrors, formErrors, warnings: feedbackEntries(envelope.warnings) };
}

/**
 * Advisory local checks, derived entirely from provider-supplied constraints.
 *
 * These never gate submission: the provider owns validation, and duplicating
 * its rules here would make the two drift. They exist to catch a typo before a
 * round trip, and provider errors replace them field-for-field once a response
 * arrives.
 */
export function localFieldHints(document: unknown, values: unknown): Record<string, string[]> {
  const current = isRecord(values) ? values : {};
  const hints: Record<string, string[]> = {};
  for (const field of formFields(document)) {
    const value = current[field.id] ?? null;
    const messages: string[] = [];
    const kind = fieldKind(field);
    const constraints = fieldConstraints(field);
    const empty = value === null || value === ""
      || (Array.isArray(value) && value.length === 0);
    if (field.nullable === false && empty) {
      messages.push("Required.");
    } else if (!empty) {
      if (kind === "integer") {
        const numeric = typeof value === "number" ? value : null;
        if (numeric === null) {
          messages.push("Must be a whole number.");
        } else {
          if (constraints.min !== undefined && numeric < constraints.min) {
            messages.push(`Must be at least ${constraints.min}.`);
          }
          if (constraints.max !== undefined && numeric > constraints.max) {
            messages.push(`Must be at most ${constraints.max}.`);
          }
        }
      }
      if (constraints.pattern !== undefined && typeof value === "string") {
        if (!safeMatches(constraints.pattern, value)) {
          messages.push("Does not match the required format.");
        }
      }
      const allowed = fieldAllowed(field);
      if (allowed.length && typeof value !== "object" && !allowed.includes(String(value))) {
        messages.push("Not one of the allowed values.");
      }
    }
    if (messages.length) hints[field.id] = messages;
  }
  return hints;
}

function safeMatches(pattern: string, value: string): boolean {
  // A provider-authored pattern is untrusted input to the RegExp engine. An
  // unsupported syntax must not throw and take the whole form down with it;
  // an unusable pattern simply yields no local hint.
  try {
    return new RegExp(pattern).test(value);
  } catch {
    return true;
  }
}

// -- profiles -------------------------------------------------------------

/**
 * Existing profiles from a list envelope.
 *
 * The list carries a per-item `etag` in the body: a response header cannot
 * carry a validator for each of many items. It is a hint for display only —
 * a write always uses the etag from the read that seeded the draft.
 */
/**
 * The service's derived match, or null.
 *
 * A partial match is discarded rather than half-rendered: an offer to bind
 * has to name a specific file, and a suggestion missing its label or its ref
 * cannot. A profile that already carries a binding never gets one.
 */
export function artifactMatchOf(entry: Record<string, unknown>): ArtifactMatch | null {
  const match = entry.artifact_match;
  if (!isRecord(match)) return null;
  if (!isRecord(match.artifact_ref) || typeof match.artifact_ref.artifact_id !== "string") {
    return null;
  }
  if (typeof match.filename !== "string" || !match.filename) return null;
  return {
    artifact_ref: match.artifact_ref,
    filename: match.filename,
    logical_path: typeof match.logical_path === "string" ? match.logical_path : match.filename,
    matched_on: typeof match.matched_on === "string" ? match.matched_on : "model_path",
  };
}

function summaryOf(entry: Record<string, unknown>): ProfileSummary {
  return {
    id: entry.id as string,
    label: entry.id as string,
    etag: typeof entry.etag === "string" ? entry.etag : null,
    values: isRecord(entry.values) ? entry.values : {},
    artifact_ref: isRecord(entry.artifact_ref) ? entry.artifact_ref : null,
    model_path: typeof entry.model_path === "string" ? entry.model_path : null,
    artifact_match: artifactMatchOf(entry),
  };
}

export function profileSummaries(body: unknown): ProfileSummary[] {
  const envelope = isRecord(body) ? body : {};
  const data = isRecord(envelope.data) ? envelope.data : {};
  const profiles = Array.isArray(data.profiles) ? data.profiles : [];
  return profiles.flatMap((entry) => {
    if (!isRecord(entry) || typeof entry.id !== "string" || !entry.id) return [];
    return [summaryOf(entry)];
  }).sort((left, right) => left.id.localeCompare(right.id, undefined, { numeric: true }));
}

export function profileFromEnvelope(body: unknown): ProfileSummary | null {
  const envelope = isRecord(body) ? body : {};
  const data = isRecord(envelope.data) ? envelope.data : {};
  const profile = isRecord(data.profile) ? data.profile : null;
  if (!profile || typeof profile.id !== "string" || !profile.id) return null;
  return summaryOf(profile);
}

/** Values a preview accepted, or null when it rejected them. */
export function previewValues(body: unknown): ProfileValues | null {
  const envelope = isRecord(body) ? body : {};
  const data = envelope.data;
  if (!isRecord(data) || !isRecord(data.values)) return null;
  return data.values;
}

/**
 * Project an inventory artifact onto the reference the service accepts.
 *
 * Identity only: authority, artifact id, observation. Concrete paths a
 * provider published for display are never submitted — the service resolves
 * the launch path in its own namespace. Mirrors `normalise_artifact_ref` in
 * profile_service/client.py, which drops the same keys server-side.
 */
export function artifactRefFor(provider: unknown, artifact: unknown): Record<string, unknown> | null {
  if (!isRecord(artifact) || typeof artifact.id !== "string" || !artifact.id) return null;
  const authority = stringOrEmpty(isRecord(provider) ? provider.id : "");
  if (!authority) return null;
  const ref: Record<string, unknown> = { authority, artifact_id: artifact.id };
  const observation = stringOrEmpty(artifact.observation);
  if (observation) ref.observation = observation;
  return ref;
}

/** Whether the service's discovery document accepts this artifact authority. */
export function authorityAccepted(serviceDocument: unknown, authority: unknown): boolean {
  const document = isRecord(serviceDocument) ? serviceDocument : {};
  const accepted = Array.isArray(document.accepted_authorities)
    ? document.accepted_authorities.map((entry) => String(entry))
    : [];
  const wanted = stringOrEmpty(authority);
  // An empty list is a service that has not declared any; do not block on it.
  if (!accepted.length || !wanted) return true;
  return accepted.includes(wanted);
}

// -- editor state ---------------------------------------------------------

export function createEditorState(): EditorState {
  return {
    mode: "idle",
    profileId: null,
    etag: null,
    artifactRef: null,
    values: {},
    baseline: {},
    fieldErrors: {},
    formErrors: [],
    warnings: [],
    conflict: null,
    previewToken: 0,
  };
}

/** Start a new profile from a provider-seeded draft. */
export function beginDraft(
  state: EditorState,
  document: unknown,
  draftBody: unknown,
  artifactRef: unknown,
): EditorState {
  const envelope = isRecord(draftBody) ? draftBody : {};
  const data = isRecord(envelope.data) ? envelope.data : {};
  const values = seededValues(document, data.values);
  const seededRef = isRecord(data.artifact_ref)
    ? data.artifact_ref
    : (isRecord(artifactRef) ? artifactRef : null);
  return {
    ...state,
    mode: "new",
    profileId: null,
    // A draft is not persisted, so there is no version to precondition on.
    etag: null,
    artifactRef: seededRef,
    values,
    baseline: { ...values },
    fieldErrors: {},
    formErrors: [],
    warnings: feedbackEntries(envelope.warnings),
    conflict: null,
  };
}

/** Load an existing profile for editing, keeping its version for `If-Match`. */
export function beginEdit(
  state: EditorState,
  document: unknown,
  profile: ProfileSummary,
  etag: string | null,
): EditorState {
  const values = seededValues(document, profile.values);
  return {
    ...state,
    mode: "editing",
    profileId: profile.id,
    etag: etag ?? profile.etag,
    artifactRef: profile.artifact_ref,
    values,
    baseline: { ...values },
    fieldErrors: {},
    formErrors: [],
    warnings: [],
    conflict: null,
  };
}

/**
 * Start a new profile from an existing one's values ("save as").
 *
 * A copy is a *new* profile, so wherever the provider deliberately seeded a
 * fresh draft — a name that steps around the ones already taken, above all —
 * that seed wins over the source's value. Whatever the draft left at the
 * form's declared default is the source's to keep, and that is what makes
 * this a copy rather than a new profile of a familiar shape.
 *
 * The rule names no field: the provider decides what a fresh profile differs
 * in, and this honours the answer without knowing the question. With no draft
 * — the source binds no artifact, so there is nothing to seed from — the
 * values carry over untouched and the provider's collision refusal is what
 * names a free spelling.
 */
export function beginCopy(
  state: EditorState,
  document: unknown,
  source: Pick<ProfileSummary, "values" | "artifact_ref">,
  draftBody?: unknown,
): EditorState {
  const values = seededValues(document, source.values);
  const envelope = isRecord(draftBody) ? draftBody : {};
  const data = isRecord(envelope.data) ? envelope.data : {};
  const seeded = isRecord(data.values) ? data.values : null;
  if (seeded) {
    for (const field of formFields(document)) {
      if (!(field.id in seeded)) continue;
      const declared = "default" in field ? field.default ?? null : null;
      // Matching the declared default means the draft did not speak to this
      // field; only a deliberate seed displaces what was copied. Compared
      // through `valuesEqual` so a list-valued default compares by element.
      if (valuesEqual({ value: seeded[field.id] }, { value: declared })) continue;
      values[field.id] = seeded[field.id];
    }
  }
  return {
    ...state,
    mode: "new",
    profileId: null,
    // Nothing is persisted yet, so there is no version to precondition on.
    etag: null,
    artifactRef: source.artifact_ref,
    values,
    // Dirty from birth, unlike a seeded draft: a copy is made *in order to*
    // be saved, so browsing away from an untouched one has to ask first.
    // Undeclared provider keys still reach a submission through `values`,
    // which carries every key the source had.
    baseline: {},
    fieldErrors: {},
    formErrors: [],
    warnings: feedbackEntries(envelope.warnings),
    conflict: null,
  };
}

export function setFieldValue(state: EditorState, field: unknown, raw: unknown): EditorState {
  if (!isRecord(field) || typeof field.id !== "string") return state;
  const fieldErrors = { ...state.fieldErrors };
  // The user has answered this error; stop asserting it until the provider
  // says so again.
  delete fieldErrors[field.id];
  return {
    ...state,
    values: { ...state.values, [field.id]: coerceFieldValue(field, raw) },
    fieldErrors,
  };
}

export function nextPreviewToken(state: EditorState): EditorState {
  return { ...state, previewToken: state.previewToken + 1 };
}

/** Apply a preview response, unless a newer edit has already superseded it. */
export function applyPreview(state: EditorState, token: number, body: unknown): EditorState {
  if (token !== state.previewToken) return state;
  const feedback = providerFeedback(body);
  return {
    ...state,
    fieldErrors: feedback.fieldErrors,
    formErrors: feedback.formErrors,
    warnings: feedback.warnings,
  };
}

/** Record a rejected write's feedback without touching the user's draft. */
export function applyWriteFailure(state: EditorState, body: unknown): EditorState {
  const feedback = providerFeedback(body);
  return {
    ...state,
    fieldErrors: feedback.fieldErrors,
    formErrors: feedback.formErrors,
    warnings: feedback.warnings,
  };
}

/**
 * A stale `If-Match`. The draft is preserved verbatim and the provider's
 * current state is attached alongside so the user can choose between them.
 */
export function applyConflict(
  state: EditorState,
  message: string,
  remote: ProfileSummary | null,
): EditorState {
  return {
    ...state,
    conflict: {
      message,
      remoteValues: remote ? remote.values : null,
      remoteEtag: remote ? remote.etag : null,
    },
  };
}

/** Adopt the provider's version, discarding the local draft. */
export function resolveConflictWithRemote(
  state: EditorState,
  document: unknown,
): EditorState {
  const remote = state.conflict?.remoteValues;
  if (!remote) return state;
  const values = seededValues(document, remote);
  return {
    ...state,
    values,
    baseline: { ...values },
    etag: state.conflict?.remoteEtag ?? state.etag,
    fieldErrors: {},
    formErrors: [],
    conflict: null,
  };
}

/** Keep the local draft and retry against the provider's current version. */
export function resolveConflictWithLocal(state: EditorState): EditorState {
  return {
    ...state,
    etag: state.conflict?.remoteEtag ?? state.etag,
    conflict: null,
  };
}

/** A write succeeded: the response body and its ETag become the new baseline. */
export function applySaved(
  state: EditorState,
  document: unknown,
  profile: ProfileSummary | null,
  etag: string | null,
): EditorState {
  if (!profile) return state;
  const values = seededValues(document, profile.values);
  return {
    ...state,
    mode: "editing",
    profileId: profile.id,
    etag: etag ?? profile.etag,
    artifactRef: profile.artifact_ref ?? state.artifactRef,
    values,
    baseline: { ...values },
    fieldErrors: {},
    formErrors: [],
    conflict: null,
  };
}

/** Return to the empty editor, e.g. after the open profile was deleted. */
export function clearEditor(state: EditorState): EditorState {
  // Advance the token so a preview still in flight for the closed profile
  // cannot land on whatever is opened next.
  return { ...createEditorState(), previewToken: state.previewToken + 1 };
}

export function isDirty(state: EditorState): boolean {
  return !valuesEqual(state.values, state.baseline);
}

/** Whether a write may be attempted at all (not whether it will be accepted). */
export function canSubmit(state: EditorState): boolean {
  if (state.mode === "idle") return false;
  if (state.conflict) return false;
  // A replace requires a precondition; without one the provider answers 428.
  if (state.mode === "editing" && !state.etag) return false;
  return true;
}

/**
 * Whether a delete may be attempted.
 *
 * Only a persisted profile can be deleted, and only at a version the user has
 * actually seen: the precondition is what makes "delete" mean "delete the
 * thing I was shown" rather than "delete whatever is there now".
 */
export function canDelete(state: EditorState): boolean {
  if (state.mode !== "editing" || !state.profileId) return false;
  if (state.conflict) return false;
  if (!state.etag) return false;
  return true;
}
