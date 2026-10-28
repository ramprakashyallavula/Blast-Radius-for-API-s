from pathlib import Path

import pytest

from blastradius.scanner import scan_consumer, scan_text, url_template

CONSUMERS = Path(__file__).resolve().parents[1] / "examples" / "consumers"


# --- url_template ----------------------------------------------------------


@pytest.mark.parametrize(
    "expr, expected",
    [
        ('f"{ORDERS_API}/orders/{order_id}"', "/{}/orders/{}"),
        ('ORDERS_API + "/orders"', "/{}/orders"),
        ('BASE + "/orders/" + str(order_id) + "/items"', "/{}/orders/{}/items"),
        ("`${API}/orders/${orderId}`", "/{}/orders/{}"),
        ('"/orders/{}".format(order_id)', "/orders/{}"),
        ('"https://orders.example.com/v1/orders?status=paid"', "/v1/orders"),
        ("'/orders/' + id + '#top'", "/orders/{}"),
        ('f"/orders/{order_id}/tracking"', "/orders/{}/tracking"),
    ],
)
def test_url_template(expr, expected):
    assert url_template(expr) == expected


@pytest.mark.parametrize("expr", ["url", 'f"{base}{path}"', '"orders"', '"discount_code"', ""])
def test_url_template_rejects_non_urls(expr):
    assert url_template(expr) is None


# --- scan_text -------------------------------------------------------------


def test_finds_python_calls_with_method_line_and_context():
    code = """import requests

def load(order_id):
    resp = requests.get(f"{API}/orders/{order_id}", timeout=5)
    return resp.json()["total"]

def create(payload):
    return requests.post(API + "/orders", json=payload)
"""
    get, post = scan_text(code, "billing", "app.py")

    assert (get.method, get.url, get.line) == ("GET", "/{}/orders/{}", 4)
    assert get.snippet.startswith("resp = requests.get(")
    # Context covers the rest of the function but stops at the next `def`.
    assert '["total"]' in get.context
    assert "def create" not in get.context
    assert (post.method, post.url, post.line) == ("POST", "/{}/orders", 8)


def test_ignores_dict_get_and_other_non_http_calls():
    code = """
config.get("/orders/path")
order.get("discount_code")
os.environ.get("API", "https://x.com/v1")
cache.delete("/orders/1")
"""
    assert scan_text(code, "svc", "a.py") == []


def test_recognizes_client_objects_by_name():
    code = """
self.session.get("/a/1")
self.client.put(f"/b/{x}")
httpClient.patch("/c")
api.delete(`/d/${id}`)
"""
    sites = scan_text(code, "svc", "a.ts")

    assert [(s.method, s.url) for s in sites] == [
        ("GET", "/a/1"),
        ("PUT", "/b/{}"),
        ("PATCH", "/c"),
        ("DELETE", "/d/{}"),
    ]


def test_fetch_defaults_to_get_and_reads_method_option():
    code = """
await fetch(`${API}/orders`);
await fetch(`${API}/orders`, { method: "POST", body: JSON.stringify(o) });
await prefetch("/not/a/call");
"""
    sites = scan_text(code, "web", "a.ts")

    assert [(s.method, s.url) for s in sites] == [("GET", "/{}/orders"), ("POST", "/{}/orders")]


def test_multiline_calls_and_strings_containing_parens():
    code = """
const { data } = await axios.post(
  `${API}/orders`,
  { note: "smile :) (really)" },
);
"""
    (site,) = scan_text(code, "web", "a.ts")

    assert (site.method, site.url, site.line) == ("POST", "/{}/orders", 2)


# --- scan_consumer ---------------------------------------------------------


def test_scans_the_example_consumers():
    found = {
        name: [(s.file, s.line, s.method, s.url) for s in scan_consumer(CONSUMERS / name)]
        for name in ("billing-service", "shipping-service", "web-frontend")
    }

    assert found == {
        "billing-service": [
            ("billing/invoices.py", 11, "GET", "/{}/orders/{}"),
            ("billing/invoices.py", 21, "GET", "/{}/orders"),
        ],
        "shipping-service": [
            ("shipping/orders_client.py", 11, "GET", "/orders/{}/tracking"),
            ("shipping/orders_client.py", 17, "GET", "/orders/{}"),
        ],
        "web-frontend": [
            ("src/api/orders.ts", 12, "GET", "/{}/orders/{}"),
            ("src/api/orders.ts", 23, "POST", "/{}/orders"),
            ("src/api/orders.ts", 31, "POST", "/{}/orders/{}/cancel"),
        ],
    }


def test_skips_dependency_folders(tmp_path):
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "lib" / "x.js").write_text('axios.get("/vendor/thing")')
    (tmp_path / "app.js").write_text('axios.get("/mine")')

    assert [s.url for s in scan_consumer(tmp_path)] == ["/mine"]
