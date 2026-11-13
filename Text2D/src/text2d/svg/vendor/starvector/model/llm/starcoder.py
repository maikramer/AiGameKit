"""StarCoder-1B wrapper used by StarVector v1.

The upstream code builds the LLM from ``bigcode/starcoderbase-1b``, which is a
* gated * repo on the Hub. The StarVector checkpoint already contains the
fine-tuned LLM and the extended tokenizer, so we build the GPT-BigCode
architecture from the StarVector config and load the tokenizer from the
StarVector repository. No access request / HF_TOKEN is needed.
"""

from __future__ import annotations

import torch
import torch.nn as nn
from transformers import AutoModelForCausalLM, AutoTokenizer, GPTBigCodeConfig

from ...compat import DTYPE_MAPPING


class StarCoderModel(nn.Module):
    def __init__(self, config, **kwargs):
        super().__init__()

        tokenizer_name = (
            kwargs.get("tokenizer_name")
            or getattr(config, "_name_or_path", None)
            or config.starcoder_model_name
        )
        self.init_tokenizer(
            tokenizer_name, vocab_size=int(getattr(config, "vocab_size", 0) or 0)
        )

        self.max_length = int(getattr(config, "max_length_train", 8192))
        model_config = self._build_model_config(config)
        model_config._attn_implementation = "eager"

        # The StarVector checkpoint carries the fine-tuned LLM; building from
        # config avoids downloading (and needing access to) the gated base
        # repo. If this fails there is nothing sensible to fall back to.
        model: nn.Module = AutoModelForCausalLM.from_config(model_config)
        input_embeddings = model.get_input_embeddings()
        if input_embeddings is not None and input_embeddings.weight.shape[0] != len(self.tokenizer):
            model.resize_token_embeddings(len(self.tokenizer))
        # ``resize_token_embeddings`` recria o lm_head e desfaz o tying; o
        # checkpoint só tem ``transformer.wte.weight``.
        model.tie_weights()

        precision = kwargs.get("model_precision", getattr(config, "torch_dtype", None))
        if isinstance(precision, str):
            precision = DTYPE_MAPPING.get(precision)
        if isinstance(precision, torch.dtype) and precision != torch.float32:
            model = model.to(dtype=precision)
        self.transformer = model

        # Prompt the model after the image embeddings.
        self.prompt = "<svg"

    def _build_model_config(self, config) -> GPTBigCodeConfig:
        hidden_size = int(getattr(config, "hidden_size", 2048))
        return GPTBigCodeConfig(
            vocab_size=int(getattr(config, "vocab_size", 49156)),
            n_positions=int(getattr(config, "max_position_embeddings", 8192)),
            n_embd=hidden_size,
            n_layer=int(getattr(config, "num_hidden_layers", 24)),
            n_head=int(getattr(config, "num_attention_heads", 16)),
            n_inner=int(getattr(config, "n_inner", 4 * hidden_size) or 4 * hidden_size),
            activation_function="gelu_pytorch_tanh",
            resid_pdrop=0.1,
            embd_pdrop=0.1,
            attn_pdrop=0.1,
            layer_norm_epsilon=1e-5,
            initializer_range=0.02,
            scale_attn_weights=True,
            use_cache=True,
            multi_query=bool(getattr(config, "multi_query", True)),
            # O checkpoint só tem ``transformer.wte.weight``; o lm_head é tied.
            tie_word_embeddings=True,
            bos_token_id=self.tokenizer.bos_token_id,
            eos_token_id=self.tokenizer.eos_token_id,
            pad_token_id=self.tokenizer.pad_token_id,
        )

    def init_tokenizer(self, model_name: str, *, vocab_size: int = 0) -> None:
        # ``trust_remote_code=False`` é obrigatório: o repo do StarVector tem
        # auto_map e o hub pergunta se pode correr o custom code (no worker o
        # stdin é o canal JSONL — um prompt corrompia o protocolo).
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            use_fast=False,
            trust_remote_code=False,
            clean_up_tokenization_spaces=False,
        )
        if self.tokenizer.eos_token_id is None:
            self.tokenizer.add_special_tokens({"eos_token": "[EOS]"})
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.add_special_tokens({"pad_token": "[PAD]"})

        self.svg_start_token = "<svg-start>"
        self.image_start_token = "<image-start>"
        self.text_start_token = "<caption-start>"
        self.tokenizer.add_tokens(
            [self.svg_start_token, self.image_start_token, self.text_start_token]
        )
        # O checkpoint tem ``vocab_size`` linhas; se o tokenizer carregado for
        # mais curto, acrescenta tokens não usados para o load não dar mismatch.
        missing = vocab_size - len(self.tokenizer)
        if missing > 0:
            self.tokenizer.add_tokens([f"<svg-unused-{i}>" for i in range(missing)])
        self.svg_start_token_id = self.tokenizer.encode(self.svg_start_token)[0]
