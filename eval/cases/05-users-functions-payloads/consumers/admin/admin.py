import requests

USERS = "https://users.internal.example.com"


def create_user(payload: dict) -> str:
    return requests.post(f"{USERS}/users", json=payload, timeout=5).json()["id"]


def invite(email: str) -> str:
    return create_user({"email": email, "name": email.split("@")[0]})
