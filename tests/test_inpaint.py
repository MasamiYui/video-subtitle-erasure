from __future__ import annotations

import numpy as np

from subtitle_eraser.inpaint import HybridTemporalInpainter, InpaintConfig


def test_flow_guided_backend_recovers_background_better_than_telea() -> None:
    rng = np.random.default_rng(7)
    background = rng.integers(0, 255, size=(72, 128, 3), dtype=np.uint8)
    target = background.copy()
    target[48:64, 24:104] = 255

    mask = np.zeros((72, 128), dtype=np.uint8)
    mask[48:64, 24:104] = 255
    zero_mask = np.zeros_like(mask)

    frames = [background.copy(), target, background.copy()]
    masks = [zero_mask, mask, zero_mask]

    telea = HybridTemporalInpainter(
        InpaintConfig(
            backend="telea",
            spatial_radius=3,
            context_margin=8,
            cleanup_passes=0,
        )
    )
    flow_guided = HybridTemporalInpainter(
        InpaintConfig(
            backend="flow-guided",
            spatial_radius=3,
            context_margin=8,
            cleanup_passes=0,
            max_temporal_references=2,
        )
    )

    telea_out = telea.process_segment(frames, masks, [1])[1]
    flow_out = flow_guided.process_segment(frames, masks, [1])[1]

    masked = mask > 0
    telea_mae = np.abs(telea_out.astype(np.int16) - background.astype(np.int16))[masked].mean()
    flow_mae = np.abs(flow_out.astype(np.int16) - background.astype(np.int16))[masked].mean()

    assert flow_mae < telea_mae


def test_flow_guided_falls_back_to_telea_without_consensus() -> None:
    rng = np.random.default_rng(17)
    background = rng.integers(0, 255, size=(72, 128, 3), dtype=np.uint8)
    target = background.copy()
    target[48:64, 24:104] = 255

    mask = np.zeros((72, 128), dtype=np.uint8)
    mask[48:64, 24:104] = 255
    zero_mask = np.zeros_like(mask)

    frames = [background.copy(), target, background.copy()]
    masks = [zero_mask, mask, mask]

    telea = HybridTemporalInpainter(
        InpaintConfig(
            backend="telea",
            spatial_radius=3,
            context_margin=8,
            cleanup_passes=0,
        )
    )
    flow_guided = HybridTemporalInpainter(
        InpaintConfig(
            backend="flow-guided",
            spatial_radius=3,
            context_margin=8,
            cleanup_passes=0,
            max_temporal_references=2,
            temporal_min_consensus=2,
        )
    )

    telea_out = telea.process_segment(frames, masks, [1])[1]
    flow_out = flow_guided.process_segment(frames, masks, [1])[1]

    assert np.array_equal(flow_out, telea_out)
