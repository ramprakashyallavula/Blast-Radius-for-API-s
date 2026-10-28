from pathlib import Path

import pytest

from blastradius.spec import Spec, SpecError, normalize_path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def make_spec(paths, components=None):
    return Spec({"openapi": "3.0.3", "paths": paths, "components": components or {}})


# --- $ref resolution -------------------------------------------------------


def test_resolve_follows_a_ref():
    spec = make_spec({}, {"schemas": {"Order": {"type": "object"}}})

    assert spec.resolve({"$ref": "#/components/schemas/Order"}) == {"type": "object"}


def test_resolve_follows_a_chain_of_refs():
    spec = make_spec(
        {},
        {"schemas": {"A": {"$ref": "#/components/schemas/B"}, "B": {"type": "string"}}},
    )

    assert spec.resolve({"$ref": "#/components/schemas/A"}) == {"type": "string"}


def test_resolve_leaves_plain_values_alone():
    spec = make_spec({})

    assert spec.resolve({"type": "integer"}) == {"type": "integer"}
    assert spec.resolve("text") == "text"


def test_resolve_handles_json_pointer_escapes():
    spec = make_spec({"/orders/{id}": {"get": {"summary": "x"}}})

    # "/" inside a key is written as "~1" in a JSON Pointer.
    resolved = spec.resolve({"$ref": "#/paths/~1orders~1{id}/get"})

    assert resolved == {"summary": "x"}


@pytest.mark.parametrize(
    "ref, message",
    [
        ("#/components/schemas/Missing", "points to nothing"),
        ("other-file.yaml#/Order", "external \\$ref"),
    ],
)
def test_resolve_errors(ref, message):
    with pytest.raises(SpecError, match=message):
        make_spec({}).resolve({"$ref": ref})


def test_resolve_detects_cycles():
    spec = make_spec(
        {},
        {
            "schemas": {
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"$ref": "#/components/schemas/A"},
            }
        },
    )

    with pytest.raises(SpecError, match="circular"):
        spec.resolve({"$ref": "#/components/schemas/A"})


# --- operations ------------------------------------------------------------


def test_normalize_path_ignores_parameter_names():
    assert normalize_path("/orders/{orderId}") == normalize_path("/orders/{id}") == "/orders/{}"


def test_lists_every_operation_in_the_example():
    ops = Spec.load(EXAMPLES / "orders-service" / "openapi.v1.yaml").operations()

    assert sorted(str(op) for op in ops.values()) == [
        "GET /orders",
        "GET /orders/{orderId}",
        "GET /orders/{orderId}/tracking",
        "POST /orders",
    ]


def test_operation_contents_are_resolved():
    ops = Spec.load(EXAMPLES / "orders-service" / "openapi.v1.yaml").operations()

    create = ops[("POST", "/orders")]
    assert create.request_body["required"] is True
    assert set(create.responses) == {"201"}

    get_one = ops[("GET", "/orders/{}")]
    assert set(get_one.responses) == {"200", "404"}
    # Path-level parameters are inherited by the method.
    assert ("path", "orderId") in get_one.params


def test_method_parameter_overrides_path_parameter():
    spec = make_spec(
        {
            "/items": {
                "parameters": [{"name": "limit", "in": "query", "required": False}],
                "get": {"parameters": [{"name": "limit", "in": "query", "required": True}]},
            }
        }
    )

    op = spec.operations()[("GET", "/items")]

    assert op.params[("query", "limit")]["required"] is True


def test_parameters_and_responses_can_be_refs():
    spec = make_spec(
        {
            "/items": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/Limit"}],
                    "responses": {200: {"$ref": "#/components/responses/Ok"}},
                }
            }
        },
        {
            "parameters": {"Limit": {"name": "limit", "in": "query"}},
            "responses": {"Ok": {"description": "fine"}},
        },
    )

    op = spec.operations()[("GET", "/items")]

    assert ("query", "limit") in op.params
    assert op.responses == {"200": {"description": "fine"}}
