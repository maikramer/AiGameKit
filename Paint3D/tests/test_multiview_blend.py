"""Blend por vista das referências do Paint3D (sem GPU)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from paint3d.hy3dpaint.textureGenPipeline import _blend_multiview_runs, _default_ref_azims


def _img(value: int) -> Image.Image:
    return Image.new("RGB", (4, 4), (value, value, value))


def _channel(image: Image.Image) -> int:
    return int(np.asarray(image)[0, 0, 0])


def test_default_ref_azims() -> None:
    assert _default_ref_azims(1) == [0.0]
    assert _default_ref_azims(2) == [0.0, 180.0]
    assert _default_ref_azims(4) == [0.0, 180.0, 90.0, 270.0]


def test_two_refs_select_front_and_back() -> None:
    """front/back: as vistas cardinais usam a ref do seu azimute; as laterais 50/50."""
    runs = [
        {"albedo": [_img(10), _img(20), _img(30), _img(40)]},
        {"albedo": [_img(110), _img(120), _img(130), _img(140)]},
    ]
    out = _blend_multiview_runs(runs, [0.0, 90.0, 180.0, 270.0], [0.0, 180.0])["albedo"]
    assert [_channel(i) for i in out] == [10, 70, 130, 90]


def test_four_refs_select_each_cardinal() -> None:
    refs = [0.0, 180.0, 90.0, 270.0]
    runs = [
        {"albedo": [_img(10), _img(20), _img(30), _img(40)]},
        {"albedo": [_img(60), _img(70), _img(80), _img(90)]},
        {"albedo": [_img(110), _img(120), _img(130), _img(140)]},
        {"albedo": [_img(160), _img(170), _img(180), _img(190)]},
    ]
    out = _blend_multiview_runs(runs, [0.0, 90.0, 180.0, 270.0], refs)["albedo"]
    # vista frontal → run da ref frontal; esquerda → ref esquerda; trás → trás; direita → direita
    assert [_channel(i) for i in out] == [10, 120, 80, 190]


# ---------------------------------------------------------------------------
# Validação / normalização das referências (regressões do multi-ref)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from paint3d.hy3dpaint.textureGenPipeline import (  # noqa: E402
    _close_images,
    _load_reference_images,
    _resolve_ref_azims,
)
from paint3d.painter import _copy_reference_images, _save_reference_image  # noqa: E402


class TestResolveRefAzims:
    def test_defaults_follow_reference_count(self) -> None:
        assert _resolve_ref_azims(2, None, blend_refs=True) == [0.0, 180.0]
        assert _resolve_ref_azims(4, None, blend_refs=True) == [0.0, 180.0, 90.0, 270.0]

    def test_no_blend_returns_empty(self) -> None:
        assert _resolve_ref_azims(4, [0, 90, 180, 270], blend_refs=False) == []
        assert _resolve_ref_azims(1, None, blend_refs=True) == []

    def test_normalizes_degrees(self) -> None:
        assert _resolve_ref_azims(2, [-90, 450], blend_refs=True) == [270.0, 90.0]

    def test_accepts_string_payload(self) -> None:
        # Um cliente externo pode mandar o campo como texto.
        assert _resolve_ref_azims(4, "0,180;90,270", blend_refs=True) == [0.0, 180.0, 90.0, 270.0]

    def test_count_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="2 valores para 4"):
            _resolve_ref_azims(4, [0, 180], blend_refs=True)

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError, match="image_azims inválido"):
            _resolve_ref_azims(2, "frente,trás", blend_refs=True)


class TestReferenceLoading:
    def test_opens_and_closes_files(self, tmp_path) -> None:
        path = tmp_path / "ref.png"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(path)
        images, owned = _load_reference_images([str(path)])
        assert images[0].fp is not None
        for image in images:
            image.load()
        _close_images(owned)
        assert images[0].fp is None

    def test_pil_input_is_not_owned(self) -> None:
        ref = Image.new("RGB", (4, 4), (9, 9, 9))
        images, owned = _load_reference_images(ref)
        assert images == [ref]
        assert owned == []

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="lista vazia"):
            _load_reference_images([])

    def test_invalid_item_raises(self) -> None:
        with pytest.raises(ValueError, match="referência inválida"):
            _load_reference_images([object()])

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError, match="image_path inválido"):
            _load_reference_images(object())


class TestBlendValidation:
    def test_azimuth_count_must_match_runs(self) -> None:
        runs = [{"albedo": [_img(10)]}, {"albedo": [_img(20)]}]
        with pytest.raises(ValueError, match="ref_azims"):
            _blend_multiview_runs(runs, [0.0], [0.0])

    def test_run_with_different_view_count_raises(self) -> None:
        runs = [{"albedo": [_img(10), _img(20)]}, {"albedo": [_img(30)]}]
        with pytest.raises(ValueError, match="vistas"):
            _blend_multiview_runs(runs, [0.0, 180.0], [0.0, 180.0])

    def test_run_missing_channel_raises(self) -> None:
        runs = [{"albedo": [_img(10)], "mr": [_img(10)]}, {"albedo": [_img(20)]}]
        with pytest.raises(ValueError, match="canal 'mr'"):
            _blend_multiview_runs(runs, [0.0], [0.0, 180.0])


class TestCopyReferenceImagesAlpha:
    def test_pil_rgba_keeps_alpha(self, tmp_path) -> None:
        """RGBA→RGB aqui virava o fundo transparente em preto opaco.

        O ``textureGenPipeline`` compõe RGBA sobre branco; converter para RGB
        antes de gravar deitava o alfa fora e o paint propagava uma referência
        escura para as vistas (assets do text2d com ``--transparent``).
        """
        ref = Image.new("RGBA", (4, 4), (200, 30, 10, 0))
        dst = tmp_path / "ref.png"
        _save_reference_image(ref, dst)
        saved = Image.open(dst)
        assert saved.mode == "RGBA"
        assert saved.getpixel((0, 0))[3] == 0

    def test_pil_rgb_stays_rgb(self, tmp_path) -> None:
        dst = tmp_path / "ref.png"
        _save_reference_image(Image.new("RGB", (4, 4), (5, 6, 7)), dst)
        assert Image.open(dst).mode == "RGB"

    def test_empty_list_raises(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="vazia"):
            _copy_reference_images([], tmp_path)

    def test_list_returns_strings_for_the_pipeline(self, tmp_path) -> None:
        _, arg = _copy_reference_images([Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4))], tmp_path)
        assert isinstance(arg, list) and len(arg) == 2 and all(isinstance(a, str) for a in arg)
        _, single = _copy_reference_images(Image.new("RGB", (4, 4)), tmp_path)
        assert isinstance(single, str)


class TestPayloadAndAdapterValidation:
    def test_blend_needs_two_images(self) -> None:
        from paint3d.vramd_payload import build_texture_request

        with pytest.raises(ValueError, match="2\\+"):
            build_texture_request(mesh_path="m", image_path="a.png", output="o", blend_refs=True)
        with pytest.raises(ValueError, match="2\\+"):
            build_texture_request(mesh_path="m", image_path="a.png", output="o", images=["a.png"], blend_refs=True)

    def test_azimuth_mismatch_raises(self) -> None:
        from paint3d.vramd_payload import build_texture_request

        with pytest.raises(ValueError, match="valores para 2 imagens"):
            build_texture_request(
                mesh_path="m",
                image_path="a.png",
                output="o",
                images=["a.png", "b.png"],
                blend_refs=True,
                image_azims=[0.0],
            )

    def test_payload_normalizes_azimuths(self) -> None:
        from paint3d.vramd_payload import build_texture_request

        req = build_texture_request(
            mesh_path="m",
            image_path="a.png",
            output="o",
            images=["a.png", "b.png"],
            blend_refs=True,
            image_azims="0,-90",
        )
        assert req["images"] == ["a.png", "b.png"]
        assert req["image_azims"] == [0.0, 270.0]
        assert req["blend_refs"] is True


class TestWorkerAdapterRefs:
    def test_images_string_is_split(self) -> None:
        from paint3d.worker_serve_adapter import _normalize_refs

        arg, blend, azims = _normalize_refs({"images": "a.png, b.png", "blend_refs": True, "image_azims": "0,180"})
        assert arg == ["a.png", "b.png"]
        assert blend is True
        assert azims == [0.0, 180.0]

    def test_single_path_keeps_old_contract(self) -> None:
        from paint3d.worker_serve_adapter import _normalize_refs

        arg, blend, azims = _normalize_refs({"image_path": "a.png"})
        assert arg == "a.png"
        assert blend is False and azims is None

    def test_blend_with_one_image_is_rejected(self) -> None:
        from paint3d.worker_serve_adapter import _normalize_refs

        with pytest.raises(ValueError, match="2\\+"):
            _normalize_refs({"image_path": "a.png", "blend_refs": True})

    def test_azimuth_mismatch_is_rejected(self) -> None:
        from paint3d.worker_serve_adapter import _normalize_refs

        with pytest.raises(ValueError, match="valores para 2 imagens"):
            _normalize_refs({"images": ["a.png", "b.png"], "blend_refs": True, "image_azims": [0.0]})

    def test_non_list_images_is_rejected(self) -> None:
        from paint3d.worker_serve_adapter import _normalize_refs

        with pytest.raises(ValueError, match="lista de paths"):
            _normalize_refs({"images": {"a": 1}})


class TestCheckReferenceImages:
    def test_checks_every_reference_and_closes_handles(self, tmp_path, monkeypatch) -> None:
        """A verificação era só na 1.ª ref e o helper deixava o fd aberto."""
        import paint3d.paint_prep as prep
        from paint3d.painter import _check_reference_images

        paths = [tmp_path / f"ref_{i}.png" for i in range(3)]
        for path in paths:
            Image.new("RGB", (4, 4), (128, 128, 128)).save(path)

        seen: list[Image.Image] = []
        monkeypatch.setattr(prep, "check_reference_image", lambda image, logger=None: seen.append(image))
        _check_reference_images(paths)
        assert len(seen) == 3
        # O ``with`` fechou cada imagem antes de a função retornar.
        assert all(image.fp is None for image in seen)


class TestCliMultiRefValidation:
    """``paint3d texture`` recusa um pedido multi-ref inconsistente antes da GPU."""

    @staticmethod
    def _invoke(tmp_path, args: list[str]):
        from click.testing import CliRunner

        from paint3d.cli import cli

        return CliRunner().invoke(cli, ["texture", *args])

    def _files(self, tmp_path) -> tuple[str, str, str]:
        mesh = tmp_path / "m.glb"
        mesh.write_bytes(b"glTF")
        ref_a = tmp_path / "a.png"
        ref_b = tmp_path / "b.png"
        for ref in (ref_a, ref_b):
            Image.new("RGB", (4, 4), (10, 20, 30)).save(ref)
        return str(mesh), str(ref_a), str(ref_b)

    def test_blend_refs_needs_two_images(self, tmp_path) -> None:
        mesh, ref_a, _ref_b = self._files(tmp_path)
        result = self._invoke(tmp_path, [mesh, "-i", ref_a, "--blend-refs", "-o", str(tmp_path / "o.glb")])
        assert result.exit_code != 0
        assert "2+ --image" in result.output

    def test_azimuth_count_mismatch(self, tmp_path) -> None:
        mesh, ref_a, ref_b = self._files(tmp_path)
        result = self._invoke(
            tmp_path,
            [mesh, "-i", ref_a, "-i", ref_b, "--blend-refs", "--image-azims", "0", "-o", str(tmp_path / "o.glb")],
        )
        assert result.exit_code != 0
        assert "valores para 2" in result.output
