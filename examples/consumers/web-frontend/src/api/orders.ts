// Calls the Orders service from the browser. Owned by the Web team.
import axios from "axios";

const API = import.meta.env.VITE_ORDERS_API ?? "/api/v1";

export interface LineItem {
  sku: string;
  quantity: number;
}

export async function fetchOrder(orderId: string) {
  const res = await fetch(`${API}/orders/${orderId}`);
  if (!res.ok) throw new Error(`Failed to load order ${orderId}`);
  const order = await res.json();
  return {
    id: order.id,
    customer: order.customer_name,
    badge: statusBadge(order.status),
  };
}

export async function createOrder(customerName: string, items: LineItem[]) {
  const { data } = await axios.post(`${API}/orders`, {
    customer_name: customerName,
    items,
  });
  return data.id as string;
}

export async function cancelOrder(orderId: string) {
  await fetch(`${API}/orders/${orderId}/cancel`, { method: "POST" });
}

function statusBadge(status: string): string {
  switch (status) {
    case "pending":
      return "gray";
    case "paid":
      return "blue";
    case "shipped":
      return "green";
    case "cancelled":
      return "red";
    default:
      throw new Error(`Unknown order status: ${status}`);
  }
}
