from pathlib import Path

from blastradius.diff import diff_specs, field_ancestors, leaf_name
from blastradius.models import ChangeKind as K
from blastradius.models import Severity as S
from blastradius.spec import Spec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def json_body(schema, required=True):
    return {"required": required, "content": {"application/json": {"schema": schema}}}


def ok(schema):
    return {"200": {"description": "ok", "content": {"application/json": {"schema": schema}}}}


def spec(paths):
    return Spec({"openapi": "3.0.3", "paths": paths})


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


STR = {"type": "string"}


def kinds(changes):
    return {(c.kind, c.field) for c in changes}


# --- helpers ---------------------------------------------------------------


def test_field_ancestors_and_leaf_name():
    assert list(field_ancestors("items[].sku")) == ["items", "items[]", "items[].sku"]
    assert list(field_ancestors("[].id")) == ["[]", "[].id"]
    assert leaf_name("items[].sku") == "sku"
    assert leaf_name("customer.name") == "name"


# --- endpoints -------------------------------------------------------------


def test_removed_and_added_endpoints():
    base = spec({"/a": {"get": {}}, "/b": {"get": {}}})
    head = spec({"/a": {"get": {}}, "/c": {"post": {}}})

    changes = diff_specs(base, head)

    assert [(c.kind, c.endpoint, c.severity) for c in changes] == [
        (K.ENDPOINT_REMOVED, "GET /b", S.BREAKING),
        (K.ENDPOINT_ADDED, "POST /c", S.SAFE),
    ]


def test_renaming_a_path_parameter_is_not_a_change():
    base = spec({"/orders/{id}": {"get": {}}})
    head = spec({"/orders/{orderId}": {"get": {}}})

    assert diff_specs(base, head) == []


# --- parameters ------------------------------------------------------------


def test_parameter_rules():
    def params(*p):
        return spec({"/x": {"get": {"parameters": list(p)}}})

    base = params(
        {"name": "page", "in": "query", "schema": {"type": "integer"}},
        {"name": "sort", "in": "query"},
        {"name": "old", "in": "query"},
    )
    head = params(
        {"name": "page", "in": "query", "schema": {"type": "string"}},
        {"name": "sort", "in": "query", "required": True},
        {"name": "tenant", "in": "header", "required": True},
        {"name": "limit", "in": "query"},
    )

    by_kind = {c.kind: c for c in diff_specs(base, head)}

    assert by_kind[K.PARAM_TYPE_CHANGED].severity is S.BREAKING
    assert by_kind[K.PARAM_BECAME_REQUIRED].field == "sort"
    assert by_kind[K.PARAM_ADDED_REQUIRED].field == "tenant"
    assert by_kind[K.PARAM_ADDED_OPTIONAL].severity is S.SAFE
    assert by_kind[K.PARAM_REMOVED].severity is S.WARNING


# --- request body: tightening is breaking ----------------------------------


def request(schema):
    return spec({"/x": {"post": {"requestBody": json_body(schema)}}})


def test_new_required_request_field_is_breaking_but_optional_is_safe():
    base = request(obj({"a": STR}))
    head = request(obj({"a": STR, "b": STR, "c": STR}, required=["b"]))

    changes = diff_specs(base, head)

    assert kinds(changes) == {
        (K.REQUEST_FIELD_ADDED_REQUIRED, "b"),
        (K.REQUEST_FIELD_ADDED_OPTIONAL, "c"),
    }


def test_new_optional_object_with_required_children_is_safe():
    base = request(obj({"a": STR}))
    head = request(obj({"a": STR, "address": obj({"zip": STR}, required=["zip"])}))

    changes = diff_specs(base, head)

    # Reported once, at the top: clients that don't send `address` are fine.
    assert kinds(changes) == {(K.REQUEST_FIELD_ADDED_OPTIONAL, "address")}


