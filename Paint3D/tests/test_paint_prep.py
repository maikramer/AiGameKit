"""Tests for paint_prep (inpaint mask restriction, depth bias, fill — no GPU)."""

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np


def test_restrict_inpaint_mask_keeps_far_holes() -> None:
    from paint3d.paint_prep import restrict_inpaint_mask

    mask = np.zeros((32, 32), dtype=np.uint8)
    mask[10:20, 10:20] = 255
    new_mask, far = restrict_inpaint_mask(mask, dilate_px=2)
    assert far[0, 0]
    assert not far[15, 15]
    assert new_mask[15, 15] == 255
    # Near the trusted blob, zeros become inpaint targets (0)
    assert (new_mask == 0).any()


class TestFillFarHoles:
    """Ilhas nunca baked: a cor de fill vem só do que foi realmente pintado.

    Regressão do bug que punha a chapel preta: a média era feita sobre
    ``~far_holes``, que inclui os buracos *near* ainda a zero (só inpaintados a
    seguir). Num bake de cinzento 0.5 com 5% de trust, isso dava ~0.08.
    """

    @staticmethod
    def _bake(trusted_frac: float = 0.05, near_frac: float = 0.27, size: int = 100):
        rng = np.random.default_rng(0)
        h = w = size
        order = rng.permutation(h * w)
        trusted = np.zeros(h * w, dtype=bool)
        far = np.zeros(h * w, dtype=bool)
        n_trust = int(h * w * trusted_frac)
        n_near = int(h * w * near_frac)
        trusted[order[:n_trust]] = True
        far[order[n_trust + n_near :]] = True
        trusted, far = trusted.reshape(h, w), far.reshape(h, w)
        texture = np.zeros((h, w, 3), dtype=np.float32)
        texture[trusted] = 0.5  # bake cinzento; tudo o resto ainda a zero
        return texture, trusted, far

    def test_fill_uses_only_trusted_texels(self) -> None:
        from paint3d.paint_prep import fill_far_holes

        texture, trusted, far = self._bake()
        out = fill_far_holes(texture, far, trusted=trusted)
        assert np.allclose(out[far], 0.5, atol=1e-6)

    def test_fill_is_not_darkened_by_unpainted_holes(self) -> None:
        """O fill não pode ser a média de ``~far_holes`` (inclui zeros)."""
        from paint3d.paint_prep import fill_far_holes

        texture, trusted, far = self._bake()
        buggy = float(texture[~far].mean())
        out = fill_far_holes(texture, far, trusted=trusted)
        assert buggy < 0.2  # a média antiga escurecia ~6x
        assert float(out[far].mean()) > 5 * buggy

    def test_no_trusted_falls_back_to_neutral(self) -> None:
        from paint3d.paint_prep import fill_far_holes

        texture = np.zeros((8, 8, 3), dtype=np.float32)
        far = np.ones((8, 8), dtype=bool)
        out = fill_far_holes(texture, far, trusted=np.zeros((8, 8), dtype=bool))
        assert np.allclose(out, 0.45)

    def test_no_far_holes_returns_input(self) -> None:
        from paint3d.paint_prep import fill_far_holes

        texture = np.zeros((4, 4, 3), dtype=np.float32)
        far = np.zeros((4, 4), dtype=bool)
        assert fill_far_holes(texture, far, trusted=far) is texture

    def test_position_map_picks_nearest_painted_color(self) -> None:
        """Parede interior herda a cor da exterior mais próxima, não a média."""
        from paint3d.paint_prep import fill_far_holes

        texture = np.zeros((1, 4, 3), dtype=np.float32)
        texture[0, 0] = (1.0, 0.0, 0.0)  # trusted em x=0
        texture[0, 3] = (0.0, 0.0, 1.0)  # trusted em x=30
        trusted = np.array([[True, False, False, True]])
        far = np.array([[False, True, True, False]])
        pos = np.array([[[0.0, 0, 0], [1.0, 0, 0], [29.0, 0, 0], [30.0, 0, 0]]], dtype=np.float32)

        out = fill_far_holes(texture, far, trusted=trusted, position_map=pos)
        assert np.allclose(out[0, 1], (1.0, 0.0, 0.0))
        assert np.allclose(out[0, 2], (0.0, 0.0, 1.0))

    def test_torch_tensor_roundtrip(self) -> None:
        torch = __import__("torch")
        from paint3d.paint_prep import fill_far_holes

        texture, trusted, far = self._bake(size=32)
        out = fill_far_holes(torch.as_tensor(texture), far, trusted=trusted)
        assert isinstance(out, torch.Tensor)
        assert torch.allclose(out[far], torch.tensor(0.5), atol=1e-6)


