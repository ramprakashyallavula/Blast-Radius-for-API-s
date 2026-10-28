import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from blastradius.server import MAX_FILES, app  # noqa: E402

client = TestClient(app)

SPEC = """
openapi: 3.0.3
paths:
  /items/{id}:
    get:
      responses:
        '200':
          content:
            application/json:
              schema:
                type: object
                properties:
                  id: {type: string}
                  price: {type: number}
"""


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_root_redirects_to_docs():
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_demo_round_trip():
    demo = client.get("/demo").json()
    assert [c["name"] for c in demo["consumers"]] == [
        "billing-service",
        "shipping-service",
        "web-frontend",
    ]

    report = client.post("/analyze", json=demo).json()

    assert report["summary"]["will_break"] == [
        "billing-service",
        "shipping-service",
        "web-frontend",
    ]
    assert report["summary"]["breaking_changes"] == 11
    first = report["changes"][0]
    assert first["worst_impact"] == "will_break"
    assert {"consumer", "file", "line", "impact", "reason", "code"} <= set(first["impacts"][0])
    assert report["unmatched"][0]["url"] == "/{}/orders/{}/cancel"
    assert report["markdown"].startswith("## 💥 BlastRadius report")
    assert first["certain"] is True


def test_analyze_with_inline_consumer():
    head = SPEC.replace("price: {type: number}", "")
    body = {
        "base_spec": SPEC,
        "head_spec": head,
        "consumers": [
            {
                "name": "shop",
                "files": [
                    {
                        "path": "cart.ts",
                        "content": (
                            "const r = await fetch(`/items/${id}`);\n"
                            "const p = (await r.json()).price;"
                        ),
                    },
                    {"path": "notes.md", "content": "fetch('/items/1') in docs is ignored"},
                ],
            }
        ],
    }

    report = client.post("/analyze", json=body).json()

    assert report["summary"]["will_break"] == ["shop"]
    assert report["summary"]["call_sites"] == 1


def test_no_consumers_is_fine():
    report = client.post("/analyze", json={"base_spec": SPEC, "head_spec": SPEC}).json()

    assert report["changes"] == []
    assert report["summary"]["consumers"] == []


@pytest.mark.parametrize(
    "base, message",
    [
        ("paths: [unclosed", "not valid YAML"),
        ("openapi: 2.0\npaths: {}", "only OpenAPI 3.x"),
        ("a: &x [1]\nb: *x\nopenapi: 3.0.0\npaths: {}", "YAML aliases"),
        (
            "openapi: 3.0.0\npaths:\n  /a:\n    get:\n      responses:\n        '200':\n"
            "          $ref: '#/components/responses/Missing'",
            "points to nothing",
        ),
    ],
)
def test_bad_specs_are_422_with_a_readable_message(base, message):
    response = client.post("/analyze", json={"base_spec": base, "head_spec": SPEC})

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_too_many_files_is_rejected():
    files = [{"path": f"f{i}.py", "content": ""} for i in range(MAX_FILES)]
    consumers = [{"name": "a", "files": files}, {"name": "b", "files": files[:1]}]

    response = client.post(
        "/analyze", json={"base_spec": SPEC, "head_spec": SPEC, "consumers": consumers}
    )

    assert response.status_code == 422
    assert "Too many files" in response.json()["detail"]


def test_oversized_spec_is_rejected_by_validation():
    response = client.post("/analyze", json={"base_spec": "x" * 500_001, "head_spec": SPEC})

    assert response.status_code == 422


def test_cors_allows_browser_calls():
    response = client.options(
        "/analyze",
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in ("*", "https://example.com")


UNION = """
openapi: 3.0.3
paths:
  /pay:
    get:
      responses:
        '200':
          content:
            application/json:
              schema:
                oneOf:
                  - {type: object, properties: {kind: {type: string}, last4: {type: string}}}
                  - {type: object, properties: {kind: {type: string}, iban: {type: string}}}
"""


def test_union_changes_are_marked_uncertain():
    head = UNION.replace("last4: {type: string}", "last4: {type: integer}")
    consumers = [
        {
            "name": "web",
            "files": [
                {
                    "path": "pay.ts",
                    "content": "const r = await fetch(`${API}/pay`);\n"
                    "const p = (await r.json()).last4;",
                }
            ],
        }
    ]

    report = client.post(
        "/analyze", json={"base_spec": UNION, "head_spec": head, "consumers": consumers}
    ).json()

    [change] = report["changes"]
    assert change["certain"] is False
    assert change["worst_impact"] == "may_break"
    assert "uncertain" in report["markdown"]
