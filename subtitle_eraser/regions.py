from __future__ import annotations

from typing import Iterable


NormalizedRegion = tuple[float, float, float, float]
PixelBox = tuple[int, int, int, int]


def clamp_normalized_region(region: NormalizedRegion) -> NormalizedRegion:
    x1, y1, x2, y2 = region
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    return x1, y1, x2, y2


def region_to_box(region: NormalizedRegion, width: int, height: int) -> PixelBox:
    x1, y1, x2, y2 = clamp_normalized_region(region)
    left = int(round(x1 * width))
    top = int(round(y1 * height))
    right = int(round(x2 * width))
    bottom = int(round(y2 * height))
    return left, top, max(left + 1, right), max(top + 1, bottom)


def boxes_intersect(box_a: PixelBox, box_b: PixelBox) -> bool:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    return min(ax2, bx2) > max(ax1, bx1) and min(ay2, by2) > max(ay1, by1)


def any_region_intersects(box: PixelBox, regions: Iterable[NormalizedRegion], width: int, height: int) -> bool:
    return any(boxes_intersect(box, region_to_box(region, width, height)) for region in regions)
