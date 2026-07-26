/*
 * Typed island for the external ArtifactInventory v1 contract.
 *
 * Odysseus serves handwritten browser modules directly, so the generated
 * JavaScript is committed under static/js/generated. Keep browser DOM work in
 * cookbookInventory.js; this module owns untrusted-wire normalization and the
 * pure inventory view model.
 */
function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}
function stringOrEmpty(value) {
    return typeof value === "string" ? value : "";
}
function stringArray(value) {
    return Array.isArray(value)
        ? value.filter((item) => typeof item === "string")
        : [];
}
function observationOf(artifact) {
    if (!isRecord(artifact))
        return {};
    return isRecord(artifact.observed) ? artifact.observed : {};
}
export function parseInventoryDocument(value) {
    if (!isRecord(value) || value.schema_version !== 1 || !Array.isArray(value.artifacts)) {
        return null;
    }
    if (!value.artifacts.every((artifact) => isRecord(artifact) && typeof artifact.id === "string")) {
        return null;
    }
    return value;
}
/**
 * Non-ready sources, carrying the provider's own reason when it supplies one.
 *
 * A provider may omit `error`; the state alone is still reportable. The reason
 * is provider-authored text and is never parsed — only displayed.
 */
export function inventorySourceIssues(value) {
    if (!isRecord(value))
        return [];
    const status = isRecord(value.status) ? value.status : {};
    const sources = Array.isArray(status.sources) ? status.sources : [];
    return sources.flatMap((source) => {
        if (!isRecord(source))
            return [];
        const state = stringOrEmpty(source.state);
        if (state === "ready")
            return [];
        const label = stringOrEmpty(source.label)
            || stringOrEmpty(source.id)
            || "Unnamed source";
        return [{ label, state: state || "unknown", error: stringOrEmpty(source.error) }];
    });
}
/**
 * Split-part chip text.
 *
 * `parts_expected` is optional: a provider omits it when the observed parts
 * disagree about the total, so no single expected count exists. Render `?`
 * rather than coercing the absent value to zero and claiming "N/0 parts".
 */
export function artifactSplitLabel(artifact) {
    const observed = observationOf(artifact);
    if (!isRecord(observed.split))
        return "";
    const present = Number(observed.split.parts_present ?? 0);
    if (!Number.isFinite(present) || present <= 0)
        return "";
    const expected = Number(observed.split.parts_expected);
    return Number.isFinite(expected) && expected > 0
        ? `${present}/${expected} parts`
        : `${present}/? parts`;
}
export function artifactPathVariants(artifact) {
    if (!isRecord(artifact) || !Array.isArray(artifact.path_variants))
        return [];
    return artifact.path_variants.flatMap((variant) => {
        if (!isRecord(variant)
            || typeof variant.label !== "string"
            || typeof variant.value !== "string") {
            return [];
        }
        return [{ label: variant.label, value: variant.value }];
    });
}
export function artifactPathVariantValue(artifact, index) {
    const numericIndex = typeof index === "number" ? index : Number(index);
    if (!Number.isInteger(numericIndex) || numericIndex < 0)
        return null;
    return artifactPathVariants(artifact)[numericIndex]?.value ?? null;
}
export function formatArtifactBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0)
        return "Unknown size";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const power = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    const amount = bytes / (1024 ** power);
    const digits = power >= 3 ? 1 : (power === 0 ? 0 : amount < 10 ? 1 : 0);
    return `${amount.toFixed(digits)} ${units[power]}`;
}
export function artifactSearchText(artifact) {
    if (!isRecord(artifact))
        return "";
    const observed = observationOf(artifact);
    return [
        artifact.filename,
        artifact.logical_path,
        artifact.display_location,
        ...stringArray(artifact.group_path),
        ...artifactPathVariants(artifact).flatMap((variant) => [variant.label, variant.value]),
        observed.format,
        observed.quantization,
        observed.state,
    ].filter(Boolean).join(" ").toLowerCase();
}
export function filterInventoryArtifacts(artifacts, query) {
    const items = Array.isArray(artifacts)
        ? artifacts.filter((artifact) => isRecord(artifact) && typeof artifact.id === "string")
        : [];
    const terms = String(query || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length)
        return items.slice();
    return items.filter((artifact) => {
        const haystack = artifactSearchText(artifact);
        return terms.every((term) => haystack.includes(term));
    });
}
const INVENTORY_COLLATOR = new Intl.Collator(undefined, {
    numeric: true,
    sensitivity: "base",
});
function artifactDirectory(artifact) {
    const logicalPath = stringOrEmpty(artifact.logical_path);
    if (logicalPath) {
        const segments = logicalPath.split("/");
        segments.pop();
        return segments.join(" / ");
    }
    const location = stringOrEmpty(artifact.display_location);
    const filename = stringOrEmpty(artifact.filename);
    const suffix = filename ? ` / ${filename}` : "";
    if (suffix && location.endsWith(suffix))
        return location.slice(0, -suffix.length);
    return stringArray(artifact.group_path).join(" / ");
}
export function sortInventoryArtifacts(artifacts, mode = "path") {
    const items = filterInventoryArtifacts(artifacts, "");
    return items.sort((left, right) => {
        const leftName = stringOrEmpty(left.filename);
        const rightName = stringOrEmpty(right.filename);
        const leftDirectory = artifactDirectory(left);
        const rightDirectory = artifactDirectory(right);
        const primary = mode === "filename"
            ? INVENTORY_COLLATOR.compare(leftName, rightName)
            : INVENTORY_COLLATOR.compare(leftDirectory, rightDirectory);
        if (primary)
            return primary;
        const secondary = mode === "filename"
            ? INVENTORY_COLLATOR.compare(leftDirectory, rightDirectory)
            : INVENTORY_COLLATOR.compare(leftName, rightName);
        if (secondary)
            return secondary;
        return INVENTORY_COLLATOR.compare(left.id, right.id);
    });
}
export function artifactDisplayLabels(artifact, mode = "path") {
    if (!isRecord(artifact)) {
        return { primary: "Unnamed GGUF", secondary: "Unnamed GGUF" };
    }
    const filename = stringOrEmpty(artifact.filename) || "Unnamed GGUF";
    const location = stringOrEmpty(artifact.display_location) || filename;
    if (mode === "filename") {
        return { primary: filename, secondary: location };
    }
    const logicalPath = stringOrEmpty(artifact.logical_path);
    const groupPath = stringArray(artifact.group_path);
    const relativeLocation = logicalPath
        ? logicalPath.split("/").join(" / ")
        : Array.isArray(artifact.group_path)
            ? [...groupPath, filename].join(" / ")
            : location.split(" / ").slice(1).join(" / ") || filename;
    return { primary: relativeLocation, secondary: location };
}
