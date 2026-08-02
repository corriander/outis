"""Pure frontend behavior for the external ProfileService authoring island.

The editor renders whatever field document the service serves, so these tests
use a generic form rather than any provider's real schema: what is asserted is
that the vocabulary is honoured, not that a particular field exists.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "generated" / "cookbookProfileEditorModel.js"
HAS_NODE = shutil.which("node") is not None

_IMPORTS = ", ".join(
    [
        "applyConflict",
        "applyPreview",
        "applySaved",
        "applyWriteFailure",
        "artifactRefFor",
        "authorityAccepted",
        "beginDraft",
        "beginEdit",
        "canDelete",
        "canSubmit",
        "clearEditor",
        "coerceFieldValue",
        "createEditorState",
        "defaultValues",
        "formLayout",
        "isDirty",
        "localFieldHints",
        "nextPreviewToken",
        "parseFormDocument",
        "pointerFieldId",
        "previewValues",
        "profileFromEnvelope",
        "profileSummaries",
        "providerFeedback",
        "resolveConflictWithLocal",
        "resolveConflictWithRemote",
        "seededValues",
        "setFieldValue",
        "submissionValues",
        "widgetForField",
    ]
)


def _run(expression: str, setup: str = ""):
    script = (
        f"import {{ {_IMPORTS} }} from '{MODULE.as_uri()}';"
        f"{setup}"
        f"console.log(JSON.stringify({expression}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


FORM = {
    "form_version": 1,
    "groups": [
        {"id": "identity", "label": "Identity", "order": 10},
        {"id": "runtime", "label": "Runtime", "order": 30},
        {"id": "placement", "label": "Placement", "order": 20},
    ],
    "fields": [
        {
            "id": "threads",
            "label": "Threads",
            "kind": "integer",
            "widget": "number",
            "group": "runtime",
            "order": 10,
            "constraints": {"min": 1, "max": 64},
            "default": None,
            "nullable": True,
        },
        {
            "id": "title",
            "label": "Title",
            "kind": "string",
            "widget": "text",
            "group": "identity",
            "order": 10,
            "constraints": {"pattern": "^[A-Za-z0-9._-]+$"},
            "default": None,
            "nullable": False,
        },
        {
            "id": "tags",
            "label": "Tags",
            "kind": "list",
            "widget": "chips",
            "group": "identity",
            "order": 20,
            "item": {"kind": "enum", "allowed": ["text", "vision"]},
            "default": ["text"],
            "nullable": False,
        },
        {
            "id": "site",
            "label": "Site",
            "kind": "enum",
            "widget": "select",
            "group": "placement",
            "order": 10,
            "allowed": ["local", "remote"],
            "default": "local",
            "nullable": False,
        },
        {
            "id": "verbose",
            "label": "Verbose",
            "kind": "boolean",
            "widget": "toggle",
            "group": "runtime",
            "order": 20,
            "default": False,
            "nullable": True,
        },
    ],
    "widget_fallbacks": {"string": "text", "integer": "number"},
}

FORM_JSON = json.dumps(FORM)
SETUP = f"const FORM = {FORM_JSON};"


# -- form vocabulary ------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_layout_follows_provider_order_not_document_order():
    layout = _run(
        "formLayout(FORM).map(g => [g.id, g.fields.map(f => f.id)])", SETUP
    )

    assert layout == [
        ["identity", ["title", "tags"]],
        ["placement", ["site"]],
        ["runtime", ["threads", "verbose"]],
    ]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_field_in_an_undeclared_group_still_renders():
    # Dropping it would hide a value that a replace still submits -- and a
    # replace omitting a key clears it, so an invisible field becomes data loss.
    setup = (
        SETUP
        + "const doc = {...FORM, fields: [...FORM.fields, "
        "{id:'later', kind:'string', group:'group-added-after-this-release'}]};"
    )
    layout = _run("formLayout(doc).map(g => [g.id, g.fields.map(f => f.id)])", setup)

    assert layout[-1][1] == ["later"]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_widget_resolution_prefers_field_then_provider_fallback_then_ours():
    assert _run("widgetForField({id:'a', kind:'string', widget:'textarea'}, FORM)", SETUP) == "textarea"
    assert _run("widgetForField({id:'a', kind:'integer'}, FORM)", SETUP) == "number"
    # The provider's fallback map does not cover enum; ours does.
    assert _run("widgetForField({id:'a', kind:'enum'}, FORM)", SETUP) == "select"
    # An unfamiliar kind still gets an editable control rather than vanishing.
    assert _run("widgetForField({id:'a', kind:'duration'}, FORM)", SETUP) == "text"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_document_without_usable_fields_is_rejected():
    assert _run("parseFormDocument({fields: [{label: 'no id'}]})") is None
    assert _run("parseFormDocument({})") is None
    assert _run("parseFormDocument(null)") is None


# -- values ---------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_defaults_seed_every_declared_field():
    assert _run("defaultValues(FORM)", SETUP) == {
        "threads": None,
        "title": None,
        "tags": ["text"],
        "site": "local",
        "verbose": False,
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_supplied_values_win_over_defaults():
    assert _run("seededValues(FORM, {site:'remote'}).site", SETUP) == "remote"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_submission_carries_every_field_because_replace_is_lossy():
    # The service clears an omitted key rather than leaving it alone, so an
    # untouched field must still be sent with its current value.
    payload = _run("submissionValues(FORM, {title:'a'}, defaultValues(FORM))", SETUP)

    assert sorted(payload) == ["site", "tags", "threads", "title", "verbose"]
    assert payload["site"] == "local"
    assert payload["title"] == "a"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_submission_preserves_a_key_this_form_never_declared():
    # A provider that gained a field after the form was fetched must not have
    # it wiped by an editor that never knew about it.
    payload = _run(
        "submissionValues(FORM, seededValues(FORM, {added_later: 7}), {added_later: 7})",
        SETUP,
    )

    assert payload["added_later"] == 7


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_control_input_is_coerced_to_the_declared_wire_type():
    integer = "{id:'n', kind:'integer', nullable:true}"
    assert _run(f"coerceFieldValue({integer}, '12')") == 12
    assert _run(f"coerceFieldValue({integer}, '')") is None
    assert _run("coerceFieldValue({id:'b', kind:'boolean'}, 'on')") is True
    assert _run("coerceFieldValue({id:'b', kind:'boolean'}, false)") is False
    assert _run("coerceFieldValue({id:'l', kind:'list'}, 'text, vision')") == ["text", "vision"]
    assert _run("coerceFieldValue({id:'s', kind:'string', nullable:true}, '')") is None
    assert _run("coerceFieldValue({id:'s', kind:'string', nullable:false}, '')") == ""


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_an_unparseable_number_is_submitted_rather_than_discarded():
    # Silently dropping it would leave the user staring at their own typing
    # with no explanation. The provider owns validation and names the problem.
    assert _run("coerceFieldValue({id:'n', kind:'integer', nullable:true}, 'eight')") == "eight"


# -- provider feedback ----------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_pointers_route_field_faults_to_their_field():
    assert _run("pointerFieldId('/values/threads')") == "threads"
    # A list element's fault belongs to the list field.
    assert _run("pointerFieldId('/values/tags/0')") == "tags"
    # RFC 6901 escaping: ~1 is a literal slash in the field name.
    assert _run("pointerFieldId('/values/a~1b')") == "a/b"
    assert _run("pointerFieldId('/artifact_ref/artifact_id')") is None
    assert _run("pointerFieldId('/')") is None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_field_and_profile_level_faults_are_separated():
    body = json.dumps(
        {
            "errors": [
                {"pointer": "/values/title", "code": "pattern", "message": "Bad title."},
                {"pointer": "/values/title", "code": "length", "message": "Too long."},
                {"pointer": "/", "code": "conflict", "message": "Profile-level."},
                {
                    "pointer": "/artifact_ref/artifact_id",
                    "code": "artifact_not_found",
                    "message": "Unknown artifact.",
                },
            ],
            "warnings": [
                {"pointer": "/artifact_ref", "code": "artifact_incomplete", "message": "Parts missing."}
            ],
        }
    )
    feedback = _run(f"providerFeedback({body})")

    assert feedback["fieldErrors"] == {"title": ["Bad title.", "Too long."]}
    assert [error["message"] for error in feedback["formErrors"]] == [
        "Profile-level.",
        "Unknown artifact.",
    ]
    assert [warning["code"] for warning in feedback["warnings"]] == ["artifact_incomplete"]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_local_hints_come_only_from_provider_supplied_constraints():
    hints = _run(
        "localFieldHints(FORM, {title:'has spaces', threads:99, site:'local', tags:['text'], verbose:false})",
        SETUP,
    )

    assert "title" in hints
    assert "threads" in hints
    assert "site" not in hints


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_required_field_left_empty_is_hinted():
    hints = _run("localFieldHints(FORM, {title:null, tags:[]})", SETUP)

    assert hints["title"] == ["Required."]
    assert hints["tags"] == ["Required."]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_an_unusable_provider_pattern_does_not_take_the_form_down():
    # A pattern the browser's RegExp cannot compile yields no hint rather than
    # throwing partway through rendering every other field.
    setup = "const doc = {fields:[{id:'x', kind:'string', constraints:{pattern:'(?<'}}]};"
    assert _run("localFieldHints(doc, {x:'anything'})", setup) == {}


# -- editor state ---------------------------------------------------------

DRAFT = json.dumps(
    {
        "data": {
            "values": {"title": "seeded", "threads": 4},
            "form_version": 1,
            "artifact_ref": {"authority": "store-a", "artifact_id": "abc"},
        },
        "warnings": [],
    }
)

STATE_SETUP = (
    SETUP
    + f"let s = beginDraft(createEditorState(), FORM, {DRAFT}, null);"
)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_draft_has_no_version_to_precondition_on():
    state = _run("s", STATE_SETUP)

    assert state["mode"] == "new"
    assert state["profileId"] is None
    assert state["etag"] is None
    assert state["values"]["title"] == "seeded"
    # Fields the draft did not mention still carry the form's defaults.
    assert state["values"]["site"] == "local"


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_editing_an_existing_profile_keeps_its_version_for_if_match():
    setup = SETUP + (
        "const profile = {id:'p1', label:'p1', etag:'\"body\"', values:{title:'x'}, artifact_ref:null};"
        "let s = beginEdit(createEditorState(), FORM, profile, '\"header\"');"
    )
    state = _run("s", setup)

    assert state["mode"] == "editing"
    assert state["profileId"] == "p1"
    # The read's header wins over the list body's hint.
    assert state["etag"] == '"header"'


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_replace_without_a_version_is_refused_before_it_becomes_a_428():
    setup = SETUP + (
        "const profile = {id:'p1', label:'p1', etag:null, values:{}, artifact_ref:null};"
        "let s = beginEdit(createEditorState(), FORM, profile, null);"
    )

    assert _run("canSubmit(s)", setup) is False


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_stale_preview_reply_cannot_overwrite_newer_feedback():
    # Validation is debounced, so a slow reply for older values can land after
    # the user has already typed past it.
    body = json.dumps({"errors": [{"pointer": "/values/title", "code": "e", "message": "Stale."}]})
    setup = STATE_SETUP + "s = nextPreviewToken(s); const stale = s.previewToken; s = nextPreviewToken(s);"
    state = _run(f"applyPreview(s, stale, {body})", setup)

    assert state["fieldErrors"] == {}


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_the_current_preview_reply_is_applied():
    body = json.dumps({"errors": [{"pointer": "/values/title", "code": "e", "message": "Bad."}]})
    setup = STATE_SETUP + "s = nextPreviewToken(s); const token = s.previewToken;"
    state = _run(f"applyPreview(s, token, {body})", setup)

    assert state["fieldErrors"] == {"title": ["Bad."]}


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_rejected_write_leaves_the_draft_intact():
    body = json.dumps({"errors": [{"pointer": "/values/title", "code": "e", "message": "No."}]})
    setup = STATE_SETUP + "s = setFieldValue(s, FORM.fields[1], 'mine');"
    state = _run(f"applyWriteFailure(s, {body})", setup)

    assert state["values"]["title"] == "mine"
    assert state["fieldErrors"] == {"title": ["No."]}


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_editing_a_field_clears_the_provider_error_it_answers():
    body = json.dumps({"errors": [{"pointer": "/values/title", "code": "e", "message": "No."}]})
    setup = STATE_SETUP + f"s = applyWriteFailure(s, {body});"
    state = _run("setFieldValue(s, FORM.fields[1], 'fixed')", setup)

    assert state["fieldErrors"] == {}


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_conflict_keeps_the_users_draft_and_blocks_a_blind_retry():
    setup = STATE_SETUP + (
        "s = setFieldValue(s, FORM.fields[1], 'mine');"
        "const remote = {id:'p1', label:'p1', etag:'\"fresh\"', values:{title:'theirs'}, artifact_ref:null};"
        "s = applyConflict(s, 'changed', remote);"
    )
    state = _run("s", setup)

    assert state["values"]["title"] == "mine"
    assert state["conflict"]["remoteValues"]["title"] == "theirs"
    # Nothing may be written until the user chooses between the two versions.
    assert _run("canSubmit(s)", setup) is False


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_keeping_local_edits_retries_against_the_current_version():
    setup = STATE_SETUP + (
        "s = setFieldValue(s, FORM.fields[1], 'mine');"
        "const remote = {id:'p1', label:'p1', etag:'\"fresh\"', values:{title:'theirs'}, artifact_ref:null};"
        "s = applyConflict(s, 'changed', remote);"
        "s = resolveConflictWithLocal(s);"
    )
    state = _run("s", setup)

    assert state["values"]["title"] == "mine"
    assert state["etag"] == '"fresh"'
    assert state["conflict"] is None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_taking_the_providers_version_replaces_the_draft_and_its_version():
    setup = STATE_SETUP + (
        "s = setFieldValue(s, FORM.fields[1], 'mine');"
        "const remote = {id:'p1', label:'p1', etag:'\"fresh\"', values:{title:'theirs'}, artifact_ref:null};"
        "s = applyConflict(s, 'changed', remote);"
        "s = resolveConflictWithRemote(s, FORM);"
    )
    state = _run("s", setup)

    assert state["values"]["title"] == "theirs"
    assert state["etag"] == '"fresh"'
    assert _run("isDirty(s)", setup) is False


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_saved_profile_becomes_the_new_baseline():
    saved = json.dumps(
        {"data": {"profile": {"id": "p1", "values": {"title": "saved"}, "etag": '"body"'}}}
    )
    setup = STATE_SETUP + f"s = applySaved(s, FORM, profileFromEnvelope({saved}), '\"header\"');"
    state = _run("s", setup)

    assert state["mode"] == "editing"
    assert state["profileId"] == "p1"
    assert state["etag"] == '"header"'
    assert _run("isDirty(s)", setup) is False


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_draft_cannot_be_deleted_because_nothing_is_persisted():
    assert _run("canDelete(s)", STATE_SETUP) is False


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_deleting_needs_a_version_the_user_has_actually_seen():
    # Without a precondition "delete" would mean "delete whatever is there
    # now", which is not what the user was shown.
    without = SETUP + (
        "const p = {id:'p1', label:'p1', etag:null, values:{}, artifact_ref:null};"
        "let s = beginEdit(createEditorState(), FORM, p, null);"
    )
    with_etag = SETUP + (
        "const p = {id:'p1', label:'p1', etag:'\"v1\"', values:{}, artifact_ref:null};"
        "let s = beginEdit(createEditorState(), FORM, p, null);"
    )

    assert _run("canDelete(s)", without) is False
    assert _run("canDelete(s)", with_etag) is True


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_an_unresolved_conflict_blocks_the_delete_as_well_as_the_save():
    setup = SETUP + (
        "const p = {id:'p1', label:'p1', etag:'\"v1\"', values:{}, artifact_ref:null};"
        "let s = beginEdit(createEditorState(), FORM, p, null);"
        "const remote = {id:'p1', label:'p1', etag:'\"v2\"', values:{}, artifact_ref:null};"
        "s = applyConflict(s, 'changed', remote);"
    )

    assert _run("canDelete(s)", setup) is False
    # Adopting the version just read is what unblocks it.
    assert _run("canDelete(resolveConflictWithLocal(s))", setup) is True


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_clearing_the_editor_invalidates_a_preview_still_in_flight():
    setup = STATE_SETUP + "s = nextPreviewToken(s); const token = s.previewToken; s = clearEditor(s);"
    body = json.dumps({"errors": [{"pointer": "/values/title", "code": "e", "message": "Gone."}]})
    state = _run(f"applyPreview(s, token, {body})", setup)

    assert state["mode"] == "idle"
    assert state["profileId"] is None
    assert state["etag"] is None
    assert state["fieldErrors"] == {}


# -- wire shapes ----------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_list_entries_carry_their_own_validator_in_the_body():
    # A response header cannot carry a validator for each of many items.
    body = json.dumps(
        {
            "data": {
                "profiles": [
                    {"id": "b", "values": {}, "etag": '"2"'},
                    {"id": "a", "values": {}, "etag": '"1"'},
                    {"values": {}},
                ]
            }
        }
    )
    summaries = _run(f"profileSummaries({body})")

    assert [(entry["id"], entry["etag"]) for entry in summaries] == [("a", '"1"'), ("b", '"2"')]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_a_rejected_preview_answers_200_with_no_values():
    rejected = json.dumps({"data": None, "errors": [{"pointer": "/", "code": "e", "message": "no"}]})
    accepted = json.dumps({"data": {"values": {"title": "ok"}}, "errors": []})

    assert _run(f"previewValues({rejected})") is None
    assert _run(f"previewValues({accepted})") == {"title": "ok"}


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_an_artifact_reference_carries_identity_and_never_a_path():
    artifact = json.dumps(
        {
            "id": "models:family/x.gguf:0123",
            "observation": "0123456789ab",
            "filename": "x.gguf",
            "path_variants": [{"label": "Runtime host", "value": "/srv/models/x.gguf"}],
            "display_location": "Local models / family / x.gguf",
        }
    )
    ref = _run(f"artifactRefFor({{id:'store-a'}}, {artifact})")

    assert ref == {
        "authority": "store-a",
        "artifact_id": "models:family/x.gguf:0123",
        "observation": "0123456789ab",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_an_artifact_without_an_authority_yields_no_reference():
    assert _run("artifactRefFor({}, {id:'x'})") is None
    assert _run("artifactRefFor({id:'store-a'}, {})") is None


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_accepted_authorities_come_from_the_discovery_document():
    assert _run("authorityAccepted({accepted_authorities:['store-a']}, 'store-a')") is True
    assert _run("authorityAccepted({accepted_authorities:['store-a']}, 'store-b')") is False
    # A service that declares none is not treated as refusing everything.
    assert _run("authorityAccepted({accepted_authorities:[]}, 'store-a')") is True
