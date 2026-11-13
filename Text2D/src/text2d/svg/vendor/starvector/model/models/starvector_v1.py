"""StarVector v1 (StarCoder-1B) — inference-only subset."""

from __future__ import annotations

import torch
import torch.nn as nn

from .starvector_base import StarVectorBase


class StarVectorStarCoder(StarVectorBase):
    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)

    def _get_svg_transformer(self, config, **kwargs):
        from ..llm.starcoder import StarCoderModel  # StarCoder (v1)

        return StarCoderModel(config, **kwargs)

    def _get_embeddings(self, input_ids):
        """V1-specific embedding method."""
        return self.svg_transformer.transformer.transformer.wte(input_ids)

    def _get_svg_text(self, svg_list):
        """V1-specific SVG text preparation."""
        return [t + self.svg_transformer.tokenizer.eos_token for t in svg_list]
