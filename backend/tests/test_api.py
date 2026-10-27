"""API regression checks using synthetic users and a temporary SQLite database.
Run from backend/: uv run --with httpx python -m unittest discover -s tests -v
No external model calls are made.
"""
import os
import secrets
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_tmp.name) / "test.db")
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from fastapi.testclient import TestClient
from main import app, Base, engine


class AccountingAPI(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.client = TestClient(app)
        self.alice = self.user("alice@example.com")
        self.bob = self.user("bob@example.com")

    def tearDown(self):
        self.client.close()

    def user(self, email):
        payload = {"email": email, "password": secrets.token_urlsafe(24) + "Aa1!"}
        self.assertEqual(self.client.post("/api/auth/register", json=payload).status_code, 201)
        result = self.client.post("/api/auth/login", json=payload)
        self.assertEqual(result.status_code, 200)
        return {"Authorization": "Bearer " + result.json()["access_token"]}

    def record(self, **overrides):
        payload = dict(amount="35.00", type="支出", category="餐饮", record_date="2026-10-27", note="Synthetic lunch")
        payload.update(overrides)
        return self.client.post("/api/records", json=payload, headers=self.alice)

    def test_authentication_required(self):
        self.assertIn(self.client.get("/api/records").status_code, (401, 403))
        self.assertEqual(self.client.get("/api/auth/me", headers=self.alice).json()["email"], "alice@example.com")

    def test_crud_and_statistics(self):
        result = self.record()
        self.assertEqual(result.status_code, 201)
        record_id = result.json()["id"]
        self.assertEqual(self.record(amount="1000", type="收入", category="其他").status_code, 201)
        stats = self.client.get("/api/statistics", headers=self.alice).json()
        self.assertEqual(Decimal(str(stats["balance"])), Decimal("965"))
        self.assertEqual(stats["record_count"], 2)
        updated = self.client.put(f"/api/records/{record_id}", json={"amount": "42.50"}, headers=self.alice)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(Decimal(updated.json()["amount"]), Decimal("42.50"))
        self.assertEqual(self.client.delete(f"/api/records/{record_id}", headers=self.alice).status_code, 200)
        self.assertEqual(self.client.get("/api/records", headers=self.alice).json()["total"], 1)

    def test_user_isolation(self):
        record_id = self.record().json()["id"]
        self.assertEqual(self.client.get("/api/records", headers=self.bob).json()["total"], 0)
        self.assertEqual(self.client.put(f"/api/records/{record_id}", json={"amount": "99"}, headers=self.bob).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/records/{record_id}", headers=self.bob).status_code, 404)

    def test_invalid_amount_and_category(self):
        for invalid in ({"amount": "0"}, {"amount": "-1"}, {"amount": "1.001"}, {"category": "invalid"}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.record(**invalid).status_code, 422)

    def test_filter_and_pagination(self):
        self.record()
        self.record(category="交通", amount="20")
        result = self.client.get("/api/records?category=交通&page=1&page_size=1", headers=self.alice).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["category"], "交通")
        self.assertEqual(self.client.get("/api/records?page=0", headers=self.alice).status_code, 422)


if __name__ == "__main__":
    unittest.main()
