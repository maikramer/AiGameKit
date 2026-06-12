# Hunyuan 3D is licensed under the TENCENT HUNYUAN NON-COMMERCIAL LICENSE AGREEMENT
# except for the third-party components listed below.
# Hunyuan 3D does not impose any additional limitations beyond what is outlined
# in the repsective licenses of these third-party components.
# Users must comply with all terms and conditions of original licenses of these third-party
# components and must ensure that the usage of the third party components adheres to
# all relevant laws and regulations.

# For avoidance of doubts, Hunyuan 3D means the large language models and
# their software and algorithms, including trained model weights, parameters (including
# optimizer states), machine-learning model code, inference-enabling code, training-enabling code,
# fine-tuning enabling code and other elements of the foregoing made publicly available
# by Tencent in accordance with TENCENT HUNYUAN COMMUNITY LICENSE AGREEMENT.

import inspect
import os
import random
from typing import List

import huggingface_hub
import numpy as np
import torch
from diffusers import DDIMScheduler, DiffusionPipeline, EulerAncestralDiscreteScheduler, UniPCMultistepScheduler
from omegaconf import OmegaConf
from PIL import Image


def _adopt_vendored_unet_optimizations(pipeline) -> None:
    """Transplanta as otimizações vendored para o código do snapshot HF.

    O ``model_index.json`` do snapshot (tencent/Hunyuan3D-2.1) aponta o
    componente unet para o código embarcado junto dos pesos
    (``unet/modules.py`` + ``unet/attn_processor.py``); o diffusers importa-o
    como dynamic module (``diffusers_modules.local.*``) e ignora o vendored —
    o nosso ``unet/`` só é usado para o que o pipeline.py importa diretamente.
    Pré-semear ``sys.modules`` não funciona (o loader rejeita módulos com spec
    de outro nome), por isso transplantamos as funções otimizadas para as
    classes já carregadas:

    - ``AttnCore.process_attention_base`` → SDPA com SageAttention opcional
    - ``UNet2p5DConditionModel.forward`` → ref-UNet offload + ensure-device

    As funções vendored resolvem globals no módulo vendored, mantendo o
    comportamento ao serem ligadas a classes de outro módulo. As classes do
    snapshot são estruturalmente idênticas (mesmo checkpoint/código upstream).
    """
    import sys

    from ..hunyuanpaintpbr.unet import attn_processor as v_attn
    from ..hunyuanpaintpbr.unet import modules as v_modules

    target = type(pipeline.unet)
    mod_name = target.__module__
    if mod_name.startswith("paint3d."):
        return  # já é o código vendored
    if target.__name__ != "UNet2p5DConditionModel":
        print(
            f"[Paint 2.1] AVISO: UNet inesperado ({mod_name}.{target.__name__}) — "
            "otimizações vendored (sage-attn/offload) não aplicadas."
        )
        return

    target.forward = v_modules.UNet2p5DConditionModel.forward

    snap_mod = sys.modules.get(mod_name)
    snap_core = getattr(snap_mod, "AttnCore", None) if snap_mod is not None else None
    if snap_core is None:
        attn_mod = sys.modules.get(mod_name.rsplit(".", 1)[0] + ".attn_processor")
        snap_core = getattr(attn_mod, "AttnCore", None) if attn_mod is not None else None
    if snap_core is not None:
        snap_core.process_attention_base = v_attn.AttnCore.process_attention_base
    else:
        print("[Paint 2.1] AVISO: AttnCore do snapshot não encontrado — SageAttention inativa no UNet.")


