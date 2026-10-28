def low_stock(api, skus):
    out = []
    for sku in skus:
        item = api.get_item(sku=sku)
        if item.stock < 5:
            out.append(sku)
    return out
