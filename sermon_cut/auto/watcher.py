from __future__ import annotations

from . import status
from ..livestream_thumbs import is_sunday_service


def scan_channel(*, enumerate_streams, conn, now) -> list[str]:
    new = []
    for vid, title in enumerate_streams():
        if not is_sunday_service(title):
            continue
        if status.get_run(conn, vid) is not None:
            continue
        status.upsert_run(conn, vid, now=now, status="discovered")
        new.append(vid)
    return new
