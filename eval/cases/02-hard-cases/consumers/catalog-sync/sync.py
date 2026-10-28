import requests

API = "https://products.internal.example.com"


def fetch_product(product_id):
    resp = requests.get(f"{API}/products/{product_id}", timeout=5)
    return resp.json()


def sync_one(product_id, db):
    product = fetch_product(product_id)
    db.save(product["id"], product["legacy_sku"])
