"""Small helpers shared by the demo scripts. The server must be running (see README)."""
import os
import sys
from pathlib import Path

import httpx

# lets `uv run python scripts/xyz.py` import the project's own packages (core, ...)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
JAEGER_UI = os.environ.get("JAEGER_UI", "http://localhost:16686")
PASSWORD = "demo-password-12345"


def new_client():
    return httpx.Client(base_url=BASE_URL, timeout=15)


def register_and_login(http, username):
    """Create the user if needed, log in, return the Authorization header."""
    http.post("/api/auth/register", json={"username": username, "password": PASSWORD})  # 409 = exists
    response = http.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access']}"}


def heading(text):
    print(f"\n=== {text} ===")


def show(label, response, max_len=110):
    body = response.text.replace("\n", " ")
    if len(body) > max_len:
        body = body[:max_len] + "..."
    print(f"{label:<44} -> {response.status_code}  {body}")
