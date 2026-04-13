from __future__ import annotations

from subtitle_eraser.evaluation import (
    filter_residual_events,
    select_sample_clips,
    weighted_event_mass,
)
from subtitle_eraser.models import SubtitleEvent, VideoInfo


def _event(
    index: int,
    start_time: float,
    end_time: float,
    start_frame: int,
    end_frame: int,
    box: tuple[int, int, int, int],
    text: str = "字幕",
) -> SubtitleEvent:
    return SubtitleEvent(
        index=index,
        start_time=start_time,
        end_time=end_time,
        start_frame=start_frame,
        end_frame=end_frame,
        text=text,
        confidence=0.95,
        box=box,
        polygon=None,
    )


def test_weighted_event_mass_prefers_longer_larger_events() -> None:
    video = VideoInfo(fps=25.0, total_frames=250, width=1280, height=720, duration=10.0)
    small = _event(0, 1.0, 1.4, 25, 35, (100, 620, 260, 670), text="短")
    large = _event(1, 2.0, 3.0, 50, 75, (80, 580, 1180, 690), text="更长的字幕文本")
    assert weighted_event_mass([large], video) > weighted_event_mass([small], video)


def test_select_sample_clips_spreads_across_timeline() -> None:
    video = VideoInfo(fps=25.0, total_frames=2500, width=1280, height=720, duration=100.0)
    events = [
        _event(0, 2.0, 4.0, 50, 100, (100, 600, 800, 680)),
        _event(1, 28.0, 30.0, 700, 750, (100, 600, 820, 680)),
        _event(2, 55.0, 57.0, 1375, 1425, (100, 600, 840, 680)),
        _event(3, 88.0, 90.0, 2200, 2250, (100, 600, 860, 680)),
    ]
    clips = select_sample_clips(video, events, clip_duration=6.0, max_clips=3)
    assert len(clips) == 3
    assert clips[0][0] < clips[1][0] < clips[2][0]


def test_filter_residual_events_requires_time_and_box_overlap() -> None:
    references = [
        _event(0, 1.0, 2.0, 25, 50, (100, 600, 500, 680)),
    ]
    kept = _event(10, 1.4, 1.8, 35, 45, (120, 605, 480, 675))
    wrong_time = _event(11, 5.0, 6.0, 125, 150, (120, 605, 480, 675))
    wrong_box = _event(12, 1.4, 1.8, 35, 45, (900, 100, 1180, 180))
    filtered = filter_residual_events(references, [kept, wrong_time, wrong_box])
    assert filtered == [kept]
