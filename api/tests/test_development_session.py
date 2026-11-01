from datetime import timedelta

import pytest
from fastapi import HTTPException

import app.main as main
from app.models import DeviceCredential


class FakeSession:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        pass

    def commit(self):
        pass


def test_development_session_is_short_lived_bound_to_a_device_and_refreshable(monkeypatch):
    db = FakeSession()
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main, "SessionLocal", lambda: db)
    monkeypatch.setattr(main, "issue_refresh_secret", lambda: "one-time-test-secret")
    monkeypatch.setattr(main, "digest", lambda value: f"digest:{value}")
    captured = {}

    def issue(_db, _family_id, _member_id, _role, _actor, _name, device_id=None):
        captured["device_id"] = device_id
        from datetime import datetime, timezone
        return "access-token", datetime.now(timezone.utc) + timedelta(seconds=main.ACCESS_SECONDS)

    monkeypatch.setattr(main, "issue_access", issue)
    response = main.development_session()

    device = next(row for row in db.rows if isinstance(row, DeviceCredential))
    assert response["access_token"] == "access-token"
    assert response["refresh_token"] == "one-time-test-secret"
    assert device.id == captured["device_id"]
    assert device.refresh_hash == "digest:one-time-test-secret"
    assert device.expires_at > main.datetime.now(main.timezone.utc) + timedelta(days=main.REFRESH_DAYS - 1)


def test_development_session_is_hidden_outside_development(monkeypatch):
    monkeypatch.setattr(main.settings, "app_env", "production")
    with pytest.raises(HTTPException) as error:
        main.development_session()
    assert error.value.status_code == 404
