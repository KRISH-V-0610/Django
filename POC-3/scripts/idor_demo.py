"""
Topic D: the IDOR attack, first against the vulnerable endpoints, then against the secure ones.

    uv run python scripts/idor_demo.py
"""
import uuid

from common import heading, new_client, register_and_login, show

http = new_client()
suffix = uuid.uuid4().hex[:6]
alice = register_and_login(http, f"alice-{suffix}")
bob = register_and_login(http, f"bob-{suffix}")

heading("Setup: alice creates a private document")
created = http.post("/api/documents", headers=alice,
                    json={"title": "Alice salary", "content": "secret numbers"})
show("alice creates document", created)
doc_id = created.json()["id"]

heading("Attack on the VULNERABLE endpoints (bob changes the id in the URL)")
show("bob reads alice's document", http.get(f"/api/vulnerable/documents/{doc_id}", headers=bob))
show("bob overwrites it", http.put(f"/api/vulnerable/documents/{doc_id}", headers=bob,
                                   json={"title": "hacked by bob", "content": "pwned"}))
show("alice reads her document again", http.get(f"/api/documents/{doc_id}", headers=alice))

heading("Same attack on the SECURE endpoints")
doc2 = http.post("/api/documents", headers=alice, json={"title": "Alice diary", "content": "private"})
doc2_id = doc2.json()["id"]
show("bob reads alice's document", http.get(f"/api/documents/{doc2_id}", headers=bob))
show("bob overwrites it", http.put(f"/api/documents/{doc2_id}", headers=bob,
                                   json={"title": "hacked by bob", "content": "pwned"}))
show("bob deletes it", http.delete(f"/api/documents/{doc2_id}", headers=bob))
show("bob asks for an id that does not exist", http.get("/api/documents/99999999", headers=bob))
show("alice still has her document", http.get(f"/api/documents/{doc2_id}", headers=alice))

heading("Result")
print("Vulnerable: bob could read and change alice's data (200).")
print("Secure: bob only gets 404, identical to a missing id, and the attempt is in the security log.")
