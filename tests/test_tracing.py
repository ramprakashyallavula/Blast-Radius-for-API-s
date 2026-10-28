"""Regression tests for the scanner and impact analysis following data through code.

Each case was either missed or overclaimed ("unaffected" without being able to
trace the response) before the scanner/impact rework.
"""

import textwrap

import pytest

from blastradius.impact import assess, field_use, response_names
from blastradius.matcher import match_call_sites
from blastradius.models import Change, Impact
from blastradius.models import ChangeKind as K
from blastradius.models import Severity as S
from blastradius.scanner import scan_text
from blastradius.spec import Operation


def scan(code, file="app.py"):
    return scan_text(textwrap.dedent(code), "svc", file)


def removed(field):
    return Change(K.RESPONSE_FIELD_REMOVED, S.BREAKING, "GET", "/orders/{id}", "m", field)


def required(field):
    return Change(K.REQUEST_FIELD_ADDED_REQUIRED, S.BREAKING, "POST", "/orders", "m", field)


# --- finding calls -----------------------------------------------------------


def test_calls_in_comments_docstrings_and_strings_are_ignored():
    sites = scan(
        '''
        """Example: requests.get("/orders/1")"""
        # requests.get("/orders/2")
        message = "call requests.get('/orders/3') to load it"
        requests.get("/orders/4")
        '''
    )

    assert [s.url for s in sites] == ["/orders/4"]


def test_js_comments_are_ignored():
    sites = scan(
        """
        // await fetch(`/orders/${id}`);
        /* axios.get("/orders") */
        await fetch(`/orders/${id}/items`);
        """,
        file="a.ts",
    )

    assert [s.url for s in sites] == ["/orders/{}/items"]


def test_url_in_a_variable():
    sites = scan(
        """
        ORDERS = BASE + "/orders"

        def load(order_id):
            url = f"{API}/orders/{order_id}"
            return requests.get(url).json()

        def all_orders():
            return requests.get(ORDERS).json()
        """
    )

    assert [s.url for s in sites] == ["/{}/orders/{}", "/{}/orders"]


@pytest.mark.parametrize(
    "call, url",
    [
        ('self.session.get(self._url("products", product_id))', "/products/{}"),
        ('requests.get(urljoin(BASE, "orders/"))', "/{}/orders"),
        ('requests.get(build_path("v1", "orders", oid, "items"))', "/v1/orders/{}/items"),
    ],
)
def test_url_built_by_a_helper(call, url):
    (site,) = scan(call)

    assert site.url == url


def test_arrow_functions_and_methods_bound_the_context():
    sites = scan(
        """
        export const loadOrder = async (id: string) => {
          const res = await fetch(`/orders/${id}`);
          return (await res.json()).total;
        };

        export const loadItems = async (id: string) => {
          const res = await fetch(`/orders/${id}/items`);
          return (await res.json()).discount;
        };
        """,
        file="a.ts",
    )

    first, second = sites
    assert first.function == "loadOrder" and second.function == "loadItems"
    assert "discount" not in first.context


def test_generated_client_calls_match_by_operation_id():
    (site,) = [s for s in scan("order = orders_api.get_order(order_id=7)\nclient.close()")]
    operations = [Operation("GET", "/orders/{id}", operation_id="getOrder")]

    result = match_call_sites([site], operations)

    assert site.url == "op:get_order"
    assert list(result.by_operation) == [("GET", "/orders/{}")]


def test_unmatched_generated_client_calls_are_not_reported_as_unknown_endpoints():
    sites = scan("api.configure(debug=True)")

    assert match_call_sites(sites, []).unmatched == []


# --- following the response --------------------------------------------------


def test_response_names_follow_assignments_loops_and_callbacks():
    code = textwrap.dedent(
        """
        resp = requests.get(url)
        order = resp.json()
        for item in order["items"]:
            pass
        """
    ).strip()

    assert response_names(code) == {"resp", "order", "item"}
    assert response_names(
        "const { data } = await axios.get(url);\ndata.items.map((it) => it.sku)"
    ) == {"data", "it"}


