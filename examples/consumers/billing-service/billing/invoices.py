"""Builds invoices from orders. Owned by the Billing team."""

import os

import requests

ORDERS_API = os.environ.get("ORDERS_API", "https://orders.internal.example.com/v1")


def build_invoice(order_id: str) -> dict:
    resp = requests.get(f"{ORDERS_API}/orders/{order_id}", timeout=5)
    resp.raise_for_status()
    order = resp.json()

    subtotal = round(order["total"] * 100) / 100
    discount = order.get("discount_code")
    return {"order_id": order["id"], "subtotal": subtotal, "discount_code": discount}


def list_paid_order_ids() -> list[str]:
    resp = requests.get(ORDERS_API + "/orders", params={"status": "paid"}, timeout=5)
    resp.raise_for_status()
    return [o["id"] for o in resp.json()]
