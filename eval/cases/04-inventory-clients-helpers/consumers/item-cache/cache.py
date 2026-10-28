import requests

BASE = "https://inventory.internal.example.com"


class ItemCache:
    def refresh(self, sku: str) -> None:
        resp = requests.get(f"{BASE}/items/{sku}", timeout=5)
        self.item = resp.json()

    def city(self) -> str:
        return self.item["warehouse"]["city"]
