import axios from "axios";

const API = "/api";

export async function displayName(id: string): Promise<string> {
  const { data: user } = await axios.get(`${API}/users/${id}`);
  return user.name;
}
