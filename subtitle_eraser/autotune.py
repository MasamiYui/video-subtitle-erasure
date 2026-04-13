from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from subtitle_eraser.detection import detect_subtitles, load_detection_debug
from subtitle_eraser.evaluation import evaluate_processed_video, select_sample_clips
from subtitle_eraser.video import PipelineConfig, erase_subtitles


@dataclass(slots=True)
class ClipTrialResult:
    clip_start: float
    clip_end: float
    score: float
    residual_ratio: float
    spill_score: float
    residual_events: int
    reference_events: int
    input_clip: str
    output_clip: str


@dataclass(slots=True)
class TuneTrialResult:
    trial_index: int
    score: float
    config: dict
    clips: list[ClipTrialResult]


@dataclass(slots=True)
class AutoTuneConfig:
    enabled: bool = False
    max_trials: int = 6
    max_rounds: int = 2
    clip_duration: float = 6.0
    max_clips: int = 3
    target_score: float = 0.08
    work_dir: str | None = None


@dataclass(slots=True)
class AutoTuneResult:
    best_config: PipelineConfig
    best_score: float
    trials: list[TuneTrialResult]
    sample_clips: list[tuple[float, float]]

    def to_dict(self) -> dict:
        return {
            "best_score": self.best_score,
            "best_config": asdict(self.best_config),
            "sample_clips": self.sample_clips,
            "trials": [
                {
                    "trial_index": trial.trial_index,
                    "score": trial.score,
                    "config": trial.config,
                    "clips": [asdict(item) for item in trial.clips],
                }
                for trial in self.trials
            ],
        }


def _extract_clip(input_path: str, output_path: str, start_time: float, end_time: float) -> None:
    duration = max(0.2, end_time - start_time)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_time:.3f}",
            "-i",
            input_path,
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            output_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _config_fingerprint(config: PipelineConfig) -> tuple:
    return (
        round(config.sample_interval, 3),
        config.mask_dilate_x,
        config.mask_dilate_y,
        config.mask_temporal_radius,
        config.event_lead_frames,
        config.event_trail_frames,
        config.residual_cleanup_passes,
        config.inpaint_backend,
        config.inpaint_radius,
        config.inpaint_context_margin,
        config.max_temporal_references,
        config.temporal_min_consensus,
        round(config.temporal_max_std, 2),
        round(config.cleanup_max_coverage, 4),
        round(config.merge_threshold, 3),
        config.ocr_det_db_thresh,
        config.ocr_det_db_box_thresh,
        config.prefilter_enabled,
    )


