from __future__ import annotations

import argparse
import logging
from pathlib import Path

from subtitle_eraser.autotune import AutoTuneConfig, auto_tune_config
from subtitle_eraser.video import PipelineConfig, erase_subtitles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Erase hard subtitles from MP4 videos")
    parser.add_argument("--input", required=True, help="Input MP4 video path")
    parser.add_argument("--output", required=True, help="Output MP4 video path")
    parser.add_argument("--subtitle-ocr-project", default=None, help="Path to the sibling subtitle-ocr project")
    parser.add_argument("--reuse-detection", default=None, help="Reuse an existing detection.json file")
    parser.add_argument("--language", default="ch", help="OCR language, default: ch")
    parser.add_argument("--mode", default="auto", choices=["auto", "semi-auto", "manual-fixed"], help="Subtitle erasure mode")
    parser.add_argument("--sample-interval", type=float, default=0.25, help="OCR sampling interval in seconds")
    parser.add_argument("--position-mode", default="bottom", choices=["auto", "bottom", "middle", "top"], help="Subtitle position prior")
    parser.add_argument("--roi-bottom-ratio", type=float, default=0.34, help="Bottom ROI ratio for detection")
    parser.add_argument(
        "--region",
        action="append",
        default=[],
        help="Normalized ROI as x1,y1,x2,y2. Repeat for multiple regions.",
    )
    parser.add_argument("--mask-dilate-x", type=int, default=14, help="Horizontal subtitle mask dilation")
    parser.add_argument("--mask-dilate-y", type=int, default=10, help="Vertical subtitle mask dilation")
    parser.add_argument("--mask-temporal-radius", type=int, default=0, help="Propagate subtitle masks from neighboring frames")
    parser.add_argument("--segment-gap-frames", type=int, default=3, help="Merge adjacent subtitle events when gap is small")
    parser.add_argument("--context-frames", type=int, default=12, help="Reference frames added before and after each subtitle segment")
    parser.add_argument("--event-lead-frames", type=int, default=2, help="Extend subtitle start by N frames")
    parser.add_argument("--event-trail-frames", type=int, default=8, help="Extend subtitle end by N frames")
    parser.add_argument("--residual-cleanup-passes", type=int, default=0, help="Extra local cleanup passes after inpainting")
    parser.add_argument("--inpaint-backend", default="telea", choices=["telea", "flow-guided"], help="Inpainting backend")
    parser.add_argument("--inpaint-radius", type=int, default=3, help="OpenCV inpaint radius")
    parser.add_argument("--inpaint-context-margin", type=int, default=80, help="Additional context around subtitle ROI during inpainting")
    parser.add_argument("--max-temporal-references", type=int, default=6, help="Maximum neighboring frames used by flow-guided backend")
    parser.add_argument("--temporal-consensus", type=int, default=2, help="Minimum agreeing reference frames before using temporal fill")
    parser.add_argument("--temporal-std-threshold", type=float, default=14.0, help="Max per-pixel color std allowed for temporal fill")
    parser.add_argument("--cleanup-max-coverage", type=float, default=0.045, help="Skip residual cleanup when the detected cleanup area is too large")
    parser.add_argument("--merge-threshold", type=float, default=0.78, help="Subtitle merge threshold passed to subtitle-ocr")
    parser.add_argument("--ocr-det-db-thresh", type=float, default=None, help="Optional PaddleOCR det_db_thresh override")
    parser.add_argument("--ocr-det-db-box-thresh", type=float, default=None, help="Optional PaddleOCR det_db_box_thresh override")
    parser.add_argument("--disable-prefilter", action="store_true", help="Disable subtitle-ocr lightweight text prefilter")
    parser.add_argument("--auto-tune", action="store_true", help="Automatically search better parameters on sampled clips before full render")
    parser.add_argument("--tune-max-trials", type=int, default=6, help="Maximum auto-tune trials")
    parser.add_argument("--tune-max-rounds", type=int, default=2, help="Maximum auto-tune rounds")
    parser.add_argument("--tune-clip-duration", type=float, default=6.0, help="Per-clip duration used for auto-tuning")
    parser.add_argument("--tune-max-clips", type=int, default=3, help="Maximum sampled clips used for auto-tuning")
    parser.add_argument("--tune-target-score", type=float, default=0.08, help="Stop auto-tune early once score is below this threshold")
    parser.add_argument("--tune-work-dir", default=None, help="Directory for auto-tune samples and reports")
    parser.add_argument("--debug-dir", default=None, help="Optional directory for detection debug output")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    input_path = str(Path(args.input).expanduser().resolve())
    output_path = str(Path(args.output).expanduser().resolve())
    debug_dir = str(Path(args.debug_dir).expanduser().resolve()) if args.debug_dir else None
    subtitle_ocr_project = (
        str(Path(args.subtitle_ocr_project).expanduser().resolve())
        if args.subtitle_ocr_project
        else None
    )
    reuse_detection = str(Path(args.reuse_detection).expanduser().resolve()) if args.reuse_detection else None
    requested_regions = []
    for raw_region in args.region:
        parts = [float(part.strip()) for part in raw_region.split(",")]
        if len(parts) != 4:
            raise ValueError(f"Invalid --region value: {raw_region}")
        requested_regions.append(tuple(parts))

    config = PipelineConfig(
        subtitle_ocr_project=subtitle_ocr_project,
        language=args.language,
        mode=args.mode,
        sample_interval=args.sample_interval,
        position_mode=args.position_mode,
        roi_bottom_ratio=args.roi_bottom_ratio,
        requested_regions=requested_regions or None,
        mask_dilate_x=args.mask_dilate_x,
        mask_dilate_y=args.mask_dilate_y,
        mask_temporal_radius=args.mask_temporal_radius,
        segment_gap_frames=args.segment_gap_frames,
        context_frames=args.context_frames,
        event_lead_frames=args.event_lead_frames,
        event_trail_frames=args.event_trail_frames,
        residual_cleanup_passes=args.residual_cleanup_passes,
        inpaint_backend=args.inpaint_backend,
        inpaint_radius=args.inpaint_radius,
        inpaint_context_margin=args.inpaint_context_margin,
        max_temporal_references=args.max_temporal_references,
        temporal_min_consensus=args.temporal_consensus,
        temporal_max_std=args.temporal_std_threshold,
        cleanup_max_coverage=args.cleanup_max_coverage,
        merge_threshold=args.merge_threshold,
        ocr_det_db_thresh=args.ocr_det_db_thresh,
        ocr_det_db_box_thresh=args.ocr_det_db_box_thresh,
        prefilter_enabled=False if args.disable_prefilter else None,
        debug_dir=debug_dir,
        reuse_detection_path=reuse_detection,
    )

    if args.auto_tune:
        tune_result = auto_tune_config(
            input_path=input_path,
            base_config=config,
            tune_config=AutoTuneConfig(
                enabled=True,
                max_trials=args.tune_max_trials,
                max_rounds=args.tune_max_rounds,
                clip_duration=args.tune_clip_duration,
                max_clips=args.tune_max_clips,
                target_score=args.tune_target_score,
                work_dir=args.tune_work_dir or debug_dir,
            ),
        )
        config = tune_result.best_config
        logging.getLogger(__name__).info(
            "auto-tune selected score=%.4f config=%s",
            tune_result.best_score,
            config,
        )

    erase_subtitles(
        input_path=input_path,
        output_path=output_path,
        config=config,
    )


if __name__ == "__main__":
    main()
