"""StarVector wrapper model (inference-only fork of the upstream arch).

Upstream: https://github.com/joanrod/star-vector (Apache-2.0).
The processor class and its AutoProcessor registration were removed: the
Text2D engine does its own CLIP preprocessing and never needs the HF
processor.
"""

from __future__ import annotations

from typing import Tuple, Union

import torch
from torch.nn import CrossEntropyLoss
from transformers import PretrainedConfig, PreTrainedModel
from transformers.models.gpt_bigcode.modeling_gpt_bigcode import (
    CausalLMOutputWithCrossAttentions,
)


class StarVectorConfig(PretrainedConfig):
    model_type = "starvector"

    def __init__(
        self,
        starcoder_model_name: str = "bigcode/starcoderbase-1b",
        image_encoder_type: str = "clip",
        adapter_norm: str = "layer_norm",
        image_size: int = 224,
        max_length: int = 8192,
        max_length_train: int = 8192,
        use_flash_attn: bool = True,
        use_cache: bool = True,
        num_attention_heads: int = 16,
        num_hidden_layers: int = 24,
        vocab_size: int = 49152,
        hidden_size: int = 2048,
        num_kv_heads: int = 4,
        torch_dtype: str = "bfloat16",
        tie_word_embeddings: bool = True,
        **kwargs,
    ):
        # transformers 5.x: ``dtype`` em vez de ``torch_dtype``. O checkpoint
        # do StarCoder tem o lm_head tied (só existe transformer.wte.weight).
        kwargs.setdefault("dtype", torch_dtype)
        kwargs.setdefault("tie_word_embeddings", tie_word_embeddings)
        self.starcoder_model_name = starcoder_model_name
        self.image_encoder_type = image_encoder_type
        self.adapter_norm = adapter_norm
        self.image_size = image_size
        self.max_length = max_length
        self.max_length_train = max_length_train
        self.use_flash_attn = use_flash_attn
        self.use_cache = use_cache
        self.num_attention_heads = num_attention_heads
        self.num_hidden_layers = num_hidden_layers
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        self.num_kv_heads = num_kv_heads
        super().__init__(**kwargs)


class StarVectorForCausalLM(PreTrainedModel):
    config_class = StarVectorConfig
    _no_split_modules: list[str] = []
    # O checkpoint só traz ``transformer.wte.weight``; o lm_head do StarCoder
    # é tied. Em transformers 5.x isto também alimenta
    # ``all_tied_weights_keys`` (usado por from_pretrained/generate).
    _tied_weights_keys = {
        "model.svg_transformer.transformer.lm_head.weight": (
            "model.svg_transformer.transformer.transformer.wte.weight"
        ),
    }

    def __init__(self, config: StarVectorConfig, **kwargs):
        super().__init__(config)
        starcoder_model_name = config.starcoder_model_name
        if "starcoder2" in starcoder_model_name:
            raise ValueError(
                "StarVector vendor: only the v1 (StarCoder-1B) checkpoint is shipped; "
                f"got {starcoder_model_name!r}"
            )
        from .model.models.starvector_v1 import StarVectorStarCoder

        self.model = StarVectorStarCoder(config=config, **kwargs)
        # transformers 5.x: ``all_tied_weights_keys`` é criado no post_init;
        # o upstream não o chamava e o from_pretrained rebentava.
        self.post_init()

    @property
    def supports_gradient_checkpointing(self):
        if hasattr(self.model, "svg_transformer"):
            return getattr(self.model.svg_transformer, "supports_gradient_checkpointing", False)
        return False

    def gradient_checkpointing_enable(self):
        if hasattr(self.model, "svg_transformer") and hasattr(
            self.model.svg_transformer, "gradient_checkpointing_enable"
        ):
            self.model.svg_transformer.gradient_checkpointing_enable()

    def forward(
        self,
        vision_embeds,
        input_ids,
        num_generations,
        attention_mask,
        num_logits_to_keep,
    ) -> Union[Tuple, CausalLMOutputWithCrossAttentions]:
        completion_embeds = self.model._get_embeddings(input_ids)
        inputs_embeds = torch.cat(
            [vision_embeds.repeat(num_generations, 1, 1), completion_embeds], dim=1
        )

        transformer_outputs = self.model.svg_transformer.transformer.transformer(
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
        )
        hidden_states = transformer_outputs[0]

        if num_logits_to_keep > 0:
            lm_logits = self.model.svg_transformer.transformer.lm_head(
                hidden_states[:, -num_logits_to_keep:, :]
            )
        else:
            lm_logits = self.model.svg_transformer.transformer.lm_head(hidden_states)

        loss: CrossEntropyLoss | None = None
        return CausalLMOutputWithCrossAttentions(
            loss=loss,
            logits=lm_logits,
            past_key_values=transformer_outputs.past_key_values,
            hidden_states=transformer_outputs.hidden_states,
            attentions=transformer_outputs.attentions,
            cross_attentions=transformer_outputs.cross_attentions,
        )

    def generate_im2svg(self, batch, **kwargs):
        return self.model.generate_im2svg(batch, **kwargs)

    def generate_im2text(self, batch, **kwargs):
        return self.model.generate_im2text(batch, **kwargs)

    def process_images(self, images):
        return self.model.image_encoder.process_images(images)