def _candidate_configs(seed: PipelineConfig) -> list[PipelineConfig]:
    sample_fast = min(seed.sample_interval, 0.18)
    configs = [
        replace(seed),
        replace(
            seed,
            sample_interval=min(sample_fast, 0.14),
            mask_dilate_x=seed.mask_dilate_x + 6,
            mask_dilate_y=seed.mask_dilate_y + 3,
            mask_temporal_radius=max(seed.mask_temporal_radius, 1),
            event_lead_frames=seed.event_lead_frames + 2,
            event_trail_frames=seed.event_trail_frames + 8,
            residual_cleanup_passes=0,
            inpaint_backend="telea",
            inpaint_radius=max(seed.inpaint_radius, 3),
            inpaint_context_margin=seed.inpaint_context_margin + 24,
            merge_threshold=min(seed.merge_threshold, 0.72),
        ),
        replace(
            seed,
            inpaint_backend="flow-guided",
            sample_interval=sample_fast,
            mask_dilate_x=seed.mask_dilate_x + 4,
            mask_dilate_y=seed.mask_dilate_y + 2,
            mask_temporal_radius=max(seed.mask_temporal_radius, 1),
            event_lead_frames=seed.event_lead_frames + 1,
            event_trail_frames=seed.event_trail_frames + 4,
            max_temporal_references=max(seed.max_temporal_references, 6),
            merge_threshold=min(seed.merge_threshold, 0.74),
        ),
        replace(
            seed,
            inpaint_backend="flow-guided",
            sample_interval=min(sample_fast, 0.14),
            mask_dilate_x=seed.mask_dilate_x + 8,
            mask_dilate_y=seed.mask_dilate_y + 4,
            mask_temporal_radius=max(seed.mask_temporal_radius, 1),
            event_lead_frames=seed.event_lead_frames + 2,
            event_trail_frames=seed.event_trail_frames + 8,
            residual_cleanup_passes=max(seed.residual_cleanup_passes, 1),
            inpaint_radius=max(seed.inpaint_radius, 3),
            inpaint_context_margin=seed.inpaint_context_margin + 24,
            max_temporal_references=max(seed.max_temporal_references, 8),
            merge_threshold=min(seed.merge_threshold, 0.70),
        ),
        replace(
            seed,
            sample_interval=min(sample_fast, 0.12),
            mask_dilate_x=seed.mask_dilate_x + 10,
            mask_dilate_y=seed.mask_dilate_y + 4,
            mask_temporal_radius=max(seed.mask_temporal_radius, 1),
            event_lead_frames=seed.event_lead_frames + 4,
            event_trail_frames=seed.event_trail_frames + 12,
            residual_cleanup_passes=max(seed.residual_cleanup_passes, 1),
            inpaint_backend="telea",
            inpaint_radius=max(seed.inpaint_radius, 4),
            inpaint_context_margin=seed.inpaint_context_margin + 40,
            merge_threshold=min(seed.merge_threshold, 0.66),
        ),
    ]
    deduped: list[PipelineConfig] = []
    seen: set[tuple] = set()
    for config in configs:
        fingerprint = _config_fingerprint(config)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(config)
    return deduped


def _reference_detection_for_clip(clip_path: str, base_config: PipelineConfig):
    return detect_subtitles(
        video_path=clip_path,
        subtitle_ocr_project=base_config.subtitle_ocr_project,
        sample_interval=min(base_config.sample_interval, 0.14),
        language=base_config.language,
        mode=base_config.mode,
        position_mode=base_config.position_mode,
        roi_bottom_ratio=base_config.roi_bottom_ratio,
        requested_regions=base_config.requested_regions,
        event_lead_frames=max(base_config.event_lead_frames, 1),
        event_trail_frames=max(base_config.event_trail_frames, 6),
        merge_threshold=min(base_config.merge_threshold, 0.74),
        ocr_det_db_thresh=base_config.ocr_det_db_thresh,
        ocr_det_db_box_thresh=base_config.ocr_det_db_box_thresh,
        prefilter_enabled=base_config.prefilter_enabled,
    )


def _with_tuning_ocr_defaults(config: PipelineConfig) -> PipelineConfig:
    return replace(
        config,
        ocr_det_db_thresh=config.ocr_det_db_thresh if config.ocr_det_db_thresh is not None else 0.24,
        ocr_det_db_box_thresh=config.ocr_det_db_box_thresh if config.ocr_det_db_box_thresh is not None else 0.40,
        prefilter_enabled=False if config.prefilter_enabled is None else config.prefilter_enabled,
    )


