import axios from "axios";

export async function paymentLabel(id: number): Promise<string> {
  const { data } = await axios.get(`/api/payments/${id}`);
  return `${data.type} ${data.last4 ?? ""}`;
}

export async function settledCount(): Promise<number> {
  const { data } = await axios.get("/api/payments", { params: { status: "settled" } });
  return data.length;
}
