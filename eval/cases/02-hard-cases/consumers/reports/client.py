import requests

API = "https://products.internal.example.com"


class ProductsClient:
    def __init__(self):
        self.session = requests.Session()

    def _url(self, *parts):
        return "/".join([API, *parts])

    def legacy_sku(self, product_id):
        resp = self.session.get(self._url("products", product_id), timeout=5)
        return resp.json()["legacy_sku"]
