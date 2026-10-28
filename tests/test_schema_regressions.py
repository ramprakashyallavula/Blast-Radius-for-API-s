"""Regression tests for schema cases that used to be missed silently.

Each test names the bug: before the fix, every one of these diffs reported no change.
"""

import pytest

from blastradius.diff import diff_specs
from blastradius.impact import assess
from blastradius.models import CallSite, Change, Impact
from blastradius.models import ChangeKind as K
from blastradius.models import Severity as S
from blastradius.spec import Spec, SpecError


def spec(paths, schemas=None):
    return Spec({"openapi": "3.1.0", "paths": paths, "components": {"schemas": schemas or {}}})


def ok(schema, code="200", media="application/json"):
    return {code: {"description": "ok", "content": {media: {"schema": schema}}}}


def get(schema):
    return {"/x": {"get": {"responses": ok(schema)}}}


def post(schema, media="application/json"):
    return {"/x": {"post": {"requestBody": {"content": {media: {"schema": schema}}}}}}


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


STR, INT, BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}


def found(base, head):
    return {(c.kind, c.field): c for c in diff_specs(base, head)}


def site(context, method="GET"):
    return CallSite("svc", "a.py", 1, method, "/x", context.splitlines()[0], context)


# --- real unions (oneOf/anyOf with several non-null branches) --------------

CAT = obj({"kind": STR, "meows": BOOL})
DOG = obj({"kind": STR, "barks": BOOL})


def test_union_field_removed_from_one_branch_is_reported_as_uncertain():
    changes = found(
        spec(get({"oneOf": [CAT, DOG]})),
        spec(get({"oneOf": [CAT, obj({"kind": STR})]})),
    )

    change = changes[(K.RESPONSE_FIELD_REMOVED, "barks")]
    assert change.severity is S.BREAKING
    assert change.certain is False


def test_union_field_type_change_inside_a_branch():
    changes = found(
        spec(get({"anyOf": [CAT, DOG]})),
        spec(get({"anyOf": [CAT, obj({"kind": STR, "barks": STR})]})),
    )

    change = changes[(K.RESPONSE_FIELD_TYPE_CHANGED, "barks")]
    assert (change.before, change.after) == ("boolean", "string")
    assert change.certain is False


def test_union_with_a_new_branch_is_a_warning():
    changes = found(
        spec(get({"oneOf": [CAT, DOG]})),
        spec(get({"oneOf": [CAT, DOG, obj({"kind": STR, "hops": BOOL})]})),
    )

    assert changes[(K.RESPONSE_UNION_VARIANT_ADDED, None)].severity is S.WARNING


def test_request_union_losing_a_branch_is_breaking():
    changes = found(spec(post({"oneOf": [CAT, DOG]})), spec(post({"oneOf": [CAT]})))

    change = changes[(K.REQUEST_UNION_VARIANT_REMOVED, None)]
    assert change.severity is S.BREAKING
    assert "2 → 1" in change.message


def test_request_union_gaining_a_branch_is_safe():
    changes = found(spec(post({"oneOf": [CAT]})), spec(post({"oneOf": [CAT, DOG]})))

    assert all(c.severity is not S.BREAKING for c in changes.values())


def test_scalar_union_type_change_is_certain():
    changes = found(
        spec(get(obj({"id": {"oneOf": [STR, INT]}}))),
        spec(get(obj({"id": STR}))),
    )

    change = changes[(K.RESPONSE_FIELD_TYPE_CHANGED, "id")]
    assert (change.before, change.after) == ("integer|string", "string")
    assert change.certain is True


def test_fastapi_optional_is_still_treated_as_a_plain_nullable_field():
    nullable = {"anyOf": [STR, {"type": "null"}]}
    changes = found(
        spec(get(obj({"nickname": nullable}))),
        spec(get(obj({"nickname": {"anyOf": [INT, {"type": "null"}]}}))),
    )

    change = changes[(K.RESPONSE_FIELD_TYPE_CHANGED, "nickname")]
    assert change.certain is True
    assert len(changes) == 1  # no union noise for Optional[X]


