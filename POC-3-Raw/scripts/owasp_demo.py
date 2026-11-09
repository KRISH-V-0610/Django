"""
Topic E: runs the OWASP attacks against the /vulnerable and /secure endpoints and prints both answers.
(IDOR has its own script: idor_demo.py.)

    uv run python scripts/owasp_demo.py
"""
import uuid

from common import PASSWORD, heading, new_client, register_and_login, show

http = new_client()
suffix = uuid.uuid4().hex[:6]
user = f"victim-{suffix}"
headers = register_and_login(http, user)

heading("SQL injection: ' OR '1'='1")
attack = {"title": "' OR '1'='1"}
show("vulnerable", http.get("/api/owasp/sqli/vulnerable", params=attack))
show("secure", http.get("/api/owasp/sqli/secure", params=attack))

heading("SQL injection: UNION reads another table")
attack = {"title": "x' UNION SELECT id, username, id FROM auth_user --"}
show("vulnerable", http.get("/api/owasp/sqli/vulnerable", params=attack))
show("secure", http.get("/api/owasp/sqli/secure", params=attack))

heading("Authentication: which usernames exist?")
for version in ("vulnerable-login", "secure-login"):
    url = f"/api/owasp/auth/{version}"
    show(f"{version}: unknown user", http.post(url, json={"username": "nobody-" + suffix, "password": "x" * 10}))
    show(f"{version}: known user, bad password", http.post(url, json={"username": user, "password": "x" * 10}))

heading("Authentication: brute force (7 wrong guesses, then the right password)")
for version in ("vulnerable-login", "secure-login"):
    url = f"/api/owasp/auth/{version}"
    name = f"{version}-{suffix}"
    register_and_login(http, name)
    for _ in range(7):
        http.post(url, json={"username": name, "password": "wrong-wrong"})
    show(f"{version}: correct password afterwards", http.post(url, json={"username": name, "password": PASSWORD}))

heading("Authorization: a normal user asks for the admin user list")
show("vulnerable", http.get("/api/owasp/admin/users/vulnerable", headers=headers))
show("secure", http.get("/api/owasp/admin/users/secure", headers=headers))

heading("Security misconfiguration: leftover debug endpoint")
show("vulnerable", http.get("/api/owasp/misconfig/vulnerable"))
show("secure", http.get("/api/owasp/misconfig/secure"))

heading("Sensitive data exposure: my own profile")
show("vulnerable", http.get("/api/owasp/profile/vulnerable", headers=headers))
show("secure", http.get("/api/owasp/profile/secure", headers=headers))

heading("Error handling: a request that makes the server fail")
show("vulnerable", http.get("/api/owasp/errors/vulnerable", params={"doc_id": "abc"}), max_len=200)
show("secure", http.get("/api/owasp/errors/secure", params={"doc_id": "abc"}), max_len=200)

heading("Security logging")
print("Look at the terminal where uvicorn is running: every failed login, lock-out and")
print("forbidden call above wrote a line starting with 'security_event'.")
