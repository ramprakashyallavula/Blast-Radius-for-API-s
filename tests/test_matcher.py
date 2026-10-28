from pathlib import Path

import pytest

from blastradius.matcher import match_call_sites, match_operation
from blastradius.models import CallSite
from blastradius.scanner import scan_consumer
from blastradius.spec import Operation, Spec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

OPS = [
    Operation("GET", "/orders"),
    Operation("POST", "/orders"),
    Operation("GET", "/orders/{orderId}"),
    Operation("GET", "/orders/search"),
    Operation("GET", "/orders/{orderId}/tracking"),
]


def call(method, url):
    return CallSite("svc", "a.py", 1, method, url, "", "")


def matched(method, url, base=frozenset()):
    op = match_operation(call(method, url), OPS, base)
    return str(op) if op else None


@pytest.mark.parametrize(
    "method, url, expected",
    [
        ("GET", "/orders", "GET /orders"),
        ("POST", "/orders", "POST /orders"),
        ("GET", "/orders/{}", "GET /orders/{orderId}"),
        ("GET", "/orders/12345", "GET /orders/{orderId}"),  # literal id in the URL
        ("GET", "/orders/{}/tracking", "GET /orders/{orderId}/tracking"),
        # Base-URL prefixes are allowed.
        ("GET", "/{}/orders/{}", "GET /orders/{orderId}"),
        ("GET", "/api/v1/orders", "GET /orders"),
        ("GET", "/{}/v2.1/orders", "GET /orders"),
    ],
)
def test_matches(method, url, expected):
    assert matched(method, url) == expected


def test_fixed_words_beat_parameters():
    assert matched("GET", "/orders/search") == "GET /orders/search"


@pytest.mark.parametrize(
    "method, url",
    [
        ("DELETE", "/orders/{}"),  # method doesn't exist
        ("GET", "/customers/{}/orders"),  # "customers" isn't a base-URL word
        ("GET", "/orders/{}/cancel"),  # not in the API
        ("GET", "/{}/tracking"),  # a dynamic segment can't stand in for "orders"
        ("GET", "/products"),
    ],
)
def test_does_not_match(method, url):
    assert matched(method, url) is None


def test_base_segments_from_the_spec_are_allowed_as_prefix():
    assert matched("GET", "/internal/orders") is None
    assert matched("GET", "/internal/orders", {"internal"}) == "GET /orders"


def test_spec_reads_base_path_from_servers():
    spec = Spec(
        {
            "openapi": "3.0.3",
            "paths": {},
            "servers": [{"url": "https://x.com/v1"}, {"url": "/{env}/internal"}],
        }
    )

    assert spec.base_path_segments == {"v1", "internal"}


def test_example_consumers_match_the_v1_api():
    spec = Spec.load(EXAMPLES / "orders-service" / "openapi.v1.yaml")
    sites = [
        site
        for name in ("billing-service", "shipping-service", "web-frontend")
        for site in scan_consumer(EXAMPLES / "consumers" / name)
    ]

    result = match_call_sites(sites, spec.operations().values(), spec.base_path_segments)

    by_op = {key: [s.location for s in group] for key, group in result.by_operation.items()}
    assert by_op == {
        ("GET", "/orders/{}"): [
            "billing/invoices.py:11",
            "shipping/orders_client.py:17",
            "src/api/orders.ts:12",
        ],
        ("GET", "/orders"): ["billing/invoices.py:21"],
        ("GET", "/orders/{}/tracking"): ["shipping/orders_client.py:11"],
        ("POST", "/orders"): ["src/api/orders.ts:23"],
    }
    assert [s.url for s in result.unmatched] == ["/{}/orders/{}/cancel"]
    assert result.matched_count == 6
