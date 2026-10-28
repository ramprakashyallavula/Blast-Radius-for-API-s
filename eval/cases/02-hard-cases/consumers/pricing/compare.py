import requests

API = "https://products.internal.example.com"


def name_changed(product_id, cached_row):
    resp = requests.get(f"{API}/products/{product_id}", timeout=5)
    fresh = resp.json()
    # cached_row is our own database row; it still has its legacy_sku column.
    print("checking", cached_row.legacy_sku)
    return fresh["name"] != cached_row.name
