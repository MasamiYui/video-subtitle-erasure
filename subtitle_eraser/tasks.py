from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from subtitle_eraser.models import TaskStatus
from subtitle_eraser.video import PipelineConfig, erase_subtitles

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, work_dir: Path, subtitle_ocr_project: str | None = None):
        self.work_dir = work_dir
        self.subtitle_ocr_project = subtitle_ocr_project
        self.upload_dir = work_dir / "uploads"
        self.output_dir = work_dir / "outputs"
        self.debug_dir = work_dir / "debug"
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskStatus] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="subtitle-erase")

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def create_task(self, filename: str | None) -> TaskStatus:
        task_id = uuid4().hex[:12]
        status = TaskStatus(
            task_id=task_id,
            status="queued",
            progress=0,
            stage="queued",
            message="任务已创建",
            input_filename=filename,
        )
        with self._lock:
            self._tasks[task_id] = status
        return status

    def get(self, task_id: str) -> TaskStatus | None:
        with self._lock:
            return self._tasks.get(task_id)

    def serialize(self, task_id: str) -> dict[str, Any] | None:
        status = self.get(task_id)
        return asdict(status) if status is not None else None

    def build_paths(self, task_id: str, original_name: str) -> tuple[Path, Path, Path]:
        safe_stem = Path(original_name or "input.mp4").stem.replace(" ", "_")
        input_path = self.upload_dir / f"{task_id}_{safe_stem}.mp4"
        output_path = self.output_dir / f"{safe_stem}_no_sub_{task_id}.mp4"
        debug_path = self.debug_dir / task_id
        return input_path, output_path, debug_path

    def start(
        self,
        task_id: str,
        input_path: Path,
        output_path: Path,
        debug_path: Path,
        config: PipelineConfig,
    ) -> None:
        self._executor.submit(self._run, task_id, input_path, output_path, debug_path, config)

    def _update(self, task_id: str, **kwargs: Any) -> None:
        with self._lock:
            status = self._tasks[task_id]
            for key, value in kwargs.items():
                setattr(status, key, value)

    def _run(
        self,
        task_id: str,
        input_path: Path,
        output_path: Path,
        debug_path: Path,
        config: PipelineConfig,
    ) -> None:
        debug_path.mkdir(parents=True, exist_ok=True)
        self._update(
            task_id,
            status="running",
            stage="detecting",
            progress=1,
            message="开始处理",
            output_path=str(output_path),
            debug_path=str(debug_path / "detection.json"),
            result_url=f"/api/v1/erase/download/{task_id}",
            debug_url=f"/api/v1/erase/debug/{task_id}",
        )

        run_config = PipelineConfig(
            subtitle_ocr_project=config.subtitle_ocr_project or self.subtitle_ocr_project,
            sample_interval=config.sample_interval,
            language=config.language,
            mode=config.mode,
            position_mode=config.position_mode,
            roi_bottom_ratio=config.roi_bottom_ratio,
            requested_regions=config.requested_regions,
            mask_dilate_x=config.mask_dilate_x,
            mask_dilate_y=config.mask_dilate_y,
            mask_temporal_radius=config.mask_temporal_radius,
            segment_gap_frames=config.segment_gap_frames,
            context_frames=config.context_frames,
            event_lead_frames=config.event_lead_frames,
            event_trail_frames=config.event_trail_frames,
            residual_cleanup_passes=config.residual_cleanup_passes,
            inpaint_backend=config.inpaint_backend,
            inpaint_radius=config.inpaint_radius,
            inpaint_context_margin=config.inpaint_context_margin,
            max_temporal_references=config.max_temporal_references,
            temporal_min_consensus=config.temporal_min_consensus,
            temporal_max_std=config.temporal_max_std,
            cleanup_max_coverage=config.cleanup_max_coverage,
            merge_threshold=config.merge_threshold,
            ocr_det_db_thresh=config.ocr_det_db_thresh,
            ocr_det_db_box_thresh=config.ocr_det_db_box_thresh,
            prefilter_enabled=config.prefilter_enabled,
            debug_dir=str(debug_path),
        )

        def on_progress(stage: str, progress: int, message: str) -> None:
            self._update(task_id, stage=stage, progress=progress, message=message)

        try:
            erase_subtitles(
                input_path=str(input_path),
                output_path=str(output_path),
                config=run_config,
                progress_callback=on_progress,
            )
        except Exception as exc:
            logger.exception("Subtitle erasure task failed: %s", task_id)
            self._update(
                task_id,
                status="failed",
                stage="failed",
                progress=100,
                message="处理失败",
                error=str(exc),
            )
            return

        self._update(
            task_id,
            status="completed",
            stage="completed",
            progress=100,
            message="处理完成",
        )
