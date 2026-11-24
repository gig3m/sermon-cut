from sermon_cut.timefmt import seconds_to_timestamp, clamp_nonnegative


def test_seconds_to_timestamp():
    assert seconds_to_timestamp(0) == "00:00:00"
    assert seconds_to_timestamp(65) == "00:01:05"
    assert seconds_to_timestamp(3661) == "01:01:01"
    assert seconds_to_timestamp(45.9) == "00:00:45"  # floors


def test_clamp_nonnegative():
    assert clamp_nonnegative(-3) == 0
    assert clamp_nonnegative(10) == 10
