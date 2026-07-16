from app.simplify import rdp_keep_mask


def test_collinear_points_reduce_to_endpoints():
    pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)]
    assert rdp_keep_mask(pts, epsilon=0.01) == [True, False, False, False, True]


def test_point_off_line_is_kept():
    pts = [(0.0, 0.0), (1.0, 5.0), (2.0, 0.0)]
    assert rdp_keep_mask(pts, epsilon=0.5) == [True, True, True]


def test_short_inputs_all_kept():
    assert rdp_keep_mask([], 0.1) == []
    assert rdp_keep_mask([(0.0, 0.0)], 0.1) == [True]
    assert rdp_keep_mask([(0.0, 0.0), (1.0, 1.0)], 0.1) == [True, True]


def test_endpoints_always_kept_even_below_epsilon():
    pts = [(0.0, 0.0), (0.5, 0.0001), (1.0, 0.0)]
    mask = rdp_keep_mask(pts, epsilon=0.01)
    assert mask[0] is True and mask[-1] is True
    assert mask[1] is False
