from __future__ import annotations

import json
import os
import urllib.request

_RESEND_URL = "https://api.resend.com/emails"


def send_email(subject: str, html: str, *, to: str, sender: str,
               api_key: str | None = None, opener=None) -> str:
    """Send an email via the Resend HTTP API and return the message id.

    api_key defaults to os.environ["RESEND_API_KEY"] — inject the process env with
    a secrets manager that sets RESEND_API_KEY, so the secret never touches disk or logs.
    opener defaults to urllib.request.urlopen and is injectable for offline tests.
    """
    if api_key is None:
        api_key = os.environ["RESEND_API_KEY"]
    body = json.dumps({"from": sender, "to": [to], "subject": subject, "html": html}).encode()
    req = urllib.request.Request(
        _RESEND_URL, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    opener = opener or urllib.request.urlopen
    with opener(req, timeout=20) as resp:
        return json.loads(resp.read())["id"]
