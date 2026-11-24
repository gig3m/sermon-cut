from sermon_cut.auto import status, watcher


def _streams():
    return [("v1", "Sunday Morning Class & Worship - August 16, 2026"),
            ("wed", "Wed Bible Class - August 12, 2026"),
            ("v2", "Sunday Morning Class & Worship - August 9, 2026")]


def test_scan_inserts_only_new_sunday_services():
    conn = status.connect(":memory:")
    new = watcher.scan_channel(enumerate_streams=_streams, conn=conn, now="t0")
    assert set(new) == {"v1", "v2"}                 # Wed class excluded
    assert status.get_run(conn, "v1").status == "discovered"
    assert status.get_run(conn, "wed") is None


def test_scan_is_idempotent():
    conn = status.connect(":memory:")
    watcher.scan_channel(enumerate_streams=_streams, conn=conn, now="t0")
    again = watcher.scan_channel(enumerate_streams=_streams, conn=conn, now="t1")
    assert again == []                              # nothing new second time
