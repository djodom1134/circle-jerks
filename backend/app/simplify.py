"""Ramer–Douglas–Peucker polyline simplification.

Reduces a track's point count while preserving its shape within a tolerance,
so the track-history endpoint can ship far fewer points without visibly
changing the path. Operates on plain (x, y) tuples — the caller passes
(lat, lon) and interprets epsilon in degrees.
"""

from __future__ import annotations


def _perpendicular_distance(
    pt: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> float:
    (x, y), (x1, y1), (x2, y2) = pt, line_start, line_end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    # Distance from point to the infinite line through (x1,y1)-(x2,y2).
    num = abs(dy * x - dx * y + x2 * y1 - y2 * x1)
    den = (dx * dx + dy * dy) ** 0.5
    return num / den


def rdp_keep_mask(points: list[tuple[float, float]], epsilon: float) -> list[bool]:
    """Return a keep/drop mask (one bool per point). Endpoints always kept."""
    n = len(points)
    if n <= 2:
        return [True] * n
    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True
    # Iterative RDP over index ranges to avoid recursion-depth limits on long
    # tracks.
    stack: list[tuple[int, int]] = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        dmax = -1.0
        index = start
        for i in range(start + 1, end):
            d = _perpendicular_distance(points[i], points[start], points[end])
            if d > dmax:
                dmax = d
                index = i
        if dmax > epsilon:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return keep
