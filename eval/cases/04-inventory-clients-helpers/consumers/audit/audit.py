import requests

BASE = "https://inventory.internal.example.com"


def name_drifted(sku: str, snapshot) -> bool:
    resp = requests.get(f"{BASE}/items/{sku}", timeout=5)
    current = resp.json()
    # snapshot is our own database row, which still has a stock column.
    return current["name"] != snapshot.name or snapshot.stock < 0
