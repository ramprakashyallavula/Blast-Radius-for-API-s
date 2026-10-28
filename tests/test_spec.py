from pathlib import Path

import pytest

from blastradius.spec import Spec, SpecError

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_loads_example_spec():
    spec = Spec.load(EXAMPLES / "orders-service" / "openapi.v1.yaml")

    assert spec.title == "Orders Service"
    assert spec.version == "1.0.0"
    assert spec.openapi_version == "3.0.3"
    assert spec.paths == ["/orders", "/orders/{orderId}", "/orders/{orderId}/tracking"]


def test_loads_json(tmp_path):
    f = tmp_path / "api.json"
    f.write_text('{"openapi": "3.1.0", "info": {"title": "T", "version": "2"}, "paths": {}}')

    assert Spec.load(f).openapi_version == "3.1.0"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(SpecError, match="could not read"):
        Spec.load(tmp_path / "nope.yaml")


def test_invalid_yaml_is_a_clear_error(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("openapi: 3.0.0\npaths: [unclosed")

    with pytest.raises(SpecError, match="not valid YAML"):
        Spec.load(f)


@pytest.mark.parametrize(
    "doc, message",
    [
        (["a", "list"], "object at the top level"),
        ({"swagger": "2.0", "paths": {}}, "only OpenAPI 3.x"),
        ({"openapi": "3.0.0"}, "missing 'paths'"),
    ],
)
def test_rejects_non_openapi3_documents(doc, message):
    with pytest.raises(SpecError, match=message):
        Spec(doc)
