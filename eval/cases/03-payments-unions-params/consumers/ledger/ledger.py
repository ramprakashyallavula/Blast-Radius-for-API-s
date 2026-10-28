import requests

PAY = "https://payments.internal.example.com"


def payment_source(payment_id: int) -> str:
    r = requests.get(f"{PAY}/payments/{payment_id}", timeout=5)
    p = r.json()
    if p["type"] == "bank":
        return p["bank_name"]
    return p["last4"]


def failed_payment_ids() -> list[int]:
    r = requests.get(f"{PAY}/payments", params={"status": "failed"}, timeout=5)
    return [x["id"] for x in r.json()]
