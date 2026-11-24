import io
import json

import pytest

from sermon_cut.auto import notify


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def read(self): return json.dumps(self._p).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_send_email_posts_expected_payload_and_returns_id():
    captured = {}

    def fake_opener(req, timeout=0):
        captured["url"] = req.full_url
        captured["auth"] = req.headers["Authorization"]
        captured["body"] = json.loads(req.data)
        return _FakeResp({"id": "email_123"})

    mid = notify.send_email("Subj", "<b>hi</b>", to="k@x.co", sender="a@church.net",
                            api_key="re_test", opener=fake_opener)
    assert mid == "email_123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test"
    assert captured["body"] == {"from": "a@church.net", "to": ["k@x.co"],
                                "subject": "Subj", "html": "<b>hi</b>"}


def test_api_key_defaults_to_env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_env")
    seen = {}

    def fake_opener(req, timeout=0):
        seen["auth"] = req.headers["Authorization"]
        return _FakeResp({"id": "x"})

    notify.send_email("s", "h", to="t@x.co", sender="a@x.co", opener=fake_opener)
    assert seen["auth"] == "Bearer re_env"


def test_missing_env_key_raises(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(KeyError):
        notify.send_email("s", "h", to="t@x.co", sender="a@x.co", opener=lambda *a, **k: None)
