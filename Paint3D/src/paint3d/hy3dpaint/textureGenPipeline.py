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

import contextlib
import math
import os
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image

from aigamekit_shared.bpy_mesh import load_glb

from .DifferentiableRenderer.MeshRender import MeshRender
from .utils.image_super_utils import imageSuperNet
from .utils.multiview_utils import multiviewDiffusionNet
from .utils.pipeline_utils import ViewProcessor, merge_albedo_mr_views, split_baked_albedo_mr
from .utils.simplify_mesh_utils import remesh_mesh
from .utils.uvwrap_utils import mesh_uv_wrap


def _default_ref_azims(n: int) -> list[float]:
    """Azimutes canónicos para N referências na ordem front/back/left/right."""
    if n <= 1:
        return [0.0]
    if n == 2:
        return [0.0, 180.0]
    if n == 4:
        return [0.0, 180.0, 90.0, 270.0]
    return [i * 360.0 / n for i in range(n)]


def _resolve_ref_azims(n_refs: int, image_azims: Any, *, blend_refs: bool) -> list[float]:
    """Valida/deriva os azimutes das referências **antes** do unwrap do mesh.

    ``image_azims`` aceita lista de números ou string ``"0,180,90,270"`` (um
    cliente externo pode mandar o campo como texto no payload). A contagem tem
    de bater com o número de imagens: sem esta validação o erro só aparecia no
    fim do ``uv_unwrap`` + seleção de vistas, minutos depois de começar.
    """
    if not blend_refs or n_refs <= 1:
        return []
    if image_azims is None:
        return _default_ref_azims(n_refs)
    if isinstance(image_azims, str):
        chunks = [chunk.strip() for chunk in image_azims.replace(";", ",").split(",")]
        values: list[Any] = [chunk for chunk in chunks if chunk]
    else:
        try:
            values = list(image_azims)
        except TypeError as exc:
            raise ValueError(f"image_azims inválido: {image_azims!r}") from exc
    try:
        azims = [float(value) % 360.0 for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"image_azims inválido: {image_azims!r}") from exc
    if len(azims) != n_refs:
        raise ValueError(f"image_azims tem {len(azims)} valores para {n_refs} referências")
    return azims


def _load_reference_images(image_path: Any) -> tuple[list[Image.Image], list[Image.Image]]:
    """Normaliza ``image_path`` em lista de PIL.

    Devolve também as imagens que **nós** abrimos (ficheiros) para o caller as
    fechar: em Windows o ``TemporaryDirectory`` do painter não apaga ficheiros
    com handle aberto — um multi-ref esgotava descritores e a limpeza do
    ``tempdir`` falhava depois do paint todo.
    """
    opened: list[Image.Image] = []
    if isinstance(image_path, (list, tuple)):
        images: list[Image.Image] = []
        if not image_path:
            raise ValueError("image_path lista vazia — o paint precisa de 1+ referências")
        for item in image_path:
            if isinstance(item, (str, Path)):
                image = Image.open(item)
                opened.append(image)
                images.append(image)
            elif isinstance(item, Image.Image):
                images.append(item)
            else:
                raise ValueError(f"referência inválida: {type(item).__name__}")
        return images, opened
    if isinstance(image_path, (str, Path)):
        image = Image.open(image_path)
        return [image], [image]
    if isinstance(image_path, Image.Image):
        return [image_path], []
    raise ValueError(f"image_path inválido: {type(image_path).__name__}")


def _close_images(images: list[Image.Image]) -> None:
    """Fecha handles abertos por ``_load_reference_images`` (best-effort)."""
    for image in images:
        with contextlib.suppress(Exception):
            image.close()


