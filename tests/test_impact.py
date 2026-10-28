from pathlib import Path

import pytest

from blastradius.impact import analyze, assess, reads_field, sends_field
from blastradius.models import CallSite, Change, Impact
from blastradius.models import ChangeKind as K
from blastradius.models import Severity as S
from blastradius.scanner import scan_consumer
from blastradius.spec import Spec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# --- reading vs. sending ---------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        'order["total"]',
        "order['total']",
        'order.get("total")',
        "order.total",
        "data.total.toFixed(2)",
    ],
)
def test_reads_field(code):
    assert reads_field(code, "total")


@pytest.mark.parametrize("code", ['{"total": 5}', "subtotal = 1", "order.total_cents", "totals"])
def test_does_not_read_field(code):
    assert not reads_field(code, "total")


@pytest.mark.parametrize(
    "code",
    [
        'json={"currency": "USD"}',
        "{ currency: 'USD' }",
        "{ items, currency }",
        'create(currency="USD")',
        "`/orders?currency=USD`",
    ],
)
def test_sends_field(code):
    assert sends_field(code, "currency")


@pytest.mark.parametrize("code", ['order["currency"]', "if currency == 'USD':", "currency_code: x"])
def test_does_not_send_field(code):
    assert not sends_field(code, "currency")


# --- rules -----------------------------------------------------------------


def change(kind, field=None, before=None):
    return Change(kind, S.BREAKING, "GET", "/x", "msg", field, before)


def site(context):
    return CallSite("svc", "a.py", 1, "GET", "/x", context.splitlines()[0], context)


@pytest.mark.parametrize(
    "kind, field, context, expected",
    [
        (K.ENDPOINT_REMOVED, None, "requests.get(url)", Impact.WILL_BREAK),
        # Response field removed: only readers break.
        (
            K.RESPONSE_FIELD_REMOVED,
            "[].discount",
            'r = get()\nx = r.json()[0]["discount"]',
            Impact.WILL_BREAK,
        ),
        (K.RESPONSE_FIELD_REMOVED, "discount", 'r = get()\nx = r.json()["id"]', Impact.UNAFFECTED),
        (K.RESPONSE_FIELD_REMOVED, "discount", "r = get()\nreturn r.json()", Impact.MAY_BREAK),
        (K.RESPONSE_ENUM_VALUE_ADDED, "status", "switch (order.status) {", Impact.MAY_BREAK),
        # New required request field: only code that doesn't send it breaks.
        (
            K.REQUEST_FIELD_ADDED_REQUIRED,
            "currency",
            "post(url, json={'items': []})",
            Impact.WILL_BREAK,
        ),
        (
            K.REQUEST_FIELD_ADDED_REQUIRED,
            "currency",
            "post(url, json={'currency': 'EUR'})",
            Impact.UNAFFECTED,
        ),
        (K.PARAM_ADDED_REQUIRED, "tenant", "get(url, params={'tenant': t})", Impact.UNAFFECTED),
        # Tightened request field: only code that sends it might break.
        (K.REQUEST_FIELD_TYPE_CHANGED, "qty", "post(url, json={'qty': 2})", Impact.MAY_BREAK),
        (K.REQUEST_FIELD_TYPE_CHANGED, "qty", "post(url, json={'sku': 'a'})", Impact.UNAFFECTED),
        (K.REQUEST_BODY_BECAME_REQUIRED, None, "post(url)", Impact.MAY_BREAK),
        (K.REQUEST_BODY_BECAME_REQUIRED, None, "post(url, json=body)", Impact.UNAFFECTED),
        (K.RESPONSE_STATUS_REMOVED, None, "if r.status_code == 201:", Impact.MAY_BREAK),
    ],
)
def test_assess(kind, field, context, expected):
    c = change(kind, field, before="201" if kind is K.RESPONSE_STATUS_REMOVED else None)

    assert assess(c, site(context)).impact is expected


def test_reasons_name_the_field():
    result = assess(change(K.RESPONSE_FIELD_REMOVED, "items[].sku"), site('x = item["sku"]'))

    assert result.reason == "reads `sku`, which was removed"


# --- the full demo ---------------------------------------------------------


@pytest.fixture(scope="module")
def demo():
    sites = [
        s
        for name in ("billing-service", "shipping-service", "web-frontend")
        for s in scan_consumer(EXAMPLES / "consumers" / name)
    ]
    return analyze(
        Spec.load(EXAMPLES / "orders-service" / "openapi.v1.yaml"),
        Spec.load(EXAMPLES / "orders-service" / "openapi.v2.yaml"),
        sites,
    )


def outcomes(analysis, impact):
    return {
        (ci.change.endpoint, ci.change.field, i.site.consumer, i.site.location)
        for ci in analysis.impacts
        for i in ci.impacts
        if i.impact is impact
    }


def test_demo_finds_exactly_the_real_breakages(demo):
    assert outcomes(demo, Impact.WILL_BREAK) == {
        ("GET /orders/{orderId}", "total", "billing-service", "billing/invoices.py:11"),
        ("GET /orders/{orderId}", "discount_code", "billing-service", "billing/invoices.py:11"),
        (
            "GET /orders/{orderId}/tracking",
            None,
            "shipping-service",
            "shipping/orders_client.py:11",
        ),
        ("GET /orders/{orderId}", "customer_name", "web-frontend", "src/api/orders.ts:12"),
        ("POST /orders", "currency", "web-frontend", "src/api/orders.ts:23"),
    }
    assert outcomes(demo, Impact.MAY_BREAK) == {
        ("GET /orders/{orderId}", "status", "web-frontend", "src/api/orders.ts:12"),
    }


def test_demo_summary(demo):
    assert demo.consumers == ["billing-service", "shipping-service", "web-frontend"]
    assert demo.consumers_with(Impact.WILL_BREAK) == demo.consumers
    assert demo.consumers_with(Impact.MAY_BREAK) == ["web-frontend"]
    assert len(demo.breaking) == 11
    assert [s.url for s in demo.unmatched] == ["/{}/orders/{}/cancel"]


def test_breaking_changes_nobody_uses_are_cleared(demo):
    # The list endpoint lost fields, but billing only reads `id` from it.
    list_changes = [ci for ci in demo.impacts if ci.change.endpoint == "GET /orders"]

    assert list_changes
    assert all(ci.worst is Impact.UNAFFECTED for ci in list_changes)