class TestInstallRestrictedInpaint:
    class _FakeRender:
        def __init__(self, pos=None) -> None:
            self.vtx_pos = pos

        def uv_feature_map(self, feat):
            raise RuntimeError("no mesh")

    class _FakeViewProcessor:
        def __init__(self, render) -> None:
            self.render = render
            self.calls: list = []

        def texture_inpaint(self, texture, mask, defualt=None):
            self.calls.append((texture, mask, defualt))
            return texture

    def test_far_islands_get_trusted_color_not_black(self) -> None:
        from paint3d.paint_prep import install_restricted_inpaint

        vp = self._FakeViewProcessor(self._FakeRender())
        install_restricted_inpaint(vp, dilate_px=1)

        mask = np.zeros((64, 64), dtype=np.uint8)
        mask[30:34, 30:34] = 255
        texture = np.zeros((64, 64, 3), dtype=np.float32)
        texture[mask > 0] = 0.6

        out = vp.texture_inpaint(texture, mask)
        far = np.ones((64, 64), dtype=bool)
        far[25:39, 25:39] = False
        assert np.allclose(out[far], 0.6, atol=1e-6)

    def test_default_path_is_untouched(self) -> None:
        from paint3d.paint_prep import install_restricted_inpaint

        vp = self._FakeViewProcessor(self._FakeRender())
        install_restricted_inpaint(vp)
        tex = np.zeros((4, 4, 3), dtype=np.float32)
        vp.texture_inpaint(tex, np.zeros((4, 4), dtype=np.uint8), defualt=[0, 0, 0])
        assert vp.calls[-1][2] == [0, 0, 0]


class TestInstallDepthBias:
    """Tolerância de profundidade do bake: constante do upstream vs. slope-scaled."""

    class _FakeRender:
        depth_bias_base = 3e-3
        depth_bias_slope = 0.0
        depth_bias_max = 0.08

    def test_defaults_enable_slope(self) -> None:
        from paint3d.paint_prep import DEFAULT_DEPTH_BIAS_SLOPE, install_depth_bias

        r = self._FakeRender()
        install_depth_bias(r)
        assert r.depth_bias_slope == DEFAULT_DEPTH_BIAS_SLOPE > 0
        assert r.depth_bias_base == 3e-3  # piso continua o do upstream

    def test_env_zero_restores_upstream_behaviour(self) -> None:
        from paint3d.paint_prep import install_depth_bias

        r = self._FakeRender()
        with patch.dict(os.environ, {"PAINT3D_DEPTH_BIAS_SLOPE": "0"}):
            install_depth_bias(r)
        assert r.depth_bias_slope == 0.0

    def test_env_override_is_used(self) -> None:
        from paint3d.paint_prep import install_depth_bias

        r = self._FakeRender()
        with patch.dict(os.environ, {"PAINT3D_DEPTH_BIAS_SLOPE": "0.2"}):
            install_depth_bias(r)
        assert r.depth_bias_slope == 0.2

    def test_garbage_env_falls_back_to_default(self) -> None:
        from paint3d.paint_prep import DEFAULT_DEPTH_BIAS_SLOPE, install_depth_bias

        r = self._FakeRender()
        with patch.dict(os.environ, {"PAINT3D_DEPTH_BIAS_SLOPE": "muito"}):
            install_depth_bias(r)
        assert r.depth_bias_slope == DEFAULT_DEPTH_BIAS_SLOPE

    def test_tolerance_grows_only_on_edge_on_surfaces(self) -> None:
        """A fórmula do back_project: tolerância ~= base em superfícies de topo."""
        from paint3d.paint_prep import (
            DEFAULT_DEPTH_BIAS_BASE,
            DEFAULT_DEPTH_BIAS_MAX,
            DEFAULT_DEPTH_BIAS_SLOPE,
        )

        def tol(cos: float) -> float:
            raw = DEFAULT_DEPTH_BIAS_BASE + DEFAULT_DEPTH_BIAS_SLOPE * (1.0 / max(cos, 1e-2) - 1.0)
            return min(raw, DEFAULT_DEPTH_BIAS_MAX)

        assert tol(1.0) == DEFAULT_DEPTH_BIAS_BASE  # de frente: sem relaxamento
        assert tol(0.5) > tol(1.0)
        assert tol(0.1) > tol(0.5)
        assert tol(0.001) == DEFAULT_DEPTH_BIAS_MAX  # de perfil: limitado pelo tecto


