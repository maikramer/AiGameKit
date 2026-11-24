"""Testes unitários do passe de close-up de rosto (`paint3d.face_closeup`).

Garante especialmente a convenção de eixos interna do ``MeshRender`` após
``set_mesh``: ``-Z`` (``z_min``) é a coroa da cabeça (linha 0 nas imagens
nvdiffrast) e ``+Z`` (``z_max``) é a sola dos pés (linha H-1).
"""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image

from paint3d.face_closeup import (
    HeadFrame,
    _build_head_blend_mask,
    _match_head_color,
    crop_reference_head,
    detect_humanoid_head,
    resolve_face_closeup_mode,
)


class _DummyRender:
    """Stub leve de ``MeshRender`` em espaço interno (``-Z``=cabeça, ``+Z``=pés, ``+Y``=frente)."""

    def __init__(self, vtx_pos: np.ndarray, alpha_img: np.ndarray, tex_size: tuple[int, int] = (32, 32)) -> None:
        self.device = torch.device("cpu")
        self.vtx_pos = torch.from_numpy(vtx_pos.astype(np.float32))
        self._alpha_img = alpha_img.astype(np.int64)
        self.texture_size = tex_size
        h, w = tex_size
        # Mapeia cada texel (r, c) linearmente de z_min (linha 0 = cabeça) até z_max (linha H-1 = pés).
        z_min = float(vtx_pos[:, 2].min())
        z_max = float(vtx_pos[:, 2].max())
        zs = np.linspace(z_min, z_max, h, dtype=np.float32)
        xs = np.linspace(-0.25, 0.25, w, dtype=np.float32)
        grid_r, grid_c = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        self.tex_grid = torch.from_numpy(np.stack([grid_r.ravel(), grid_c.ravel()], axis=-1).astype(np.int64))
        pos = np.zeros((h * w, 4), dtype=np.float32)
        pos[:, 0] = np.repeat(xs[None, :], h, axis=0).ravel()
        pos[:, 1] = 0.03
        pos[:, 2] = np.repeat(zs[:, None], w, axis=1).ravel()
        pos[:, 3] = 1.0
        self.tex_position = torch.from_numpy(pos)

    def render_alpha(self, elev: float, azim: float, resolution: int = 256, return_type: str = "np") -> np.ndarray:
        return self._alpha_img[None, :, :, None]


def _make_humanoid_stub() -> _DummyRender:
    """Constrói nuvem de vértices + silhueta 256x256 de humanoide (cabeça em -Z, pés em +Z)."""
    pts = []
    # Coroa/cabeça: z em [-0.50, -0.35], raio x ~0.07, centrada em x=+0.02, y=+0.03
    rng = np.random.default_rng(42)
    head = np.column_stack(
        [
            0.02 + rng.uniform(-0.07, 0.07, 200),
            0.03 + rng.uniform(-0.07, 0.07, 200),
            rng.uniform(-0.50, -0.35, 200),
        ]
    )
    # Acessório assimétrico atrás do ombro (estojo de mapas): z em [-0.39, -0.20], x=-0.16, y=-0.09
    tube = np.column_stack(
        [
            -0.16 + rng.uniform(-0.03, 0.03, 120),
            -0.09 + rng.uniform(-0.03, 0.03, 120),
            rng.uniform(-0.39, -0.20, 120),
        ]
    )
    # Tronco/ombros/braços: z em [-0.30, +0.15], x em [-0.40, +0.40]
    torso = np.column_stack(
        [
            rng.uniform(-0.40, 0.40, 300),
            rng.uniform(-0.12, 0.12, 300),
            rng.uniform(-0.30, 0.15, 300),
        ]
    )
    # Pernas/pés: z em [+0.15, +0.50], duas botas afastadas em X (ex.: centro puxado se errado)
    boots = np.column_stack(
        [
            -0.12 + rng.uniform(-0.08, 0.25, 200),
            rng.uniform(-0.10, 0.12, 200),
            rng.uniform(0.15, 0.50, 200),
        ]
    )
    pts = np.vstack([head, tube, torso, boots])

    # Silhueta 256x256 com linha 16..240 (h=224): cabeça estreita, pescoço fino, ombros largos, pés estreitos.
    alpha = np.zeros((256, 256), dtype=np.int64)
    alpha[16:42, 114:142] = 1  # cabeça (28 px)
    alpha[42:62, 119:137] = 1  # pescoço (18 px)
    alpha[62:118, 60:196] = 1  # ombros + braços (136 px)
    alpha[118:240, 96:160] = 1  # pernas + pés (64 px < 0.72 * 136)
    return _DummyRender(pts, alpha)


