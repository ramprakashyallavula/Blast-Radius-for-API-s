const API = "/api";

export async function signup(email: string, name: string): Promise<boolean> {
  const body = { email, name, locale: navigator.language };
  const res = await fetch(`${API}/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.ok;
}