def _blend_multiview_runs(
    runs: list[dict[str, list[Image.Image]]],
    view_azims: list[float],
    ref_azims: list[float],
    *,
    sharp: float = 4.0,
) -> dict[str, list[Image.Image]]:
    """Mistura N difusões single-ref por vista, pesando cada ref pelo azimute.

    Cada run viu uma referência diferente (front/back/left/right). Para cada
    vista escolhida pelo bake, o peso de cada ref é
    ``max(0, cos(Δazim)) ** sharp`` normalizado: a vista frontal usa a ref
    frontal, a traseira a ref de trás, as laterais as refs laterais; topo/base
    usam a ref de azimute mais próximo.
    """
    if not runs:
        raise ValueError("runs vazio")
    if len(ref_azims) != len(runs):
        raise ValueError(f"ref_azims tem {len(ref_azims)} valores para {len(runs)} runs")
    if not view_azims:
        raise ValueError("view_azims vazio")
    keys = [k for k in ("albedo", "mr", "hdr") if k in runs[0]]
    if not keys:
        raise ValueError(f"runs sem albedo/mr: {sorted(runs[0])}")
    out: dict[str, list[Image.Image]] = {}
    sizes: dict[str, tuple[int, int]] = {}
    for key in keys:
        n_views = len(runs[0][key])
        if n_views > len(view_azims):
            raise ValueError(f"run '{key}' tem {n_views} vistas para {len(view_azims)} azimutes")
        for idx, run in enumerate(runs):
            if key not in run:
                raise ValueError(f"run {idx} sem canal '{key}'")
            if len(run[key]) != n_views:
                raise ValueError(f"run {idx} tem {len(run[key])} vistas em '{key}', esperado {n_views}")
        blended: list[Image.Image] = []
        for i in range(n_views):
            weights: list[float] = []
            for ref_azim in ref_azims:
                c = math.cos(math.radians(float(view_azims[i]) - float(ref_azim)))
                # cos(90°) dá ~6e-17, não 0: sem o corte, uma vista a 90° de
                # uma ref escolhia-a com peso 1 em vez de empatar com a oposta.
                weights.append(0.0 if c <= 1e-6 else c**sharp)
            total = sum(weights)
            weights = [1.0 / len(ref_azims)] * len(ref_azims) if total <= 0 else [w / total for w in weights]
            arr = None
            for weight, run in zip(weights, runs, strict=True):
                cur = np.asarray(run[key][i], dtype=np.float32)
                if arr is None:
                    sizes[key] = (cur.shape[0], cur.shape[1])
                elif (cur.shape[0], cur.shape[1]) != sizes[key]:
                    raise ValueError(f"run com vista {i} de tamanho {cur.shape[:2]} em '{key}', esperado {sizes[key]}")
                arr = cur * weight if arr is None else arr + cur * weight
            if arr is None:
                raise ValueError("sem runs para misturar")
            blended.append(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)))
        out[key] = blended
    return out


warnings.filterwarnings("ignore")
from diffusers.utils import logging as diffusers_logging

diffusers_logging.set_verbosity(50)


class Hunyuan3DPaintConfig:
    def __init__(self, max_num_view, resolution):
        self.device = "cuda"

        self.multiview_cfg_path = "hy3dpaint/cfgs/hunyuan-paint-pbr.yaml"
        self.custom_pipeline = "hunyuanpaintpbr"
        self.multiview_pretrained_path = "tencent/Hunyuan3D-2.1"
        self.dino_ckpt_path = "facebook/dinov2-giant"
        self.realesrgan_ckpt_path = "ckpt/RealESRGAN_x4plus.pth"

        self.raster_mode = "cr"
        self.bake_mode = "back_sample"
        self.render_size = 1024 * 2
        self.texture_size = 1024 * 4
        self.max_selected_view_num = max_num_view
        self.resolution = resolution
        self.bake_exp = 4
        self.merge_method = "fast"
        # O canal ``mr`` (metallic/roughness) é de baixa frequência: o Real-ESRGAN
        # inventa micro-detalhe que não existe no material e custa o mesmo que o
        # albedo. Por omissão só o albedo passa pelo enhance; ``PAINT3D_ENHANCE_MR=1``
        # repõe o comportamento antigo.
        self.enhance_mr = os.environ.get("PAINT3D_ENHANCE_MR", "0").strip().lower() in ("1", "true", "yes", "on")

        # view selection
        self.candidate_camera_azims = [0, 90, 180, 270, 0, 180]
        self.candidate_camera_elevs = [0, 0, 0, 0, 90, -90]
        self.candidate_view_weights = [1, 0.25, 0.7, 0.25, 0.05, 0.05]

        for azim in range(0, 360, 30):
            self.candidate_camera_azims.append(azim)
            self.candidate_camera_elevs.append(20)
            self.candidate_view_weights.append(0.01)

            self.candidate_camera_azims.append(azim)
            self.candidate_camera_elevs.append(-20)
            self.candidate_view_weights.append(0.01)


