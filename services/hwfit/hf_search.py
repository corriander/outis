"""Broad Hugging Face repository discovery.

This deliberately does not apply Cookbook runtime or hardware filters, and
compatibility remains explicitly unassessed until a provider can inspect a
selected repository or artifact.

Ordering is ``sort=downloads``, deliberately: the plain ``api/models`` endpoint
has NO true relevance sort — its unsorted default is effectively arbitrary
match order (live check 2026-07-19: a "opus" query surfaced zero-download
``opus-mt-*-finetuned`` scraps). Downloads is the least-bad ordering the
endpoint offers; the Hub website's relevance search uses a different API.
"""

import re
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


HF_MODELS_API = "https://huggingface.co/api/models"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _next_cursor(link_header: str) -> str:
    if not link_header:
        return ""
    for part in link_header.split(","):
        match = re.search(r'<([^>]+)>;\s*rel="next"', part.strip())
        if not match:
            continue
        parsed = urlparse(match.group(1))
        if parsed.scheme != "https" or parsed.netloc != "huggingface.co" or parsed.path != "/api/models":
            return ""
        values = parse_qs(parsed.query).get("cursor") or []
        return values[0] if values else ""
    return ""


def _compatibility_annotations(entry: dict) -> list[str]:
    tags = [str(tag) for tag in (entry.get("tags") or [])]
    tag_text = " ".join(tags).lower()
    annotations = []
    if any(marker in tag_text for marker in ("lora", "adapter", "peft", "qlora")):
        annotations.append("Adapter or finetune metadata detected")
    if not entry.get("pipeline_tag"):
        annotations.append("Pipeline metadata is missing")
    if not tags:
        annotations.append("Tag metadata is incomplete")
    if entry.get("gated"):
        annotations.append("Repository access may require approval or a token")
    return annotations


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalise_model(entry: dict, query: str) -> dict | None:
    repo_id = str(entry.get("modelId") or entry.get("id") or "").strip()
    if not repo_id:
        return None
    tags = [str(tag) for tag in (entry.get("tags") or [])]
    # `full=true` responses carry the Hub's safetensors-declared parameter
    # total for many repos — real metadata that beats a name parse when the
    # hit gets enriched into a catalogue row downstream.
    safetensors = entry.get("safetensors")
    declared_params = _as_int(safetensors.get("total")) if isinstance(safetensors, dict) else 0
    return {
        "repo_id": repo_id,
        "name": repo_id,
        "author": str(entry.get("author") or repo_id.split("/", 1)[0]),
        "url": f"https://huggingface.co/{repo_id}",
        "downloads": _as_int(entry.get("downloads")),
        "likes": _as_int(entry.get("likes")),
        "params": declared_params,
        "pipeline_tag": str(entry.get("pipeline_tag") or ""),
        "tags": tags,
        "last_modified": str(entry.get("lastModified") or ""),
        "created_at": str(entry.get("createdAt") or ""),
        "gated": entry.get("gated") or False,
        "private": bool(entry.get("private")),
        "library_name": str(entry.get("library_name") or ""),
        "relevance": {
            "source": "huggingface",
            "query": query,
            "ordering": "downloads",
        },
        "compatibility": {
            "status": "unknown",
            "annotations": _compatibility_annotations(entry),
        },
    }


async def search_huggingface_models(
    query: str,
    *,
    limit: int = 50,
    cursor: str = "",
    client=None,
) -> dict:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(limit), 100))
    cursor = str(cursor or "").strip()
    if len(cursor) > 2048 or _CONTROL_CHARS.search(cursor):
        raise ValueError("invalid cursor")

    params = {
        "search": query,
        "sort": "downloads",
        "direction": "-1",
        "limit": str(limit),
        "full": "true",
    }
    if cursor:
        params["cursor"] = cursor
    url = f"{HF_MODELS_API}?{urlencode(params)}"

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=15, follow_redirects=False)
    try:
        response = await client.get(url, headers={"User-Agent": "outis-cookbook/1.0"})
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await client.aclose()

    rows = payload if isinstance(payload, list) else []
    models = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = _normalise_model(row, query)
        if model:
            models.append(model)

    return {
        "query": query,
        "ordering": "downloads",
        "models": models,
        "next_cursor": _next_cursor(response.headers.get("link", "")),
    }
