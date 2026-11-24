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

import os

import numpy as np
import torch


def _rgb_float_view(image):
    """PIL/np → float32 HxWx3 em 0..1 (tons de cinza replicados em RGB)."""
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=2)
    return arr / 255.0


def merge_albedo_mr_views(albedo_views, mr_views):
    """Concatena albedo (3ch) + mr (3ch) de cada vista num único 6ch float32 0..1.

    O bake por vista é geométrico (rasterização, cos map, boundary) — idêntico
    para os dois mapas. Um só passe de 6 canais projecta as N vistas uma vez em
    vez de duas. O merge ponderado é linear por canal, portanto o resultado é
    igual ao de dois bakes separados.
    """
    return [
        np.concatenate([_rgb_float_view(albedo), _rgb_float_view(mr)], axis=2)
        for albedo, mr in zip(albedo_views, mr_views, strict=False)
    ]


def split_baked_albedo_mr(texture6):
    """Separa o bake 6ch em albedo e mr (3ch contíguos, para o render/inpaint)."""
    return texture6[..., :3].contiguous(), texture6[..., 3:].contiguous()


class ViewProcessor:
    def __init__(self, config, render):
        self.config = config
        self.render = render

    def render_normal_multiview(self, camera_elevs, camera_azims, use_abs_coor=True):
        normal_maps = []
        for elev, azim in zip(camera_elevs, camera_azims, strict=False):
            normal_map = self.render.render_normal(elev, azim, use_abs_coor=use_abs_coor, return_type="pl")
            normal_maps.append(normal_map)

        return normal_maps

    def render_position_multiview(self, camera_elevs, camera_azims):
        position_maps = []
        for elev, azim in zip(camera_elevs, camera_azims, strict=False):
            position_map = self.render.render_position(elev, azim, return_type="pl")
            position_maps.append(position_map)

        return position_maps

    def bake_view_selection(
        self, candidate_camera_elevs, candidate_camera_azims, candidate_view_weights, max_selected_view_num
    ):
        original_resolution = self.render.default_resolution
        # A selecção só precisa de cobertura por triângulo (não de cor): 512²
        # chega e corta ~4× os pixels rasterizados nas ~30 vistas candidatas.
        # Nunca acima da resolução de render pedida (evita subir em GPUs pequenas).
        _res = (
            original_resolution
            if isinstance(original_resolution, (list, tuple))
            else (original_resolution, original_resolution)
        )
        self.render.set_default_render_resolution(min(512, int(_res[0]), int(_res[1])))

        selected_camera_elevs = []
        selected_camera_azims = []
        selected_view_weights = []
        selected_alpha_maps = []
        viewed_tri_idxs = []
        viewed_masks = []

        # 计算每个三角片的面积
        face_areas = self.render.get_face_areas(from_one_index=True)
        total_area = face_areas.sum()
        face_area_ratios = face_areas / total_area

        candidate_view_num = len(candidate_camera_elevs)
        self.render.set_boundary_unreliable_scale(2)

        for elev, azim in zip(candidate_camera_elevs, candidate_camera_azims, strict=False):
            viewed_tri_idx = self.render.render_alpha(elev, azim, return_type="np")
            viewed_tri_idxs.append(set(np.unique(viewed_tri_idx.flatten())))
            viewed_masks.append(viewed_tri_idx[0, :, :, 0] > 0)

        is_selected = [False for _ in range(candidate_view_num)]
        total_viewed_tri_idxs = set()
        total_viewed_area = 0.0

        # Em malhas assentes no chão (edifícios/personagens/props), elev=-90°
        # desperdiçava 1 vista inteira no piso inferior ou sola dos pés.
        # Só entra se o utilizador pedir explicitamente (PAINT3D_ALLOW_BOTTOM_VIEW=1).
        allow_bottom = os.environ.get("PAINT3D_ALLOW_BOTTOM_VIEW", "0").strip().lower() in ("1", "true", "yes", "on")

        def _add_candidate(cidx: int) -> None:
            selected_camera_elevs.append(candidate_camera_elevs[cidx])
            selected_camera_azims.append(candidate_camera_azims[cidx])
            selected_view_weights.append(candidate_view_weights[cidx])
            selected_alpha_maps.append(viewed_masks[cidx])
            is_selected[cidx] = True
            total_viewed_tri_idxs.update(viewed_tri_idxs[cidx])

        # Com >=8 vistas e os 4 cantos 3/4 elevados disponíveis (20° @ 45/135/225/315),
        # ancora o ring simétrico (4 cardinais + 4 diagonais 3/4) — evita viés de
        # um único lado e cobre telhados/beirais/ombros a 98-99.8% da área UV.
        # Com >=9 vistas ancora também o topo (elev=90°).
        # Com <8 vistas ancora as 4 cardinais + topo (se existir) e deixa o guloso
        # escolher as restantes entre vistas úteis (sem desperdiçar em elev=-90°).
        want = int(max_selected_view_num)
        diag_targets = [(20, 45), (20, 135), (20, 225), (20, 315)]
        diag_indices = []
        for te, ta in diag_targets:
            found = next(
                (
                    i
                    for i, (e, a) in enumerate(zip(candidate_camera_elevs, candidate_camera_azims, strict=False))
                    if int(round(e)) == te and int(round(a)) % 360 == ta
                ),
                None,
            )
            if found is not None:
                diag_indices.append(found)

        if want >= 8 and len(diag_indices) == 4 and candidate_view_num >= 8:
            for idx in range(min(4, candidate_view_num)):
                _add_candidate(idx)
            for idx in diag_indices:
                if not is_selected[idx] and len(selected_view_weights) < want:
                    _add_candidate(idx)
            # 9.ª+ vista: topo (elev=90°) se disponível na lista
            if want >= 9:
                top_idx = next(
                    (
                        i
                        for i, e in enumerate(candidate_camera_elevs)
                        if int(round(e)) >= 80 and not is_selected[i]
                    ),
                    None,
                )
                if top_idx is not None:
                    _add_candidate(top_idx)
        else:
            base_count = min(5, want, candidate_view_num)
            for idx in range(base_count):
                _add_candidate(idx)

        total_viewed_area = face_area_ratios[list(total_viewed_tri_idxs)].sum()
        for _iter in range(want - len(selected_view_weights)):
            max_inc = 0
            max_idx = -1

            for idx, (elev, azim, _weight) in enumerate(
                zip(candidate_camera_elevs, candidate_camera_azims, candidate_view_weights, strict=False)
            ):
                if is_selected[idx]:
                    continue
                if not allow_bottom and float(elev) <= -80.0:
                    continue
                new_tri_idxs = viewed_tri_idxs[idx] - total_viewed_tri_idxs
                new_inc_area = face_area_ratios[list(new_tri_idxs)].sum()

                if new_inc_area > max_inc:
                    max_inc = new_inc_area
                    max_idx = idx

            if max_inc > 0.005:
                _add_candidate(max_idx)
                total_viewed_area += max_inc
            else:
                break

        self.render.set_default_render_resolution(original_resolution)

        return selected_camera_elevs, selected_camera_azims, selected_view_weights

    def bake_from_multiview(self, views, camera_elevs, camera_azims, view_weights):
        """Acumulação streaming O(1) VRAM por vista (substitui lista O(N) em VRAM).

        Mantém apenas um acumulador de textura e um acumulador de pesos na GPU,
        libertando cada vista logo após a projeção — desbloqueia render 2048px
        e 8-9 vistas confortavelmente em GPUs de 6 GB.
        """
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        blur_radius = 5
        _blur_k = None
        tex_accum = None
        w_accum = None

        for view, camera_elev, camera_azim, weight in zip(
            views, camera_elevs, camera_azims, view_weights, strict=False
        ):
            project_texture, project_cos_map, project_boundary_map = self.render.back_project(
                view, camera_elev, camera_azim
            )
            project_cos_map = weight * (project_cos_map**self.config.bake_exp)

            if _blur_k is None:
                _blur_k = torch.ones(1, 1, blur_radius, blur_radius, device=project_boundary_map.device) / (
                    blur_radius * blur_radius
                )
            bm_blurred = (
                torch.nn.functional.conv2d(
                    project_boundary_map.permute(2, 0, 1).unsqueeze(0),
                    _blur_k,
                    padding=blur_radius // 2,
                )
                .squeeze(0)
                .permute(1, 2, 0)
            )
            # ``boundary_map`` (sketch_image invertido em MeshRender.back_project) vale
            # 1.0 no interior limpo e 0.0 nas bordas de profundidade — interior recebe
            # peso total (1.0) e bordas de silhueta/oclusão reduzem suavemente até 0.25.
            soft_factor = (0.25 + 0.75 * bm_blurred.clamp(0.0, 1.0)).clamp(0.0, 1.0)
            w = project_cos_map * soft_factor

            if tex_accum is None:
                tex_accum = project_texture * w
                w_accum = w.clone()
            else:
                tex_accum.add_(project_texture * w)
                w_accum.add_(w)

            del project_texture, project_cos_map, project_boundary_map, bm_blurred, w

        if tex_accum is None or w_accum is None:
            raise ValueError("bake_from_multiview recebeu lista de vistas vazia")
        valid = w_accum > 1e-8
        texture = torch.where(valid, tex_accum / w_accum.clamp(min=1e-8), torch.zeros_like(tex_accum))
        return texture, valid

    def texture_inpaint(self, texture, mask, defualt=None):
        if defualt is not None:
            mask = mask.astype(bool)
            inpaint_value = torch.tensor(defualt, dtype=texture.dtype, device=texture.device)
            texture[~mask] = inpaint_value
        else:
            texture_np = self.render.uv_inpaint(texture, mask)
            texture = torch.tensor(texture_np / 255).float().to(texture.device)

        return texture
