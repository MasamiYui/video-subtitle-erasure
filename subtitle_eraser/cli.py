from __future__ import annotations

import argparse
import logging
from pathlib import Path

from subtitle_eraser.video import PipelineConfig, erase_subtitles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Erase hard subtitles from MP4 videos")
    parser.add_argument("--input", required=True, help="Input MP4 video path")
    parser.add_argument("--output", required=True, help="Output MP4 video path")
    parser.add_argument("--subtitle-ocr-project", default=None, help="Path to the sibling subtitle-ocr project")
    parser.add_argument("--language", default="ch", help="OCR language, default: ch")
    parser.add_argument("--mode", default="auto", choices=["auto", "semi-auto", "manual-fixed"], help="Subtitle erasure mode")
    parser.add_argument("--sample-interval", type=float, default=0.25, help="OCR sampling interval in seconds")
    parser.add_argument("--position-mode", default="bottom", choices=["auto", "bottom", "middle", "top"], help="Subtitle position prior")
    parser.add_argument("--roi-bottom-ratio", type=float, default=0.34, help="Bottom ROI ratio for detection")
    parser.add_argument("--mask-dilate-x", type=int, default=14, help="Horizontal subtitle mask dilation")
    parser.add_argument("--mask-dilate-y", type=int, default=10, help="Vertical subtitle mask dilation")
    parser.add_argument("--segment-gap-frames", type=int, default=3, help="Merge adjacent subtitle events when gap is small")
    parser.add_argument("--context-frames", type=int, default=12, help="Reference frames added before and after each subtitle segment")
    parser.add_argument("--event-lead-frames", type=int, default=2, help="Extend subtitle start by N frames")
    parser.add_argument("--event-trail-frames", type=int, default=8, help="Extend subtitle end by N frames")
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

    erase_subtitles(
        input_path=input_path,
        output_path=output_path,
        config=PipelineConfig(
            subtitle_ocr_project=subtitle_ocr_project,
            language=args.language,
            mode=args.mode,
            sample_interval=args.sample_interval,
            position_mode=args.position_mode,
            roi_bottom_ratio=args.roi_bottom_ratio,
            mask_dilate_x=args.mask_dilate_x,
            mask_dilate_y=args.mask_dilate_y,
            segment_gap_frames=args.segment_gap_frames,
            context_frames=args.context_frames,
            event_lead_frames=args.event_lead_frames,
            event_trail_frames=args.event_trail_frames,
            debug_dir=debug_dir,
        ),
    )


if __name__ == "__main__":
    main()
