"""Talks to the Orders service. Owned by the Shipping team."""

import httpx


class OrdersClient:
    def __init__(self, base_url: str):
        self.client = httpx.Client(base_url=base_url, timeout=5.0)

    def get_tracking(self, order_id: str) -> dict:
        resp = self.client.get(f"/orders/{order_id}/tracking")
        resp.raise_for_status()
        data = resp.json()
        return {"carrier": data["carrier"], "number": data["tracking_number"]}

    def get_items_to_pack(self, order_id: str) -> list[dict]:
        resp = self.client.get(f"/orders/{order_id}")
        resp.raise_for_status()
        order = resp.json()
        return [{"sku": i["sku"], "qty": i["quantity"]} for i in order["items"]]
