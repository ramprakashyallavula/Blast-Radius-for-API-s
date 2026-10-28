import ky from "ky";

export const itemSummary = async (sku: string) => {
  const item = await ky.get(`items/${sku}`).json<{ name: string; stock: number }>();
  return `${item.name} (${item.stock} left)`;
};
