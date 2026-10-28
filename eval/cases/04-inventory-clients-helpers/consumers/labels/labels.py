import requests

BASE = "https://inventory.internal.example.com"
ROUTES = {"item": "/items/{sku}"}


def shelf_label(sku: str) -> str:
    url = ROUTES["item"].format(sku=sku)
    data = requests.get(BASE + url, timeout=5).json()
    return f"{data['name']} ({data['stock']} in stock)"