def test_request_tightening():
    base = request(
        obj(
            {
                "qty": {"type": "integer"},
                "kind": {"type": "string", "enum": ["a", "b"]},
                "note": STR,
            }
        )
    )
    head = request(
        obj({"qty": STR, "kind": {"type": "string", "enum": ["a"]}, "note": STR}, required=["note"])
    )

    changes = diff_specs(base, head)

    assert kinds(changes) == {
        (K.REQUEST_FIELD_TYPE_CHANGED, "qty"),
        (K.REQUEST_ENUM_VALUE_REMOVED, "kind"),
        (K.REQUEST_FIELD_BECAME_REQUIRED, "note"),
    }
    assert all(c.severity is S.BREAKING for c in changes)


def test_request_loosening_is_not_breaking():
    base = request(obj({"a": STR, "kind": {"type": "string", "enum": ["x"]}}, required=["a"]))
    head = request(obj({"a": STR, "kind": {"type": "string", "enum": ["x", "y"]}}))

    assert diff_specs(base, head) == []


# --- response: taking away is breaking -------------------------------------


def response(schema):
    return spec({"/x": {"get": {"responses": ok(schema)}}})


def test_removed_response_field_is_breaking_and_reported_at_the_top():
    base = response(obj({"id": STR, "customer": obj({"name": STR, "email": STR})}))
    head = response(obj({"id": STR}))

    changes = diff_specs(base, head)

    assert kinds(changes) == {(K.RESPONSE_FIELD_REMOVED, "customer")}
    assert changes[0].severity is S.BREAKING


def test_response_rules():
    base = response(
        obj(
            {"total": {"type": "number"}, "status": {"type": "string", "enum": ["a"]}, "id": STR},
            required=["id"],
        )
    )
    head = response(
        obj({"total": STR, "status": {"type": "string", "enum": ["a", "b"]}, "id": STR, "new": STR})
    )

    by_kind = {c.kind: c for c in diff_specs(base, head)}

    assert by_kind[K.RESPONSE_FIELD_TYPE_CHANGED].severity is S.BREAKING
    assert by_kind[K.RESPONSE_ENUM_VALUE_ADDED].after == ["b"]
    assert by_kind[K.RESPONSE_ENUM_VALUE_ADDED].severity is S.WARNING
    assert by_kind[K.RESPONSE_FIELD_BECAME_OPTIONAL].field == "id"
    assert by_kind[K.RESPONSE_FIELD_ADDED].severity is S.SAFE


def test_removed_success_status_is_a_warning():
    base = spec({"/x": {"post": {"responses": {"200": {}, "201": {}}}}})
    head = spec({"/x": {"post": {"responses": {"200": {}}}}})

    (change,) = diff_specs(base, head)

    assert change.kind is K.RESPONSE_STATUS_REMOVED
    assert change.before == "201"


# --- the example Orders API ------------------------------------------------


def test_example_v1_to_v2():
    base = Spec.load(EXAMPLES / "orders-service" / "openapi.v1.yaml")
    head = Spec.load(EXAMPLES / "orders-service" / "openapi.v2.yaml")

    changes = diff_specs(base, head)
    breaking = {(c.endpoint, c.kind, c.field) for c in changes if c.severity is S.BREAKING}

    assert ("GET /orders/{orderId}/tracking", K.ENDPOINT_REMOVED, None) in breaking
    assert ("POST /orders", K.REQUEST_FIELD_ADDED_REQUIRED, "currency") in breaking
    for field in ("discount_code", "customer_name"):
        assert ("GET /orders/{orderId}", K.RESPONSE_FIELD_REMOVED, field) in breaking
        assert ("GET /orders", K.RESPONSE_FIELD_REMOVED, f"[].{field}") in breaking
    assert ("GET /orders/{orderId}", K.RESPONSE_FIELD_TYPE_CHANGED, "total") in breaking

    safe = {(c.endpoint, c.kind) for c in changes if c.severity is S.SAFE}
    assert ("GET /orders", K.PARAM_ADDED_OPTIONAL) in safe
    assert ("POST /orders/{orderId}/refunds", K.ENDPOINT_ADDED) in safe

    # Adding an enum value to a *query parameter* loosens the request: not reported.
    assert not any(c.kind is K.PARAM_TYPE_CHANGED for c in changes)
