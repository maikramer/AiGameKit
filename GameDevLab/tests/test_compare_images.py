import pytest

pytest.importorskip("numpy")

import numpy as np

from gamedev_lab.compare_images import metrics_mae_rmse_ssim


def test_metrics_identical() -> None:
    a = np.zeros((8, 8, 3), dtype=np.float32)
    b = np.zeros((8, 8, 3), dtype=np.float32)
    m = metrics_mae_rmse_ssim(a, b)
    assert m["mae"] == 0.0
    assert m["rmse"] == 0.0
    assert m["ssim"] >= 0.99
