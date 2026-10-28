from pathlib import Path

from blastradius.spec import FieldInfo, Spec, schema_type

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def make_spec(schemas=None):
    return Spec({"openapi": "3.0.3", "paths": {}, "components": {"schemas": schemas or {}}})


def test_flattens_nested_objects_and_arrays():
    spec = make_spec()
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "string"},
            "customer": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
            },
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"sku": {"type": "string"}}},
            },
        },
    }

    fields = spec.flatten(schema)

    assert fields == {
        "id": FieldInfo("string", True),
        "customer": FieldInfo("object", False),
        "customer.name": FieldInfo("string", True),
        "customer.email": FieldInfo("string", False),
        "items": FieldInfo("array", False),
        "items[]": FieldInfo("object", True),
        "items[].sku": FieldInfo("string", False),
    }


def test_top_level_array():
    fields = make_spec().flatten(
        {"type": "array", "items": {"properties": {"id": {"type": "string"}}}}
    )

    assert set(fields) == {"[]", "[].id"}


def test_follows_refs_and_keeps_enums():
    spec = make_spec({"Status": {"type": "string", "enum": ["a", "b"]}})

    fields = spec.flatten({"properties": {"status": {"$ref": "#/components/schemas/Status"}}})

    assert fields["status"] == FieldInfo("string", False, ("a", "b"))


def test_merges_all_of():
    spec = make_spec(
        {"Base": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}}
    )
    schema = {
        "allOf": [
            {"$ref": "#/components/schemas/Base"},
            {"properties": {"total": {"type": "number"}}, "required": ["total"]},
        ]
    }

    fields = spec.flatten(schema)

    assert fields == {"id": FieldInfo("string", True), "total": FieldInfo("number", True)}


def test_recursive_schema_does_not_loop_forever():
    spec = make_spec(
        {
            "Category": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "children": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Category"},
                    },
                },
            }
        }
    )

    fields = spec.flatten({"$ref": "#/components/schemas/Category"})

    assert fields["children[]"] == FieldInfo("object", True)
    assert "children[].name" not in fields


def test_schema_type_details():
    assert schema_type({"type": "string", "format": "date-time"}) == "string(date-time)"
    assert schema_type({"type": ["string", "null"]}) == "string"  # OpenAPI 3.1 nullable
    assert schema_type({"properties": {}}) == "object"
    assert schema_type({}) is None


def test_example_order_response_and_request():
    spec = Spec.load(EXAMPLES / "orders-service" / "openapi.v1.yaml")
    ops = spec.operations()

    order = spec.flatten(spec.success_schema(ops[("GET", "/orders/{}")]))
    assert order["total"] == FieldInfo("number", True)
    assert order["discount_code"] == FieldInfo("string", False)
    assert order["status"].enum == ("pending", "paid", "shipped", "cancelled")
    assert order["items[].sku"] == FieldInfo("string", True)

    new_order = spec.flatten(spec.request_schema(ops[("POST", "/orders")]))
    assert set(new_order) == {
        "customer_name",
        "items",
        "items[]",
        "items[].sku",
        "items[].quantity",
    }

    # The list endpoint returns an array of orders.
    assert "[].total" in spec.flatten(spec.success_schema(ops[("GET", "/orders")]))


def test_success_schema_skips_error_and_empty_responses():
    spec = make_spec()
    spec.doc["paths"] = {
        "/x": {
            "delete": {
                "responses": {
                    "204": {"description": "no content"},
                    "400": {"content": {"application/json": {"schema": {"type": "object"}}}},
                }
            }
        }
    }

    op = spec.operations()[("DELETE", "/x")]

    assert spec.success_schema(op) is None


def test_any_of_with_null_is_treated_as_the_type():
    spec = make_spec({"Address": {"type": "object", "properties": {"city": {"type": "string"}}}})
    schema = {
        "properties": {
            "nickname": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "address": {"anyOf": [{"$ref": "#/components/schemas/Address"}, {"type": "null"}]},
        }
    }

    fields = spec.flatten(schema)

    assert fields["nickname"].type == "string"
    assert fields["address.city"].type == "string"


def test_real_unions_are_compared_by_their_types():
    spec = make_spec()

    fields = spec.flatten(
        {
            "properties": {
                "id": {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}
            }
        }
    )

    assert fields["id"].type == "integer|string"
