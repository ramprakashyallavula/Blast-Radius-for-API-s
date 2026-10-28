async def stock_level(client, sku: str) -> int:
    resp = await client.get(f"/items/{sku}")
    return resp.json()["stock"]
