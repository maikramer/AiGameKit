"""Tests for paint_prep (inpaint mask restriction — no GPU)."""

from __future__ import annotations

import numpy as np


def test_restrict_inpaint_mask_keeps_far_holes() -> None:
    from paint3d.paint_prep import restrict_inpaint_mask

    mask = np.zeros((32, 32), dtype=np.uint8)
    mask[10:20, 10:20] = 255
    new_mask, far = restrict_inpaint_mask(mask, dilate_px=2)
    assert far[0, 0]
    assert not far[15, 15]
    assert new_mask[15, 15] == 255
    # Near the trusted blob, zeros become inpaint targets (0)
    assert (new_mask == 0).any()