def test_resolve_face_closeup_mode_defaults_and_env(monkeypatch) -> None:
    monkeypatch.delenv("PAINT3D_FACE_CLOSEUP", raising=False)
    assert resolve_face_closeup_mode(None) == "auto"
    assert resolve_face_closeup_mode(True) == "on"
    assert resolve_face_closeup_mode(False) == "off"
    monkeypatch.setenv("PAINT3D_FACE_CLOSEUP", "0")
    assert resolve_face_closeup_mode(True) == "off"
    monkeypatch.setenv("PAINT3D_FACE_CLOSEUP", "on")
    assert resolve_face_closeup_mode(False) == "on"


def test_detect_humanoid_head_targets_z_min_not_boots() -> None:
    render = _make_humanoid_stub()
    head = detect_humanoid_head(render, force=False)
    assert head is not None
    # Em MeshRender, z_min (-0.50) é a coroa da cabeça e z_max (+0.50) são os pés.
    assert head.z_min < 0.0 < head.z_max
    assert head.cam_center[2] < 0.0, "cam_center.z deve apontar para a cabeça (-Z), nunca para os pés (+Z)"
    assert abs(head.cam_center[2] - (head.z_min + 0.10 * (head.z_max - head.z_min))) < 1e-4
    # Centro X da cabeça (+0.02) não deve ser puxado pelo estojo nas costas (-0.16) nem pelas botas.
    assert abs(head.cam_center[0] - 0.02) < 0.03
    # neck_z_hi (perto da cabeça) tem Z menor que neck_z_lo (perto do tronco).
    assert head.z_min < head.neck_z_hi < head.neck_z_lo < 0.0


def test_detect_humanoid_head_rejects_building() -> None:
    rng = np.random.default_rng(7)
    box = rng.uniform([-0.5, -0.5, -0.35], [0.5, 0.5, 0.35], size=(300, 3))
    alpha = np.zeros((256, 256), dtype=np.int64)
    alpha[40:220, 40:216] = 1  # silhueta larga de edifício
    render = _DummyRender(box, alpha)
    assert detect_humanoid_head(render, force=False) is None


def test_blend_mask_covers_head_and_excludes_boots() -> None:
    render = _make_humanoid_stub()
    head = detect_humanoid_head(render, force=False)
    assert head is not None

    max_cos = torch.ones((32, 32, 1), dtype=torch.float32)
    head_trust = torch.ones((32, 32, 1), dtype=torch.bool)
    alpha = _build_head_blend_mask(render, render.tex_position, head, max_cos, head_trust)

    # Linha 2 (coroa/rosto, centro X) deve ter peso alto; linha 28 (botas, +Z) tem de ser estritamente 0.
    assert float(alpha[2, 16, 0]) > 0.9
    assert float(alpha[28, :, 0].max()) == 0.0
    # Acessório distante em X (borda do atlas em x=-0.25) deve ficar fora da máscara radial.
    assert float(alpha[2, 0, 0]) == 0.0


def test_crop_reference_head_focuses_top_of_subject() -> None:
    # Referência sintética: fundo branco, sujeito com cabeça vermelha no topo e botas azuis no fundo.
    img = np.full((400, 400, 3), 255, dtype=np.uint8)
    img[40:100, 180:220] = [220, 40, 40]  # cabeça (topo)
    img[100:360, 150:250] = [40, 60, 220]  # corpo + botas (fundo)
    ref = Image.fromarray(img)

    head = HeadFrame(
        cam_center=(0.0, 0.0, -0.40),
        ortho_scale=0.26,
        head_center_xy=(0.0, 0.0),
        z_min=-0.50,
        z_max=0.50,
        neck_z_hi=-0.355,
        neck_z_lo=-0.305,
        radial_inner=0.10,
        radial_outer=0.15,
    )
    crop = np.asarray(crop_reference_head(ref, head, 128))
    center_rgb = crop[64, 64]
    assert center_rgb[0] > 180 and center_rgb[2] < 80, f"Recorte não centrou na cabeça vermelha: {center_rgb}"


def test_match_head_color_shifts_towards_base_on_transition_ring() -> None:
    base = torch.full((16, 16, 3), 0.60)
    head = torch.full((16, 16, 3), 0.56)
    alpha = torch.zeros((16, 16, 1))
    alpha[:6, :] = 1.0
    alpha[6:10, :] = 0.5
    trust = torch.ones((16, 16), dtype=torch.bool)
    matched = _match_head_color(head, base, alpha, trust, max_shift=0.06)
    assert abs(float(matched[0, 0, 0]) - 0.60) < 1e-4
