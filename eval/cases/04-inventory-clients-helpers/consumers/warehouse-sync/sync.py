BASE = "https://inventory.internal.example.com"


async def city_of(session, sku: str) -> str:
    async with session.get(f"{BASE}/items/{sku}") as resp:
        item = await resp.json()
    return item["warehouse"]["city"]