def test_uncertain_changes_are_at_most_may_break():
    change = Change(K.RESPONSE_FIELD_REMOVED, S.BREAKING, "GET", "/x", "m", "barks", certain=False)

    result = assess(change, site('pet = r.json()\nprint(pet["barks"])'))

    assert result.impact is Impact.MAY_BREAK
    assert "oneOf/anyOf" in result.reason


# --- nullable fields --------------------------------------------------------


@pytest.mark.parametrize(
    "nullable_schema",
    [
        {"type": ["string", "null"]},  # OpenAPI 3.1
        {"type": "string", "nullable": True},  # OpenAPI 3.0
        {"anyOf": [STR, {"type": "null"}]},  # FastAPI Optional[str]
    ],
)
def test_response_field_becoming_nullable_is_a_warning(nullable_schema):
    changes = found(spec(get(obj({"name": STR}))), spec(get(obj({"name": nullable_schema}))))

    assert changes[(K.RESPONSE_FIELD_BECAME_NULLABLE, "name")].severity is S.WARNING


def test_request_field_no_longer_nullable_is_breaking():
    changes = found(
        spec(post(obj({"note": {"type": ["string", "null"]}}))), spec(post(obj({"note": STR})))
    )

    assert changes[(K.REQUEST_FIELD_NO_LONGER_NULLABLE, "note")].severity is S.BREAKING


def test_unchanged_nullability_is_not_reported():
    same = obj({"name": {"type": ["string", "null"]}})

    assert diff_specs(spec(get(same)), spec(get(same))) == []


# --- path parameters --------------------------------------------------------


def path_op(path, name, schema):
    param = {"name": name, "in": "path", "required": True, "schema": schema}
    return spec({path: {"get": {"parameters": [param]}}})


def test_renaming_a_path_parameter_stays_safe():
    assert diff_specs(path_op("/o/{id}", "id", INT), path_op("/o/{orderId}", "orderId", INT)) == []


@pytest.mark.parametrize(
    "before, after, kind",
    [
        (INT, STR, K.PATH_PARAM_TYPE_CHANGED),
        (STR, {"type": "string", "format": "uuid"}, K.PATH_PARAM_TYPE_CHANGED),
        (
            {"type": "string", "enum": ["a", "b"]},
            {"type": "string", "enum": ["a"]},
            K.PATH_PARAM_ENUM_RESTRICTED,
        ),
        (STR, {"type": "string", "enum": ["a"]}, K.PATH_PARAM_ENUM_RESTRICTED),
    ],
)
def test_path_parameter_value_changes_are_breaking(before, after, kind):
    changes = found(path_op("/o/{id}", "id", before), path_op("/o/{orderId}", "orderId", after))

    assert changes[(kind, "orderId")].severity is S.BREAKING


def test_loosening_a_path_parameter_enum_is_safe():
    before = {"type": "string", "enum": ["a"]}
    after = {"type": "string", "enum": ["a", "b"]}

    assert diff_specs(path_op("/o/{id}", "id", before), path_op("/o/{id}", "id", after)) == []


def test_path_parameter_changes_may_break_every_caller():
    change = Change(K.PATH_PARAM_TYPE_CHANGED, S.BREAKING, "GET", "/o/{id}", "m", "id")

    assert assess(change, site("requests.get(url)")).impact is Impact.MAY_BREAK


# --- query / header / cookie enums -----------------------------------------


@pytest.mark.parametrize("location", ["query", "header", "cookie"])
@pytest.mark.parametrize(
    "before, after, breaking",
    [
        (["a", "b"], ["a"], True),  # value removed
        (None, ["a"], True),  # newly restricted
        (["a"], ["a", "b"], False),  # loosened
        (["a"], None, False),  # restriction lifted
    ],
)
def test_parameter_enum_changes(location, before, after, breaking):
    def op(values):
        schema = {"type": "string", **({"enum": values} if values else {})}
        return spec(
            {"/x": {"get": {"parameters": [{"name": "p", "in": location, "schema": schema}]}}}
        )

    changes = found(op(before), op(after))

    assert ((K.PARAM_ENUM_RESTRICTED, "p") in changes) is breaking


