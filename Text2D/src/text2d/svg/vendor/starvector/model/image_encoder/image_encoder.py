"""CLIP image encoder — inference-only subset of the upstream module.

Only the ``clip`` branch is used by the 1B checkpoint; VQGAN/ConvNeXt/SigLIP
require the training stack (omegaconf/taming/open_clip) and are not shipped.
"""

from __future__ import annotations

import torch.nn as nn

from .clip_model import convert_weights_to_precision


class ImageEncoder(nn.Module):
    def __init__(self, config, **kwargs):
        super().__init__()

        image_size = config.image_size
        torch_dtype = kwargs.get("model_precision", config.torch_dtype)
        self.image_encoder_type = config.image_encoder_type
        if self.image_encoder_type != "clip":
            raise ValueError(
                f"StarVector vendor: only image_encoder_type='clip' is supported, "
                f"got {self.image_encoder_type!r}"
            )

        self.visual_encoder, self.ln_vision = self.build_clip_encoder(image_size=image_size)
        convert_weights_to_precision(self, torch_dtype)
        # The Text2D engine preprocesses images itself (CLIP mean/std/size).
        self.processor = None

    def build_clip_encoder(self, image_size):
        from .clip_model import LayerNorm, VisionTransformer

        visual_encoder = VisionTransformer(
            input_resolution=image_size,
            patch_size=14,
            width=1024,
            layers=23,
            heads=16,
            use_grad_checkpointing=False,
        )

        ln_vision = LayerNorm(visual_encoder.num_features)
        return visual_encoder, ln_vision

    def forward(self, image):
        embeds = self.visual_encoder(image)
        return self.ln_vision(embeds)

    def process_images(self, images):
        # Kept for API parity; the engine does not call it.
        return images