@pytest.mark.parametrize(
    "code, impact",
    [
        # Destructuring the response.
        (
            "const res = await fetch('/products/1');\n"
            "const { name, legacy_sku } = await res.json();",
            Impact.WILL_BREAK,
        ),
        # Reading the same name from an unrelated object.
        (
            "r = requests.get('/products/1')\nprint(cached_row.legacy_sku, r.json()['id'])",
            Impact.MAY_BREAK,
        ),
        # Passing the response on.
        ("r = requests.get('/products/1')\nsave(r.json())", Impact.MAY_BREAK),
        ("r = requests.get('/products/1')\nself.cache = r", Impact.MAY_BREAK),
        # Using it fully in place, without the field: safe.
        ("r = requests.get('/products/1')\nprint(r.json()['id'])", Impact.UNAFFECTED),
        # A comment mentioning the field doesn't count as a read.
        (
            "r = requests.get('/products/1')\n"
            "# r.json()['legacy_sku'] used to be here\n"
            "print(r.json()['id'])",
            Impact.UNAFFECTED,
        ),
    ],
)
def test_field_reads_must_be_on_the_response(code, impact):
    (site,) = scan(code, file="a.ts" if "const" in code else "a.py")

    assert assess(removed("legacy_sku"), site).impact is impact


def test_nested_fields_need_their_parent():
    direct = scan('r = requests.get("/products/1")\nname = r.json()["customer"]["name"]')[0]
    unrelated = scan('r = requests.get("/products/1")\nprint(r.json()["id"], user.name)')[0]

    assert assess(removed("customer.name"), direct).impact is Impact.WILL_BREAK
    assert assess(removed("customer.name"), unrelated).impact is Impact.MAY_BREAK


def test_array_fields_through_js_callbacks():
    (site,) = scan(
        """
        const res = await fetch(`/orders/${id}`);
        const order = await res.json();
        return order.items.map((item) => item.sku);
        """,
        file="a.ts",
    )

    assert field_use(site.context, "items[].sku", response_names(site.context)) == "direct"


# --- following the response into the function's callers -------------------


def test_field_read_in_the_function_that_uses_the_result():
    sites = scan(
        """
        def fetch_product(pid):
            resp = requests.get(f"{API}/products/{pid}")
            return resp.json()

        def sync_one(pid, db):
            product = fetch_product(pid)
            db.save(product["legacy_sku"])
        """
    )

    result = assess(removed("legacy_sku"), sites[0])

    assert result.impact is Impact.WILL_BREAK
    assert "fetch_product()" in result.reason


def test_returned_response_with_callers_that_dont_read_the_field_is_unaffected():
    (site,) = scan(
        """
        def fetch_product(pid):
            return requests.get(f"{API}/products/{pid}").json()

        def name_of(pid):
            product = fetch_product(pid)
            return product["name"]
        """
    )

    assert assess(removed("legacy_sku"), site).impact is Impact.UNAFFECTED


def test_returned_response_with_no_callers_in_the_file_is_may_break():
    (site,) = scan(
        """
        def fetch_product(pid):
            resp = requests.get(f"{API}/products/{pid}")
            return resp.json()
        """
    )

    assert assess(removed("legacy_sku"), site).impact is Impact.MAY_BREAK


# --- request payloads ------------------------------------------------------


def test_payload_built_before_the_call():
    with_field, without_field = scan(
        """
        def create(items):
            payload = {"items": items}
            payload["currency"] = "USD"
            return requests.post(f"{API}/orders", json=payload)

        def create_old(items):
            payload = {"items": items}
            return requests.post(f"{API}/orders", json=payload)
        """
    )

    assert assess(required("currency"), with_field).impact is Impact.UNAFFECTED
    assert assess(required("currency"), without_field).impact is Impact.WILL_BREAK


def test_payload_from_a_parameter_cant_be_checked():
    (site,) = scan(
        """
        def create(payload):
            return requests.post(f"{API}/orders", json=payload)
        """
    )

    result = assess(required("currency"), site)

    assert result.impact is Impact.MAY_BREAK
    assert "built elsewhere" in result.reason