class TestMeshRenderDepthBiasDefaults:
    def test_vendored_defaults_match_upstream(self) -> None:
        """Sem ``install_depth_bias`` o renderer tem de manter o 3e-3 constante."""
        import inspect

        from paint3d.hy3dpaint.DifferentiableRenderer import MeshRender as mr

        src = inspect.getsource(mr.MeshRender.__init__)
        assert "self.depth_bias_base = 3e-3" in src
        assert "self.depth_bias_slope = 0.0" in src


class TestApplyTopViewWeight:
    """Peso das vistas de topo/baixo no blend do bake."""

    class _Cfg:
        def __init__(self) -> None:
            self.candidate_view_weights = [1, 0.25, 0.7, 0.25, 0.05, 0.05, 0.01]

    def test_default_is_a_noop(self) -> None:
        from paint3d.paint_prep import DEFAULT_TOP_VIEW_WEIGHT, apply_top_view_weight

        cfg = self._Cfg()
        before = list(cfg.candidate_view_weights)
        assert apply_top_view_weight(cfg) == DEFAULT_TOP_VIEW_WEIGHT
        assert cfg.candidate_view_weights == before

    def test_only_top_and_bottom_change(self) -> None:
        from paint3d.paint_prep import apply_top_view_weight

        cfg = self._Cfg()
        apply_top_view_weight(cfg, 0.5)
        assert cfg.candidate_view_weights == [1, 0.25, 0.7, 0.25, 0.5, 0.5, 0.01]

    def test_env_override(self) -> None:
        from paint3d.paint_prep import apply_top_view_weight

        cfg = self._Cfg()
        with patch.dict(os.environ, {"PAINT3D_TOP_VIEW_WEIGHT": "0.4"}):
            assert apply_top_view_weight(cfg) == 0.4
        assert cfg.candidate_view_weights[4] == 0.4

    def test_short_weight_list_is_left_alone(self) -> None:
        from paint3d.paint_prep import DEFAULT_TOP_VIEW_WEIGHT, apply_top_view_weight

        class _Short:
            def __init__(self) -> None:
                self.candidate_view_weights = [1.0, 0.5]

        cfg = _Short()
        assert apply_top_view_weight(cfg, 0.5) == DEFAULT_TOP_VIEW_WEIGHT
        assert cfg.candidate_view_weights == [1.0, 0.5]


class TestSaveGlbVerifyStage:
    """O ``save_glb`` do Paint3D escreve o painted **e** o input do paint.

    O input ainda não tem UVs (o unwrap é do pipeline), por isso verificá-lo
    como ``painted`` produzia um ERROR ``NO_UV`` que não é erro nenhum.
    A escrita é atómica (tmp + os.replace) — o mock cria o tmp.
    """

    def _save(self, path: str, *, verify_stage: str) -> tuple:
        from pathlib import Path as _P

        from paint3d.utils import mesh_io

        def fake_save(objects, output_path, **kw):
            _P(str(output_path)).write_bytes(b"glTF")
            return str(output_path)

        out = None
        with (
            patch.object(mesh_io, "_bpy_save_glb", side_effect=fake_save) as save,
            patch.object(mesh_io, "smooth_shade_scene", create=True),
        ):
            out = mesh_io.save_glb([], path, verify_stage=verify_stage)
        return save.call_args.kwargs["verify_stage"], str(out)

    def test_default_stage_is_painted(self) -> None:
        stage, out = self._save("/tmp/out.glb", verify_stage="painted")
        assert stage == "painted"
        assert out == "/tmp/out.glb"  # tmp foi substituído no caminho final

    def test_input_mesh_stage_is_forwarded(self) -> None:
        stage, out = self._save("/tmp/in.glb", verify_stage="to_paint")
        assert stage == "to_paint"
        assert out == "/tmp/in.glb"

    def test_painter_writes_input_as_to_paint(self) -> None:
        """Regressão: os dois call-sites do input têm de passar to_paint."""
        import inspect

        from paint3d import painter

        src = inspect.getsource(painter)
        assert src.count('save_glb(mesh, mesh_in, verify_stage="to_paint")') == 2
        assert "save_glb(mesh, mesh_in)" not in src


