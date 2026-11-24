from sermon_cut.config import ShortsConfig
from sermon_cut.shorts_track import (
    ema, deadzone_follow, velocity_cap, smooth_camera, crop_x_from_center,
    median_column_center)

def test_deadzone_holds_small_moves():
    out = deadzone_follow([100, 110, 90, 105], deadzone=50)
    assert out == [100, 100, 100, 100]  # all within +/-50 of start

def test_deadzone_eases_on_big_move():
    out = deadzone_follow([100, 300], deadzone=50)
    assert out[1] == 250  # keeps subject at deadzone edge

def test_velocity_cap_limits_step():
    assert velocity_cap([0, 100], max_vel=10)[1] == 10

def test_smooth_camera_reduces_jitter():
    noisy = [100 + (i % 2) * 40 for i in range(20)]  # oscillates 100/140
    sm = smooth_camera(noisy, ShortsConfig())
    assert max(sm) - min(sm) < 40  # jitter compressed

def test_crop_x_clamps_to_bounds():
    assert crop_x_from_center([0, 5000], crop_w=430, xmax=1490) == [0, 1490]

def test_median_column_center_is_robust_to_far_noise():
    # bulk mass at columns 10-12, a tiny far cluster at 100
    cols = [0]*10 + [255, 255, 255] + [0]*87 + [5]
    c = median_column_center(cols, rx0=0, scale=1.0)
    assert 9 <= c <= 13   # median sits in the bulk, not pulled to 100
