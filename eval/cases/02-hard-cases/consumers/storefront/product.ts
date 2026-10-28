const API = "/api";

export async function productLabel(id: string): Promise<string> {
  const res = await fetch(`${API}/products/${id}`);
  const { name, legacy_sku } = await res.json();
  return `${name} (${legacy_sku.toUpperCase()})`;
}