class Hunyuan3DPaintPipeline:
    def __init__(self, config=None) -> None:
        self.config = config if config is not None else Hunyuan3DPaintConfig()
        self.models = {}
        self.stats_logs = {}
        self.render = MeshRender(
            default_resolution=self.config.render_size,
            texture_size=self.config.texture_size,
            bake_mode=self.config.bake_mode,
            raster_mode=self.config.raster_mode,
        )
        self.view_processor = ViewProcessor(self.config, self.render)
        self.load_models()

    @property
    def multiview_pipeline(self):
        mv = self.models.get("multiview_model")
        return getattr(mv, "pipeline", None)

    @property
    def vae(self):
        pipe = self.multiview_pipeline
        return getattr(pipe, "vae", None) if pipe is not None else None

    @property
    def unet(self):
        pipe = self.multiview_pipeline
        return getattr(pipe, "unet", None) if pipe is not None else None

    @unet.setter
    def unet(self, value):
        pipe = self.multiview_pipeline
        if pipe is not None:
            pipe.unet = value

    def enable_attention_slicing(self, *args, **kwargs):
        pipe = self.multiview_pipeline
        if pipe is not None and hasattr(pipe, "enable_attention_slicing"):
            return pipe.enable_attention_slicing(*args, **kwargs)
        return None

    def load_models(self):
        torch.cuda.empty_cache()
        # Multiview (diffusers) primeiro: pico de VRAM; Real-ESRGAN depois (ou CPU se low_vram).
        self.models["multiview_model"] = multiviewDiffusionNet(self.config)
        self.models["super_model"] = imageSuperNet(self.config)
        print("Models Loaded.")

    @torch.no_grad()
    def __call__(
        self,
        mesh_path=None,
        image_path=None,
        output_mesh_path=None,
        use_remesh=True,
        save_glb=True,
        step_callback=None,
        blend_refs: bool = False,
        image_azims: list[float] | None = None,
    ):
        """Generate texture for 3D mesh using multiview diffusion.

        ``image_path`` pode ser uma lista (front/back/left/right). Com
        ``blend_refs=True`` corre a difusão uma vez por referência e mistura as
        albedos por vista pelo azimute (``image_azims``); sem blend usa só a
        primeira, como antes.
        """

        def _step(phase, pct):
            if step_callback:
                step_callback(phase, pct)

        image_prompt, owned_images = _load_reference_images(image_path)
        try:
            # Validação dos azimutes ANTES do uv_unwrap/seleção de vistas: um
            # pedido inconsistente (-‑blend-refs com azimutes a menos) falhava
            # minutos depois, já com o mesh processado.
            ref_azims = _resolve_ref_azims(len(image_prompt), image_azims, blend_refs=blend_refs)

            ##########  Style  ###########
            image_caption = "high quality"
            image_style = []
            for image in image_prompt:
                image = image.resize((512, 512))
                if image.mode == "RGBA":
                    white_bg = Image.new("RGB", image.size, (255, 255, 255))
                    white_bg.paste(image, mask=image.getchannel("A"))
                    image = white_bg
                image_style.append(image)
            image_style = [image.convert("RGB") for image in image_style]
        finally:
            # As originais já estão carregadas em memória (resize/convert
            # fizeram load): fechar aqui liberta os handles antes do
            # TemporaryDirectory do painter tentar limpar em Windows.
            _close_images(owned_images)

        _step("uv_unwrap", 0)
        # Process mesh
        path = os.path.dirname(mesh_path)
        if use_remesh:
            processed_mesh_path = os.path.join(path, "white_mesh_remesh.glb")
            remesh_mesh(mesh_path, processed_mesh_path)
        else:
            processed_mesh_path = mesh_path

        # Output path
        if output_mesh_path is None:
            output_mesh_path = os.path.join(path, "textured_mesh.glb")

        if output_mesh_path.endswith(".obj"):
            output_mesh_path = output_mesh_path.replace(".obj", ".glb")

        # Load mesh
        mesh = load_glb(processed_mesh_path)
        _tex = self.config.texture_size
        _tex = int(_tex[0]) if isinstance(_tex, (list, tuple)) else int(_tex)
        mesh = mesh_uv_wrap(mesh, texture_size=_tex)
        self.render.load_mesh(mesh=mesh)
        _step("uv_unwrap", 100)

        ########### View Selection #########
        _step("view_selection", 0)
        selected_camera_elevs, selected_camera_azims, selected_view_weights = self.view_processor.bake_view_selection(
            self.config.candidate_camera_elevs,
            self.config.candidate_camera_azims,
            self.config.candidate_view_weights,
            self.config.max_selected_view_num,
        )

        normal_maps = self.view_processor.render_normal_multiview(
            selected_camera_elevs, selected_camera_azims, use_abs_coor=True
        )
        position_maps = self.view_processor.render_position_multiview(selected_camera_elevs, selected_camera_azims)
        _step("view_selection", 100)

        ##########  Style (já resolvido antes do unwrap)  ###########

        ###########  Multiview  ##########
        _step("multiview_render", 0)
        if blend_refs and len(image_style) > 1:
            # N referências = N difusões completas (custo linear); as vistas
            # normais/posição são calculadas uma vez e reutilizadas.
            print(f"Paint3D: blend de {len(image_style)} referências ({len(image_style)} difusões)")
            runs = [
                self.models["multiview_model"](
                    [ref],
                    normal_maps + position_maps,
                    prompt=image_caption,
                    custom_view_size=self.config.resolution,
                    resize_input=True,
                )
                for ref in image_style
            ]
            multiviews_pbr = _blend_multiview_runs(runs, selected_camera_azims, ref_azims)
        else:
            if len(image_style) > 1:
                # Sem blend o modelo só vê a 1.ª referência (forward_one usa
                # input_images[0:1]); as restantes eram descartadas em silêncio.
                print(f"Paint3D: {len(image_style)} referências sem --blend-refs — a usar só a primeira")
            multiviews_pbr = self.models["multiview_model"](
                image_style,
                normal_maps + position_maps,
                prompt=image_caption,
                custom_view_size=self.config.resolution,
                resize_input=True,
            )
        _step("multiview_render", 100)
        # PAINT3D_DEBUG_DIR: dump das vistas (normal/posição/albedo/mr) + referência
        # para diagnosticar o paint sem reabrir o GLB.
        debug_dir = os.environ.get("PAINT3D_DEBUG_DIR", "").strip()
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            image_style[0].save(os.path.join(debug_dir, "ref.png"))
            for i, (elev, azim) in enumerate(zip(selected_camera_elevs, selected_camera_azims, strict=False)):
                tag = f"{i:02d}_e{elev}_a{azim}"
                normal_maps[i].save(os.path.join(debug_dir, f"normal_{tag}.png"))
                position_maps[i].save(os.path.join(debug_dir, f"position_{tag}.png"))
                multiviews_pbr["albedo"][i].save(os.path.join(debug_dir, f"albedo_{tag}.png"))
                multiviews_pbr["mr"][i].save(os.path.join(debug_dir, f"mr_{tag}.png"))
        ###########  Enhance  ##########
        _step("enhance", 0)
        # Cópia rasa: cada elemento é substituído pelo output do super_model,
        # os PILs originais não são reutilizados — deepcopy era só custo.
        enhance_images = {}
        enhance_images["albedo"] = list(multiviews_pbr["albedo"])
        enhance_images["mr"] = list(multiviews_pbr["mr"])

        for i in range(len(enhance_images["albedo"])):
            enhance_images["albedo"][i] = self.models["super_model"](enhance_images["albedo"][i])
            if self.config.enhance_mr:
                enhance_images["mr"][i] = self.models["super_model"](enhance_images["mr"][i])
        if debug_dir:
            for i, im in enumerate(enhance_images["albedo"]):
                im.save(os.path.join(debug_dir, f"enhanced_{i:02d}.png"))
        _step("enhance", 100)

        ###########  Bake  ##########
        _step("bake", 0)
        for i in range(len(enhance_images["albedo"])):
            enhance_images["albedo"][i] = enhance_images["albedo"][i].resize(
                (self.config.render_size, self.config.render_size)
            )
            enhance_images["mr"][i] = enhance_images["mr"][i].resize((self.config.render_size, self.config.render_size))
        # Um só passe de bake: albedo (3ch) + mr (3ch) concatenados em 6 canais.
        # A rasterização/back-projection é geométrica (idêntica para os dois
        # mapas) — assim as N vistas são projectadas uma vez em vez de duas. A
        # soma ponderada por canal é linear, logo o resultado é igual ao dos
        # dois bakes separados.
        merged_views = merge_albedo_mr_views(enhance_images["albedo"], enhance_images["mr"])
        texture6, mask = self.view_processor.bake_from_multiview(
            merged_views, selected_camera_elevs, selected_camera_azims, selected_view_weights
        )
        mask_np = (mask.squeeze(-1).cpu().numpy() * 255).astype(np.uint8)
        mask_mr_np = mask_np
        texture, texture_mr = split_baked_albedo_mr(texture6)

        _step("bake", 100)
        if debug_dir:
            Image.fromarray((texture.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)).save(
                os.path.join(debug_dir, "tex_baked.png")
            )
            Image.fromarray(mask_np).save(os.path.join(debug_dir, "tex_trust.png"))

        ##########  inpaint  ###########
        _step("inpaint", 0)
        texture = self.view_processor.texture_inpaint(texture, mask_np)
        self.render.set_texture(texture, force_set=True)
        if "mr" in enhance_images:
            texture_mr = self.view_processor.texture_inpaint(texture_mr, mask_mr_np)
            self.render.set_texture_mr(texture_mr)
        _step("inpaint", 100)
        if debug_dir:
            Image.fromarray((texture.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)).save(
                os.path.join(debug_dir, "tex_inpainted.png")
            )

        _step("save", 0)
        self.render.save_mesh(output_mesh_path, downsample=False)
        _step("save", 100)

        return output_mesh_path