class multiviewDiffusionNet:
    def __init__(self, config) -> None:
        self.device = config.device

        cfg_path = config.multiview_cfg_path
        custom_pipeline = os.path.join(os.path.dirname(__file__), "..", "hunyuanpaintpbr")
        cfg = OmegaConf.load(cfg_path)
        self.cfg = cfg
        self.mode = self.cfg.model.params.stable_diffusion_config.custom_pipeline[2:]

        weights_subfolder = getattr(config, "multiview_weights_subfolder", "hunyuan3d-paintpbr-v2-1")
        model_path = huggingface_hub.snapshot_download(
            repo_id=config.multiview_pretrained_path,
            allow_patterns=[f"{weights_subfolder}/*"],
        )
        model_path = os.path.join(model_path, weights_subfolder)

        pipeline = DiffusionPipeline.from_pretrained(
            model_path,
            custom_pipeline=custom_pipeline,
            torch_dtype=torch.float16,
        )

        _adopt_vendored_unet_optimizations(pipeline)

        pipeline.scheduler = UniPCMultistepScheduler.from_config(pipeline.scheduler.config, timestep_spacing="trailing")
        pipeline.set_progress_bar_config(disable=True)
        pipeline.eval()
        pipeline.view_size = cfg.model.params.get("view_size", 320)

        explicit_quant = getattr(config, "quantization_config", None)
        explicit_quant_type = None
        if isinstance(explicit_quant, dict):
            explicit_quant_type = explicit_quant.get("type")

        # --- Quantização pré-computada (qint8 via optimum-quanto) ---
        self._unet_quantized = False
        from paint3d.utils.unet_quantization import load_unet_quantized, want_quantized_unet

        if explicit_quant_type is None and want_quantized_unet(self.device, model_path):
            try:
                if load_unet_quantized(pipeline, model_path):
                    self._unet_quantized = True
                    print("[Paint 2.1] UNet quantizado (qint8) carregado com sucesso.")
                else:
                    print("[Paint 2.1] Artefactos qint8 em falta; UNet FP16.")
            except Exception as e:
                print(f"[Paint 2.1] AVISO: requantize falhou ({e}); UNet FP16.")

        if explicit_quant_type is not None:
            try:
                if explicit_quant_type.startswith("sdnq"):
                    from sdnq import sdnq_post_load_quant

                    supported = set(inspect.signature(sdnq_post_load_quant).parameters.keys()) - {"model"}
                    sdnq_kwargs = {
                        key: value for key, value in vars(explicit_quant["config"]).items() if key in supported
                    }

                    # IMPORTANTE: Excluir módulos que não devem ser quantizados
                    # para evitar problemas com o pipeline customizado
                    modules_to_skip = [
                        # Módulos de entrada/saída que afetam shape
                        "conv_in",
                        "conv_out",
                        "conv_shortcut",
                        # Time embedding pode causar problemas
                        "time_emb",
                        # Projetações de condicionamento
                        "cond_proj",
                        "context_embedder",
                        # Módulos do wrapper UNet2p5D
                        "image_proj_model_dino",
                        "learned_text_clip",
                    ]

                    # Mesclar com módulos já existentes na config
                    existing_skip = sdnq_kwargs.get("modules_to_not_convert", []) or []
                    sdnq_kwargs["modules_to_not_convert"] = list(existing_skip) + modules_to_skip

                    print(f"[Paint 2.1] Aplicando SDNQ (excluindo: {modules_to_skip})...")
                    pipeline.unet = sdnq_post_load_quant(pipeline.unet, **sdnq_kwargs)
                    self._unet_quantized = True
                    print(f"[Paint 2.1] UNet quantizado com {explicit_quant_type}.")
                elif explicit_quant_type in ("quanto-int8", "quanto-int4"):
                    from optimum.quanto.quantize import freeze, quantize

                    quantize(pipeline.unet, weights=explicit_quant["config"], activations=None)
                    freeze(pipeline.unet)
                    self._unet_quantized = True
                    print(f"[Paint 2.1] UNet quantizado com {explicit_quant_type}.")
                else:
                    if explicit_quant_type not in ("none", None):
                        print(f"[Paint 2.1] Quantização explícita não suportada neste pipeline: {explicit_quant_type}")
            except Exception as e:
                print(f"[Paint 2.1] AVISO: quantização explícita {explicit_quant_type} falhou ({e}); UNet original.")

        if getattr(config, "use_tiny_vae", False):
            try:
                from diffusers import AutoencoderTiny

                tiny_vae_repo = getattr(config, "tiny_vae_repo", "madebyollin/taesdxl")
                pipeline.vae = AutoencoderTiny.from_pretrained(tiny_vae_repo, torch_dtype=torch.float16)
                print(f"[Paint 2.1] TinyVAE carregado de {tiny_vae_repo}.")
            except Exception as e:
                print(f"[Paint 2.1] AVISO: TinyVAE falhou ({e}); VAE original mantido.")

        self.pipeline = pipeline.to(self.device)

        if hasattr(self.pipeline, "enable_vae_slicing"):
            self.pipeline.enable_vae_slicing()
        if hasattr(self.pipeline, "enable_vae_tiling"):
            self.pipeline.enable_vae_tiling()

        # CFG em chunks sequenciais (uncond/ref/full): pico de ativações ÷3,
        # matemática idêntica. Resolvido pelo painter (default: low-VRAM).
        self.pipeline.cfg_batch_chunking = bool(getattr(config, "cfg_batch_chunking", False))

        # Ref-UNet (dual stream) offload para CPU após preencher o cache de
        # condicionamento — só é usado no primeiro step de cada pintura.
        if hasattr(self.pipeline.unet, "use_dual_stream"):
            self.pipeline.unet.offload_ref_unet = bool(getattr(config, "offload_ref_unet", False))

        # DINO: em CPU usa fp32 (fp16 em CPU não tem kernels nativos — lentíssimo);
        # em CUDA usa fp16 (~2.2 GB para o dinov2-giant). Output é movido/castado
        # para o device/dtype do UNet no forward.
        self._dino_device = str(getattr(config, "dino_device", "cpu"))
        if hasattr(self.pipeline.unet, "use_dino") and self.pipeline.unet.use_dino:
            from ..hunyuanpaintpbr.unet.modules import Dino_v2

            dino_dtype = torch.float16 if self._dino_device.startswith("cuda") else torch.float32
            self.dino_v2 = Dino_v2(config.dino_ckpt_path).to(dino_dtype).to(self._dino_device)

    def seed_everything(self, seed):
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        os.environ["PL_GLOBAL_SEED"] = str(seed)

    @torch.no_grad()
    def __call__(self, images, conditions, prompt=None, custom_view_size=None, resize_input=False):
        pils = self.forward_one(
            images, conditions, prompt=prompt, custom_view_size=custom_view_size, resize_input=resize_input
        )
        return pils

    def forward_one(self, input_images, control_images, prompt=None, custom_view_size=None, resize_input=False):
        self.seed_everything(0)
        custom_view_size = custom_view_size if custom_view_size is not None else self.pipeline.view_size
        if not isinstance(input_images, List):
            input_images = [input_images]
        if not resize_input:
            input_images = [
                input_image.resize((self.pipeline.view_size, self.pipeline.view_size)) for input_image in input_images
            ]
        else:
            input_images = [input_image.resize((custom_view_size, custom_view_size)) for input_image in input_images]
        for i in range(len(control_images)):
            control_images[i] = control_images[i].resize((custom_view_size, custom_view_size))
            if control_images[i].mode == "L":
                control_images[i] = control_images[i].point(lambda x: 255 if x > 1 else 0, mode="1")

        kwargs = dict(generator=torch.Generator(device=self.pipeline.device).manual_seed(0))

        num_view = len(control_images) // 2
        normal_image = [[control_images[i] for i in range(num_view)]]
        position_image = [[control_images[i + num_view] for i in range(num_view)]]

        kwargs["width"] = custom_view_size
        kwargs["height"] = custom_view_size
        kwargs["num_in_batch"] = num_view
        kwargs["images_normal"] = normal_image
        kwargs["images_position"] = position_image

        if hasattr(self.pipeline.unet, "use_dino") and self.pipeline.unet.use_dino:
            dino_hidden_states = self.dino_v2(input_images[0])
            if isinstance(dino_hidden_states, torch.Tensor):
                dino_hidden_states = dino_hidden_states.to(torch.device(self.device)).to(self.pipeline.unet.dtype)
            kwargs["dino_hidden_states"] = dino_hidden_states

        sync_condition = None

        infer_steps_dict = {
            "EulerAncestralDiscreteScheduler": 30,
            "UniPCMultistepScheduler": 15,
            "DDIMScheduler": 50,
            "ShiftSNRScheduler": 15,
        }

        mvd_image = self.pipeline(
            input_images[0:1],
            num_inference_steps=infer_steps_dict[self.pipeline.scheduler.__class__.__name__],
            prompt=prompt,
            sync_condition=sync_condition,
            guidance_scale=3.0,
            **kwargs,
        ).images

        if "pbr" in self.mode:
            mvd_image = {"albedo": mvd_image[:num_view], "mr": mvd_image[num_view:]}
        else:
            mvd_image = {"hdr": mvd_image}

        return mvd_image
