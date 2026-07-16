from __future__ import annotations

from click.testing import CliRunner
from part3d.cli import main


def test_decompose_help_exposes_fine_segmentation_controls() -> None:
    result = CliRunner().invoke(main, ["decompose", "--help"])

    assert result.exit_code == 0
    assert "--fine-parts" in result.output
    assert "--detail-levels" in result.output
    assert "--min-cluster-support" in result.output
    assert "--prompt-batch-size" in result.output
    assert "--segmentation-proxy" in result.output
    assert "--multi-head" in result.output
    assert "--consensus" in result.output
    assert "--consensus-vote" in result.output
    assert "--segment-mode" in result.output
    assert "--parts-mode" in result.output
    assert "--xpart-max-area-frac" in result.output
