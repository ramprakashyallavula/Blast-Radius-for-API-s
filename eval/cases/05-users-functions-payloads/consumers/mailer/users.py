import requests

USERS = "https://users.internal.example.com"


def email_of(user_id: str) -> str:
    return fetch_user(user_id)["email"]


def fetch_user(user_id: str) -> dict:
    r = requests.get(f"{USERS}/users/{user_id}", timeout=5)
    r.raise_for_status()
    return r.json()


def is_legacy(user_id: str) -> bool:
    return fetch_user(user_id).get("legacy_flag", False)