class TestCheckReferenceImage:
    """A referência do paint tem de trazer cor; um blockout cinzento sai escuro."""

    @staticmethod
    def _img(rgb, *, size=(64, 64), subject=0.5):
        from PIL import Image

        arr = np.full((size[1], size[0], 3), 255, dtype=np.uint8)  # fundo branco
        rows = int(size[1] * subject)
        arr[:rows] = np.asarray(rgb, dtype=np.uint8)
        return Image.fromarray(arr)

    class _Log:
        def __init__(self) -> None:
            self.warnings: list[str] = []

        def warn(self, msg: str) -> None:
            self.warnings.append(msg)

    def test_grey_silhouette_warns(self) -> None:
        from paint3d.paint_prep import check_reference_image

        log = self._Log()
        sat = check_reference_image(self._img((128, 128, 128)), logger=log)
        assert sat < 1.0
        assert log.warnings and "saturação" in log.warnings[0]

    def test_legitimately_grey_art_is_not_flagged(self) -> None:
        """Uma árvore morta cinzenta mede ~10 e é arte válida; só o blockout
        (praticamente sem cor) deve gritar."""
        from paint3d.paint_prep import check_reference_image

        log = self._Log()
        sat = check_reference_image(self._img((120, 128, 136)), logger=log)
        assert 6.0 < sat < 30.0
        assert log.warnings == []

    def test_coloured_reference_is_quiet(self) -> None:
        from paint3d.paint_prep import check_reference_image

        log = self._Log()
        sat = check_reference_image(self._img((200, 90, 40)), logger=log)
        assert sat > 12.0
        assert log.warnings == []

    def test_all_white_returns_sentinel(self) -> None:
        from paint3d.paint_prep import check_reference_image

        assert check_reference_image(self._img((255, 255, 255), subject=0.0)) == -1.0

    def test_unreadable_input_does_not_raise(self) -> None:
        from paint3d.paint_prep import check_reference_image

        log = self._Log()
        assert check_reference_image("/nao/existe.png", logger=log) == -1.0
        assert log.warnings


class TestScaleMeshForPaint:
    """Resize-in: bbox grande (muita área de superfície) não cabe no raster —
    escala para o envelope; o caller restaura o tamanho original no fim."""

    class _Obj:
        def __init__(self) -> None:
            self.scale = (1.0, 1.0, 1.0)

    def test_big_extent_scales_down(self, monkeypatch) -> None:
        import numpy as np

        from paint3d import painter

        monkeypatch.setenv("PAINT3D_PAINT_MAX_EXTENT_M", "3.0")
        monkeypatch.setattr(painter, "_get_combined_bounds", lambda objs: (np.zeros(3), np.full(3, 6.0)))
        objs = [self._Obj()]
        f = painter._scale_mesh_for_paint(objs)
        assert abs(f - 0.5) < 1e-6
        assert abs(objs[0].scale[0] - 0.5) < 1e-6

    def test_within_envelope_noop(self, monkeypatch) -> None:
        import numpy as np

        from paint3d import painter

        monkeypatch.setenv("PAINT3D_PAINT_MAX_EXTENT_M", "3.0")
        monkeypatch.setattr(painter, "_get_combined_bounds", lambda objs: (np.zeros(3), np.full(3, 2.5)))
        objs = [self._Obj()]
        assert painter._scale_mesh_for_paint(objs) == 1.0
        assert objs[0].scale == (1.0, 1.0, 1.0)

    def test_env_zero_disables(self, monkeypatch) -> None:
        from paint3d import painter

        monkeypatch.setenv("PAINT3D_PAINT_MAX_EXTENT_M", "0")
        assert painter._scale_mesh_for_paint([]) == 1.0