def auto_tune_config(
    input_path: str,
    base_config: PipelineConfig,
    tune_config: AutoTuneConfig,
) -> AutoTuneResult:
    if not tune_config.enabled:
        return AutoTuneResult(best_config=base_config, best_score=float("inf"), trials=[], sample_clips=[])

    base_config = _with_tuning_ocr_defaults(base_config)
    work_dir = Path(tune_config.work_dir or Path(input_path).with_suffix(""))
    tune_dir = work_dir / "autotune"
    clips_dir = tune_dir / "clips"
    outputs_dir = tune_dir / "outputs"
    debug_dir = tune_dir / "debug"
    for path in (clips_dir, outputs_dir, debug_dir):
        path.mkdir(parents=True, exist_ok=True)

    initial_detection = detect_subtitles(
        video_path=input_path,
        subtitle_ocr_project=base_config.subtitle_ocr_project,
        sample_interval=max(base_config.sample_interval, 0.32),
        language=base_config.language,
        mode=base_config.mode,
        position_mode=base_config.position_mode,
        roi_bottom_ratio=base_config.roi_bottom_ratio,
        requested_regions=base_config.requested_regions,
        event_lead_frames=max(base_config.event_lead_frames, 1),
        event_trail_frames=max(base_config.event_trail_frames, 6),
        merge_threshold=min(base_config.merge_threshold, 0.74),
        ocr_det_db_thresh=base_config.ocr_det_db_thresh,
        ocr_det_db_box_thresh=base_config.ocr_det_db_box_thresh,
        prefilter_enabled=base_config.prefilter_enabled,
    ) if not base_config.reuse_detection_path else load_detection_debug(base_config.reuse_detection_path)
    sample_clips = select_sample_clips(
        initial_detection.video_info,
        initial_detection.events,
        clip_duration=tune_config.clip_duration,
        max_clips=tune_config.max_clips,
    )

    best_config = base_config
    best_score = float("inf")
    trials: list[TuneTrialResult] = []
    seen: set[tuple] = set()
    seed_config = base_config
    trial_index = 0

    for _round in range(tune_config.max_rounds):
        for candidate in _candidate_configs(seed_config):
            fingerprint = _config_fingerprint(candidate)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            if trial_index >= tune_config.max_trials:
                break

            clip_results: list[ClipTrialResult] = []
            weighted_score = 0.0
            weighted_mass = 0.0

            for clip_idx, (clip_start, clip_end) in enumerate(sample_clips):
                clip_input = clips_dir / f"clip_{clip_idx:02d}_{trial_index:02d}.mp4"
                clip_output = outputs_dir / f"clip_{clip_idx:02d}_{trial_index:02d}_no_sub.mp4"
                clip_debug = debug_dir / f"trial_{trial_index:02d}_clip_{clip_idx:02d}"
                _extract_clip(input_path, str(clip_input), clip_start, clip_end)
                reference_detection = _reference_detection_for_clip(str(clip_input), candidate)
                clip_run_config = replace(candidate, debug_dir=str(clip_debug))
                erase_subtitles(str(clip_input), str(clip_output), clip_run_config)
                metrics = evaluate_processed_video(
                    input_path=str(clip_input),
                    output_path=str(clip_output),
                    reference_detection=reference_detection,
                    subtitle_ocr_project=candidate.subtitle_ocr_project,
                    language=candidate.language,
                    position_mode=candidate.position_mode,
                    roi_bottom_ratio=candidate.roi_bottom_ratio,
                    mode=candidate.mode,
                    requested_regions=candidate.requested_regions,
                    sample_interval=candidate.sample_interval,
                    pad_x=candidate.mask_dilate_x,
                    pad_y=candidate.mask_dilate_y,
                    ocr_det_db_thresh=candidate.ocr_det_db_thresh,
                    ocr_det_db_box_thresh=candidate.ocr_det_db_box_thresh,
                    prefilter_enabled=candidate.prefilter_enabled,
                )
                clip_results.append(
                    ClipTrialResult(
                        clip_start=clip_start,
                        clip_end=clip_end,
                        score=metrics.score,
                        residual_ratio=metrics.residual_ratio,
                        spill_score=metrics.spill_score,
                        residual_events=metrics.residual_events,
                        reference_events=metrics.reference_events,
                        input_clip=str(clip_input),
                        output_clip=str(clip_output),
                    )
                )
                weighted_score += metrics.score * max(metrics.reference_mass, 1e-6)
                weighted_mass += max(metrics.reference_mass, 1e-6)

            trial_score = weighted_score / max(weighted_mass, 1e-6)
            trials.append(
                TuneTrialResult(
                    trial_index=trial_index,
                    score=trial_score,
                    config=asdict(candidate),
                    clips=clip_results,
                )
            )
            if trial_score < best_score:
                best_score = trial_score
                best_config = candidate
            trial_index += 1
            if best_score <= tune_config.target_score:
                break

        seed_config = best_config
        if trial_index >= tune_config.max_trials or best_score <= tune_config.target_score:
            break

    report = AutoTuneResult(
        best_config=best_config,
        best_score=best_score,
        trials=trials,
        sample_clips=sample_clips,
    )
    (tune_dir / "report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return report
