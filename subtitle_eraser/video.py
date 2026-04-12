from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from tqdm import tqdm

from subtitle_eraser.detection import detect_subtitles, write_detection_debug
from subtitle_eraser.inpaint import HybridTemporalInpainter, InpaintConfig
from subtitle_eraser.masking import build_frame_mask
from subtitle_eraser.models import DetectionResult, ProcessingSegment
from subtitle_eraser.progress import ProgressCallback
from subtitle_eraser.regions import NormalizedRegion

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineConfig:
    subtitle_ocr_project: str | None = None
    sample_interval: float = 0.25
    language: str = "ch"
    mode: str = "auto"
    position_mode: str = "bottom"
    roi_bottom_ratio: float = 0.34
    requested_regions: list[NormalizedRegion] | None = None
    mask_dilate_x: int = 14
    mask_dilate_y: int = 10
    segment_gap_frames: int = 3
    context_frames: int = 12
    event_lead_frames: int = 2
    event_trail_frames: int = 8
    debug_dir: str | None = None


class FFmpegPipeWriter:
    def __init__(self, output_path: str, width: int, height: int, fps: float):
        self.output_path = output_path
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{width}x{height}",
                "-r",
                f"{fps:.6f}",
                "-i",
                "-",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                output_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def write(self, frame: np.ndarray) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(frame.tobytes())

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait()
        if self.process.returncode != 0:
            raise RuntimeError("ffmpeg video writer failed")


class FrameReader:
    def __init__(self, video_path: str):
        self.video_path = video_path

    def read_range(self, start_frame: int, end_frame: int) -> list[np.ndarray]:
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {self.video_path}")
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frames: list[np.ndarray] = []
        current = start_frame
        while current <= end_frame:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
            current += 1
        cap.release()
        return frames


def _merge_segments(detection: DetectionResult, config: PipelineConfig) -> list[ProcessingSegment]:
    events = sorted(detection.events, key=lambda item: (item.start_frame, item.end_frame))
    if not events:
        return []

    segments: list[ProcessingSegment] = []
    current_events = [events[0]]
    current_end = events[0].end_frame

    for event in events[1:]:
        if event.start_frame <= current_end + config.segment_gap_frames:
            current_events.append(event)
            current_end = max(current_end, event.end_frame)
            continue

        segments.append(_finalize_segment(current_events, detection.video_info.total_frames, config.context_frames))
        current_events = [event]
        current_end = event.end_frame

    segments.append(_finalize_segment(current_events, detection.video_info.total_frames, config.context_frames))
    return segments


def _finalize_segment(events, total_frames: int, context_frames: int) -> ProcessingSegment:
    start_frame = min(event.start_frame for event in events)
    end_frame = max(event.end_frame for event in events)
    context_start = max(0, start_frame - context_frames)
    context_end = min(total_frames - 1, end_frame + context_frames)
    frame_events: dict[int, list] = {}
    for event in events:
        for frame_idx in range(event.start_frame, event.end_frame + 1):
            frame_events.setdefault(frame_idx, []).append(event)
    return ProcessingSegment(
        start_frame=start_frame,
        end_frame=end_frame,
        context_start=context_start,
        context_end=context_end,
        frame_events=frame_events,
    )


def _merge_audio(video_only_path: str, source_path: str, output_path: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_only_path,
            "-i",
            source_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-shortest",
            output_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notify(progress_callback: ProgressCallback | None, stage: str, progress: int, message: str) -> None:
    if progress_callback is not None:
        progress_callback(stage, max(0, min(100, progress)), message)


def erase_subtitles(
    input_path: str,
    output_path: str,
    config: PipelineConfig,
    progress_callback: ProgressCallback | None = None,
) -> DetectionResult:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    debug_dir = Path(config.debug_dir) if config.debug_dir else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    logger.info("detecting subtitles")
    _notify(progress_callback, "detecting", 2, "开始分析字幕")
    detection = detect_subtitles(
        video_path=input_path,
        subtitle_ocr_project=config.subtitle_ocr_project,
        sample_interval=config.sample_interval,
        language=config.language,
        mode=config.mode,
        position_mode=config.position_mode,
        roi_bottom_ratio=config.roi_bottom_ratio,
        requested_regions=config.requested_regions,
        event_lead_frames=config.event_lead_frames,
        event_trail_frames=config.event_trail_frames,
        progress_callback=progress_callback,
    )
    _notify(progress_callback, "detecting", 24, f"检测完成，共 {len(detection.events)} 段字幕")

    if debug_dir:
        write_detection_debug(detection, debug_dir / "detection.json")

    video_info = detection.video_info
    segments = _merge_segments(detection, config)
    frame_reader = FrameReader(input_path)
    inpainter = HybridTemporalInpainter(
        InpaintConfig(
            context_margin=80,
        )
    )

    temp_video = output.with_suffix(".video_only.mp4")
    writer = FFmpegPipeWriter(str(temp_video), video_info.width, video_info.height, video_info.fps)
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {input_path}")

    segment_iter = iter(segments)
    current_segment = next(segment_iter, None)
    current_frame = 0
    last_progress_emit = monotonic()

    progress = tqdm(total=video_info.total_frames, unit="frame", desc="Erasing subtitles")
    try:
        while current_frame < video_info.total_frames:
            if current_segment is None or current_frame < current_segment.start_frame:
                ok, frame = cap.read()
                if not ok:
                    break
                writer.write(frame)
                current_frame += 1
                progress.update(1)
                if monotonic() - last_progress_emit >= 0.4:
                    percent = 25 + int(68 * (current_frame / max(1, video_info.total_frames)))
                    _notify(progress_callback, "erasing", percent, "处理视频帧")
                    last_progress_emit = monotonic()
                continue

            context_frames = frame_reader.read_range(current_segment.context_start, current_segment.context_end)
            masks = []
            target_local_indices = []
            for offset, absolute_frame in enumerate(range(current_segment.context_start, current_segment.context_end + 1)):
                frame = context_frames[offset]
                events = current_segment.frame_events.get(absolute_frame, [])
                mask = build_frame_mask(
                    frame,
                    events,
                    pad_x=config.mask_dilate_x,
                    pad_y=config.mask_dilate_y,
                    requested_regions=detection.requested_regions,
                    mode=detection.mode,
                )
                masks.append(mask)
                if current_segment.start_frame <= absolute_frame <= current_segment.end_frame:
                    target_local_indices.append(offset)

            outputs = inpainter.process_segment(context_frames, masks, target_local_indices)

            cap.set(cv2.CAP_PROP_POS_FRAMES, current_segment.start_frame)
            absolute_frame = current_segment.start_frame
            while absolute_frame <= current_segment.end_frame:
                ok, frame = cap.read()
                if not ok:
                    break
                local_idx = absolute_frame - current_segment.context_start
                writer.write(outputs.get(local_idx, frame))
                absolute_frame += 1
                current_frame += 1
                progress.update(1)
                if monotonic() - last_progress_emit >= 0.4:
                    percent = 25 + int(68 * (current_frame / max(1, video_info.total_frames)))
                    _notify(progress_callback, "erasing", percent, "擦除字幕中")
                    last_progress_emit = monotonic()

            current_segment = next(segment_iter, None)
    finally:
        progress.close()
        cap.release()
        writer.close()

    _notify(progress_callback, "muxing", 96, "合并音频")
    _merge_audio(str(temp_video), input_path, output_path)
    temp_video.unlink(missing_ok=True)
    _notify(progress_callback, "completed", 100, "处理完成")
    return detection