def test_request_field_newly_restricted_to_an_enum():
    changes = found(
        spec(post(obj({"currency": STR}))),
        spec(post(obj({"currency": {"type": "string", "enum": ["USD"]}}))),
    )

    change = changes[(K.REQUEST_ENUM_VALUE_REMOVED, "currency")]
    assert "now only accepts" in change.message


# --- several success responses and media types -----------------------------


def test_every_success_response_is_compared():
    def op(accepted):
        return spec(
            {
                "/x": {
                    "post": {
                        "responses": {
                            **ok(obj({"id": STR}), "200"),
                            **ok(accepted, "202"),
                        }
                    }
                }
            }
        )

    changes = found(op(obj({"id": STR, "eta": STR})), op(obj({"id": STR})))

    change = changes[(K.RESPONSE_FIELD_REMOVED, "eta")]
    assert "`202`" in change.message


def test_new_success_status_is_a_warning():
    base = spec({"/x": {"post": {"responses": ok(obj({"id": STR}), "200")}}})
    head = spec({"/x": {"post": {"responses": {**ok(obj({"id": STR}), "200"), "202": {}}}}})

    assert found(base, head)[(K.RESPONSE_STATUS_ADDED, None)].after == "202"


def test_request_media_type_removed_is_breaking_and_added_is_not():
    json_only = post(obj({"a": STR}))
    form_only = post(obj({"a": STR}), media="multipart/form-data")
    both = {
        "/x": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {"schema": obj({"a": STR})},
                        "multipart/form-data": {"schema": obj({"a": STR})},
                    }
                }
            }
        }
    }

    removed = found(spec(json_only), spec(form_only))
    assert removed[(K.REQUEST_MEDIA_TYPE_REMOVED, None)].before == "application/json"
    assert diff_specs(spec(json_only), spec(both)) == []


def test_response_media_type_removed_is_breaking():
    base = spec(get(obj({"a": STR})))
    head = spec({"/x": {"get": {"responses": ok({"type": "string"}, media="text/csv")}}})

    changes = found(base, head)

    assert changes[(K.RESPONSE_MEDIA_TYPE_REMOVED, None)].severity is S.BREAKING
    # Not also reported as every field being removed.
    assert not any(k == K.RESPONSE_FIELD_REMOVED for k, _ in changes)


def test_media_type_impact():
    removed_json = Change(
        K.REQUEST_MEDIA_TYPE_REMOVED, S.BREAKING, "POST", "/x", "m", before="application/json"
    )

    assert assess(removed_json, site("requests.post(url, json=body)")).impact is Impact.WILL_BREAK
    assert assess(removed_json, site("requests.post(url, files=f)")).impact is Impact.MAY_BREAK


# --- nested $ref + allOf + nullable ----------------------------------------


def test_nested_ref_inside_all_of_and_optional():
    schemas = {
        "Base": obj({"id": STR}, required=["id"]),
        "Address": obj({"city": STR, "zip": STR}),
        "Customer": {
            "allOf": [
                {"$ref": "#/components/schemas/Base"},
                {
                    "properties": {
                        "address": {
                            "anyOf": [{"$ref": "#/components/schemas/Address"}, {"type": "null"}]
                        }
                    }
                },
            ]
        },
    }
    head_schemas = {**schemas, "Address": obj({"city": STR})}
    ref = {"$ref": "#/components/schemas/Customer"}

    changes = found(spec(get(ref), schemas), spec(get(ref), head_schemas))

    assert changes[(K.RESPONSE_FIELD_REMOVED, "address.zip")].certain is True


# --- what's still unsupported says so clearly -------------------------------


def test_external_refs_explain_how_to_bundle():
    s = spec(get({"$ref": "common.yaml#/components/schemas/Thing"}))

    with pytest.raises(SpecError, match="Bundle the spec"):
        s.flatten({"$ref": "common.yaml#/components/schemas/Thing"})


def test_unknown_change_kinds_default_to_may_break():
    # REQUEST_FIELD_ADDED_OPTIONAL is safe and never assessed; if it ever were,
    # the analyzer must not claim the caller is fine without a rule saying so.
    change = Change(K.REQUEST_FIELD_ADDED_OPTIONAL, S.SAFE, "POST", "/x", "m", "x")

    assert assess(change, site("requests.post(url)")).impact is Impact.MAY_BREAK
