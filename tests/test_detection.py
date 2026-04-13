from __future__ import annotations

from pathlib import Path

from subtitle_eraser.detection import expand_event_windows, filter_events_by_regions
from subtitle_eraser.models import SubtitleEvent


def make_event() -> SubtitleEvent:
    return SubtitleEvent(
        index=1,
        start_time=1.0,
        end_time=2.0,
        start_frame=25,
        end_frame=50,
        text="测试字幕",
        confidence=0.95,
        box=(100, 300, 340, 360),
        polygon=None,
    )


def test_expand_event_windows_extends_start_and_end() -> None:
    event = make_event()
    expanded = expand_event_windows([event], total_frames=200, fps=25.0, lead_frames=3, trail_frames=7)
    assert expanded[0].start_frame == 22
    assert expanded[0].end_frame == 57
    assert expanded[0].start_time == 22 / 25.0
    assert expanded[0].end_time == 57 / 25.0


def test_filter_events_by_regions_keeps_only_intersections() -> None:
    event = make_event()
    kept = filter_events_by_regions(
        [event],
        regions=[(0.08, 0.72, 0.4, 0.92)],
        width=960,
        height=416,
    )
    removed = filter_events_by_regions(
        [event],
        regions=[(0.7, 0.0, 0.95, 0.2)],
        width=960,
        height=416,
    )
    assert len(kept) == 1
    assert removed == []


def test_load_detection_debug_roundtrip(tmp_path: Path) -> None:
    from subtitle_eraser.detection import load_detection_debug, write_detection_debug
    from subtitle_eraser.models import DetectionResult, VideoInfo

    event = make_event()
    detection = DetectionResult(
        video_info=VideoInfo(fps=25.0, total_frames=200, width=960, height=416, duration=8.0),
        events=[event],
        anchors=[{"center_x": 0.5}],
        anchor_debug={"ok": True},
        requested_regions=[(0.1, 0.7, 0.9, 0.95)],
        mode="manual-fixed",
    )
    output_path = tmp_path / "detection.json"
    write_detection_debug(detection, output_path)

    loaded = load_detection_debug(output_path)

    assert loaded.video_info.width == 960
    assert loaded.mode == "manual-fixed"
    assert loaded.events[0].box == event.box
