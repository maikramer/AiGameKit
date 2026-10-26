"""Features FLUX.3 Image Klein — referências, boxes, silhueta, num_images, upsampler.

Cobre o que a migração acrescentou: validação de ``--boxes``, extração de
silhueta (rembg mockado), normalização de referências no generator, contrato da
chamada ``generate`` (refs/boxes/num_images/guidance inerte) e o load modular
(``ModularPipeline`` com pop do bloco ``prompt_upsampler``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from text2d.boxes import parse_boxes, validate_box

# ---------------------------------------------------------------------------
# boxes.py — validação de layout
# ---------------------------------------------------------------------------


def test_parse_boxes_none_returns_empty() -> None:
    assert parse_boxes(None) == []


def test_parse_boxes_inline_json() -> None:
    raw = json.dumps([{"id": "balloon", "desc": "a balloon", "bbox": [0.1, 0.2, 0.3, 0.4]}])
    boxes = parse_boxes(raw)
    assert boxes == [{"id": "balloon", "desc": "a balloon", "bbox": [0.1, 0.2, 0.3, 0.4]}]


def test_parse_boxes_from_file(tmp_path: Path) -> None:
    f = tmp_path / "boxes.json"
    f.write_text(json.dumps([{"id": "a", "desc": "d", "bbox": [0, 0, 0.5, 0.5]}]), encoding="utf-8")
    assert parse_boxes(str(f))[0]["id"] == "a"


def test_parse_boxes_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        parse_boxes("/nao/existe/boxes.json")


def test_parse_boxes_not_a_list_raises(tmp_path: Path) -> None:
    f = tmp_path / "boxes.json"
    f.write_text(json.dumps({"id": "a"}), encoding="utf-8")
    with pytest.raises(ValueError, match="lista"):
        parse_boxes(str(f))


def test_parse_boxes_invalid_json_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_boxes("[{nao é json")


@pytest.mark.parametrize("missing", ["id", "desc", "bbox"])
def test_validate_box_missing_required(missing: str) -> None:
    box = {"id": "a", "desc": "d", "bbox": [0, 0, 1, 1]}
    del box[missing]
    with pytest.raises(ValueError, match=missing):
        validate_box(box)


@pytest.mark.parametrize(
    "bbox",
    [
        [0, 0, 1],  # tamanho errado
        [0, 0, 1.2, 1],  # fora de 0-1
        [-0.1, 0, 1, 1],  # negativo
        [0.5, 0.5, 0.2, 0.6],  # y invertido
        [0.1, 0.5, 0.4, 0.2],  # x invertido
        [0.1, 0.1, 0.1, 0.2],  # vazio
        ["a", 0, 1, 1],  # não numérico
    ],
)
def test_validate_box_bad_bbox(bbox: Any) -> None:
    with pytest.raises(ValueError, match="bbox"):
        validate_box({"id": "a", "desc": "d", "bbox": bbox})


def test_validate_box_accepts_normalized_copy() -> None:
    out = validate_box({"id": "a", "desc": "d", "bbox": [0, 0, 1, 1], "extra": "ignorado"})
    assert "extra" not in out
    assert out["bbox"] == [0.0, 0.0, 1.0, 1.0]


def test_validate_box_from_target() -> None:
    out = validate_box(
        {"id": "cactus", "desc": "d", "bbox": [0, 0, 1, 1], "from": "target", "tgt_bbox": [0.5, 0.5, 0.9, 0.9]}
    )
    assert out["from"] == "target"
    assert out["tgt_bbox"] == [0.5, 0.5, 0.9, 0.9]


def test_validate_box_from_ref_image_valid() -> None:
    out = validate_box({"id": "k", "desc": "d", "bbox": [0, 0, 1, 1], "from": "ref_image_0"}, num_refs=1)
    assert out["from"] == "ref_image_0"


def test_validate_box_from_ref_image_out_of_range() -> None:
    with pytest.raises(ValueError, match="só há 1"):
        validate_box({"id": "k", "desc": "d", "bbox": [0, 0, 1, 1], "from": "ref_image_2"}, num_refs=1)


def test_validate_box_from_unknown_source() -> None:
    with pytest.raises(ValueError, match="ref_image_N"):
        validate_box({"id": "k", "desc": "d", "bbox": [0, 0, 1, 1], "from": "imagem"})


# ---------------------------------------------------------------------------
# silhouette.py — extração (rembg mockado)
# ---------------------------------------------------------------------------


def _fake_cutout(size: tuple[int, int] = (8, 8)) -> Image.Image:
    """RGBA com metade esquerda opaca e metade direita transparente."""
    img = Image.new("RGBA", size, (10, 20, 30, 255))
    alpha = Image.new("L", size, 0)
    for y in range(size[1]):
        for x in range(size[0] // 2):
            alpha.putpixel((x, y), 255)
    img.putalpha(alpha)
    return img


def test_extract_silhouette_binarizes_alpha(monkeypatch: pytest.MonkeyPatch) -> None:
    import text2d.bg_removal as bg

    monkeypatch.setattr(bg, "remove_background", lambda image, session=None: _fake_cutout())
    from text2d.silhouette import extract_silhouette

    sil = extract_silhouette(Image.new("RGB", (8, 8), (200, 0, 0)))
    assert sil.mode == "RGBA"
    assert sil.size == (8, 8)
    # Sujeito branco opaco à esquerda, transparente à direita.
    assert sil.getpixel((0, 0))[:3] == (255, 255, 255)
    assert sil.getpixel((0, 0))[3] == 255
    assert sil.getpixel((7, 0))[3] == 0


def test_extract_silhouette_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    import text2d.bg_removal as bg

    def _semi(image: Image.Image, session: Any = None) -> Image.Image:
        img = Image.new("RGBA", (2, 1), (0, 0, 0, 200))
        return img

    monkeypatch.setattr(bg, "remove_background", _semi)
    from text2d.silhouette import extract_silhouette

    assert extract_silhouette(Image.new("RGB", (2, 1)), threshold=201).getpixel((0, 0))[3] == 0
    assert extract_silhouette(Image.new("RGB", (2, 1)), threshold=200).getpixel((0, 0))[3] == 255


def test_extract_silhouette_file_writes_png(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import text2d.bg_removal as bg

    monkeypatch.setattr(bg, "remove_background", lambda image, session=None: _fake_cutout())
    from text2d.silhouette import extract_silhouette_file

    src = tmp_path / "src.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(src)
    out = extract_silhouette_file(src, tmp_path / "sub" / "sil.png")
    assert out.is_file()
    assert Image.open(out).mode == "RGBA"


# ---------------------------------------------------------------------------
# generator — referências e contrato generate
# ---------------------------------------------------------------------------


def test_resolve_ref_images_missing_file(tmp_path: Path) -> None:
    from text2d.generator import KleinFluxGenerator

    gen = KleinFluxGenerator(device="cpu")
    with pytest.raises(FileNotFoundError, match="não encontrada"):
        gen._resolve_ref_images([tmp_path / "nope.png"])


def test_resolve_ref_images_max_ten(tmp_path: Path) -> None:
    from text2d.generator import KleinFluxGenerator

    gen = KleinFluxGenerator(device="cpu")
    refs = []
    for i in range(11):
        p = tmp_path / f"r{i}.png"
        Image.new("RGB", (4, 4)).save(p)
        refs.append(str(p))
    with pytest.raises(ValueError, match="Máximo de 10"):
        gen._resolve_ref_images(refs)


def test_resolve_ref_images_accepts_pil_and_paths(tmp_path: Path) -> None:
    from text2d.generator import KleinFluxGenerator

    gen = KleinFluxGenerator(device="cpu")
    p = tmp_path / "r.png"
    Image.new("RGB", (4, 4), (9, 9, 9)).save(p)
    out = gen._resolve_ref_images([p, Image.new("RGB", (2, 2), (1, 1, 1))])
    assert out is not None and len(out) == 2
    assert all(isinstance(i, Image.Image) for i in out)


def test_resolve_ref_images_none() -> None:
    from text2d.generator import KleinFluxGenerator

    assert KleinFluxGenerator(device="cpu")._resolve_ref_images(None) is None


class _FakePipe:
    """Pipeline mínimo: regista kwargs e devolve N imagens."""

    def __init__(self, num_images: int = 1) -> None:
        self.calls: list[dict[str, Any]] = []
        self._num = num_images

    def __call__(self, **kwargs: Any) -> list[Image.Image]:
        self.calls.append(kwargs)
        return [Image.new("RGB", (4, 4), (i, i, i)) for i in range(self._num)]


def _gen_with_fake_pipe(monkeypatch: pytest.MonkeyPatch, *, num_images: int = 1) -> tuple[Any, _FakePipe]:
    from text2d.generator import KleinFluxGenerator

    gen = KleinFluxGenerator(device="cpu", memory_efficient=True)
    pipe = _FakePipe(num_images=num_images)
    monkeypatch.setattr(gen, "_load_pipeline", lambda: pipe)
    return gen, pipe


def test_generate_passes_refs_boxes_num_images(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gen, pipe = _gen_with_fake_pipe(monkeypatch, num_images=2)
    gen.upsample = True  # boxes só são consumidas pelo bloco prompt_upsampler
    ref = tmp_path / "ref.png"
    Image.new("RGB", (4, 4)).save(ref)
    boxes = [{"id": "a", "desc": "d", "bbox": [0.1, 0.1, 0.5, 0.5]}]

    image, meta = gen.generate("p", num_inference_steps=8, seed=1, refs=[ref], boxes=boxes, num_images=2)

    call = pipe.calls[0]
    assert call["num_inference_steps"] == 8
    assert call["num_images_per_prompt"] == 2
    assert call["boxes"] == boxes
    assert len(call["image"]) == 1
    assert "guidance_scale" not in call  # distilled: nunca envia CFG
    assert meta["refs"] == 1 and meta["boxes"] == 1 and meta["num_images"] == 2
    assert len(meta["images"]) == 2
    assert image.size == (4, 4)


def test_generate_drops_boxes_without_upsample(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sem o bloco prompt_upsampler o diffusers ignora "boxes" — não as enviar.
    gen, pipe = _gen_with_fake_pipe(monkeypatch)
    _, meta = gen.generate("p", num_inference_steps=8, seed=1, boxes=[{"id": "a", "desc": "d", "bbox": [0, 0, 1, 1]}])
    assert "boxes" not in pipe.calls[0]
    assert meta["boxes"] == 0


def test_generate_auto_size_when_wh_none(monkeypatch: pytest.MonkeyPatch) -> None:
    gen, pipe = _gen_with_fake_pipe(monkeypatch)
    ref = Image.new("RGB", (4, 4))
    gen.generate("p", refs=[ref], num_inference_steps=4)
    call = pipe.calls[0]
    assert "width" not in call and "height" not in call


def test_generate_explicit_wh_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    gen, pipe = _gen_with_fake_pipe(monkeypatch)
    gen.generate("p", width=768, height=512, num_inference_steps=4)
    call = pipe.calls[0]
    assert call["width"] == 768 and call["height"] == 512


def test_generate_guidance_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    gen, pipe = _gen_with_fake_pipe(monkeypatch)
    gen.generate("p", guidance_scale=7.5, num_inference_steps=4)
    assert "guidance_scale" not in pipe.calls[0]
    assert gen._guidance_warned is True


def test_generate_rejects_bad_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    gen, _ = _gen_with_fake_pipe(monkeypatch)
    with pytest.raises(ValueError, match="Parâmetros inválidos"):
        gen.generate("p", width=100, height=100, num_inference_steps=4)


def test_generate_warns_steps_outside_trained_range(monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    gen, pipe = _gen_with_fake_pipe(monkeypatch)
    gen.verbose = True
    gen.generate("p", num_inference_steps=32)
    assert pipe.calls[0]["num_inference_steps"] == 32


def test_generate_defaults_16_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    gen, pipe = _gen_with_fake_pipe(monkeypatch)
    gen.generate("p")
    assert pipe.calls[0]["num_inference_steps"] == 16


# ---------------------------------------------------------------------------
# load modular — pop do upsampler + load_components
# ---------------------------------------------------------------------------


class _FakeBlocks:
    def __init__(self) -> None:
        self.sub_blocks: dict[str, Any] = {"prompt_upsampler": object(), "text_encoder": object()}

    def init_pipeline(self, repo: str) -> Any:
        blocks = self

        class _Pipe:
            def __init__(self) -> None:
                self.blocks = blocks
                self.loaded: dict[str, Any] = {}
                self.transformer = None
                self.text_encoder = None
                self.vae = None

            def load_components(self, **kwargs: Any) -> None:
                self.loaded = kwargs
                self.transformer = lambda *a, **k: None
                self.text_encoder = lambda *a, **k: None
                self.vae = lambda *a, **k: None

            def to(self, device: Any) -> None:
                self.device = device

        return _Pipe()


class _FakeModularPipeline:
    def __init__(self) -> None:
        self.blocks = _FakeBlocks()

    @classmethod
    def from_pretrained(cls, repo: str, **kwargs: Any) -> Any:
        inst = cls()
        inst.repo = repo
        inst.kwargs = kwargs
        return inst


def _install_fake_modular(monkeypatch: pytest.MonkeyPatch, *, upsample: bool) -> Any:
    import diffusers

    from text2d import generator as gen_mod

    monkeypatch.setattr(diffusers, "ModularPipeline", _FakeModularPipeline)
    monkeypatch.setattr(gen_mod.KleinFluxGenerator, "_preflight_download", lambda self: None)
    # Sem quantização (CPU plan) e sem GO.
    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    gen = gen_mod.KleinFluxGenerator(device="cpu", group_offload=False, upsample=upsample)
    # _place_with_planner em CPU usa _cpu_plan e chama pipe.to("cpu").
    pipe = gen._load_pipeline()
    return gen, pipe


def test_load_modular_pops_upsampler_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _, pipe = _install_fake_modular(monkeypatch, upsample=False)
    assert "prompt_upsampler" not in pipe.blocks.sub_blocks
    assert pipe.loaded["dtype"]["vae"] is not None
    assert pipe.loaded["dtype"]["default"] is not None


def test_load_modular_keeps_upsampler_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    _, pipe = _install_fake_modular(monkeypatch, upsample=True)
    assert "prompt_upsampler" in pipe.blocks.sub_blocks


def test_load_modular_passes_cache_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    import diffusers

    from text2d import generator as gen_mod

    seen: dict[str, Any] = {}

    class _P(_FakeModularPipeline):
        @classmethod
        def from_pretrained(cls, repo: str, **kwargs: Any) -> Any:
            seen["cache_dir"] = kwargs.get("cache_dir")
            return super().from_pretrained(repo, **kwargs)

    monkeypatch.setattr(diffusers, "ModularPipeline", _P)
    monkeypatch.setattr(gen_mod.KleinFluxGenerator, "_preflight_download", lambda self: None)
    gen = gen_mod.KleinFluxGenerator(device="cpu", group_offload=False, cache_dir="/tmp/cache-x")
    gen._load_pipeline()
    assert seen["cache_dir"] == "/tmp/cache-x"


# ---------------------------------------------------------------------------
# CLI — novas flags viajam no payload vramd
# ---------------------------------------------------------------------------


def _invoke_generate(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> tuple[Any, dict[str, Any]]:
    from text2d import cli

    captured: dict[str, Any] = {}

    def _fake_delegate(backend: str, *, payload: dict, **kwargs: object) -> bool:
        captured["backend"] = backend
        captured["payload"] = payload
        return True

    monkeypatch.setattr(cli, "delegate_or_prepare", _fake_delegate)
    from click.testing import CliRunner

    result = CliRunner().invoke(cli.cli, ["generate", *args])
    return result, captured


def test_cli_ref_payload_and_auto_size(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    Image.new("RGB", (4, 4)).save(ref)
    result, captured = _invoke_generate(monkeypatch, ["edit this", "--ref", str(ref), "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 0, result.output
    assert captured["payload"]["refs"] == [str(ref)]
    # Com referência e sem -W/-H explícitos: tamanho auto (não força o 1024 do tier).
    assert "width" not in captured["payload"]
    assert "height" not in captured["payload"]


def test_cli_ref_with_explicit_wh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    Image.new("RGB", (4, 4)).save(ref)
    result, captured = _invoke_generate(
        monkeypatch, ["edit", "--ref", str(ref), "-W", "768", "-H", "512", "-o", str(tmp_path / "o.png")]
    )
    assert result.exit_code == 0, result.output
    assert captured["payload"]["width"] == 768
    assert captured["payload"]["height"] == 512


def test_cli_more_than_ten_refs_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = ["p", "-o", str(tmp_path / "o.png")]
    for i in range(11):
        p = tmp_path / f"r{i}.png"
        Image.new("RGB", (4, 4)).save(p)
        args += ["--ref", str(p)]
    result, captured = _invoke_generate(monkeypatch, args)
    assert result.exit_code != 0
    assert "Máximo de 10" in result.output
    assert "payload" not in captured


def test_cli_num_images_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result, captured = _invoke_generate(monkeypatch, ["p", "-n", "3", "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 0, result.output
    assert captured["payload"]["num_images"] == 3


def test_cli_upsample_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result, captured = _invoke_generate(monkeypatch, ["p", "--upsample", "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 0, result.output
    assert captured["payload"]["upsample"] is True


def test_cli_upsample_off_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result, captured = _invoke_generate(monkeypatch, ["p", "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 0, result.output
    assert captured["payload"]["upsample"] is False


def test_cli_boxes_inline_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    boxes = json.dumps([{"id": "balloon", "desc": "a balloon", "bbox": [0.1, 0.1, 0.5, 0.5]}])
    result, captured = _invoke_generate(
        monkeypatch, ["p", "--boxes", boxes, "--upsample", "-o", str(tmp_path / "o.png")]
    )
    assert result.exit_code == 0, result.output
    assert captured["payload"]["boxes"][0]["id"] == "balloon"


def test_cli_boxes_without_upsample_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    boxes = json.dumps([{"id": "balloon", "desc": "a balloon", "bbox": [0.1, 0.1, 0.5, 0.5]}])
    result, captured = _invoke_generate(monkeypatch, ["p", "--boxes", boxes, "-o", str(tmp_path / "o.png")])
    assert result.exit_code != 0
    assert "--upsample" in result.output
    assert "payload" not in captured


def test_cli_boxes_invalid_rejected_before_delegation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result, captured = _invoke_generate(
        monkeypatch,
        ["p", "--boxes", '[{"id":"a","desc":"d","bbox":[2,0,1,1]}]', "-o", str(tmp_path / "o.png")],
    )
    assert result.exit_code != 0
    assert "--boxes inválido" in result.output
    assert "payload" not in captured


def test_cli_ref_silhouette_extracts_and_passes_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import text2d.silhouette as sil_mod

    src = tmp_path / "hero.png"
    Image.new("RGB", (4, 4)).save(src)
    made: dict[str, Any] = {}

    def _fake_extract(in_path: Any, out_path: Any, *, threshold: int = 128) -> Path:
        made["in"] = str(in_path)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(out_path)
        return Path(out_path)

    monkeypatch.setattr(sil_mod, "extract_silhouette_file", _fake_extract)
    result, captured = _invoke_generate(
        monkeypatch, ["pose", "--ref-silhouette", str(src), "-o", str(tmp_path / "o.png")]
    )
    assert result.exit_code == 0, result.output
    assert made["in"] == str(src)
    refs = captured["payload"]["refs"]
    assert len(refs) == 1 and refs[0].endswith("silhouette.png")


def test_cli_generate_help_lists_flux3_flags() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    for flag in ("--ref", "--boxes", "--ref-silhouette", "--num-images", "--upsample"):
        assert flag in r.output


def test_cli_silhouette_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from click.testing import CliRunner

    import text2d.bg_removal as bg
    from text2d.cli import cli

    monkeypatch.setattr(bg, "remove_background", lambda image, session=None: _fake_cutout())
    src = tmp_path / "src.png"
    Image.new("RGB", (8, 8), (4, 4, 4)).save(src)
    out = tmp_path / "sil.png"
    r = CliRunner().invoke(cli, ["silhouette", str(src), "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert out.is_file()
    assert Image.open(out).mode == "RGBA"


def test_cli_silhouette_default_output_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from click.testing import CliRunner

    import text2d.bg_removal as bg
    from text2d.cli import cli

    monkeypatch.setattr(bg, "remove_background", lambda image, session=None: _fake_cutout())
    src = tmp_path / "hero.png"
    Image.new("RGB", (8, 8), (4, 4, 4)).save(src)
    r = CliRunner().invoke(cli, ["silhouette", str(src)])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "hero_silhouette.png").is_file()


class _FakeUpsamplePipe(_FakePipe):
    """Devolve dict quando o caller pede múltiplos outputs (caption do pup)."""

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        images = [Image.new("RGB", (4, 4), (i, i, i)) for i in range(self._num)]
        if isinstance(kwargs.get("output"), (list, tuple)):
            return {"images": images, "prompt": "an expanded scene caption"}
        return images


def test_generate_upsample_requests_and_exposes_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    from text2d.generator import KleinFluxGenerator

    gen = KleinFluxGenerator(device="cpu", memory_efficient=True, upsample=True)
    pipe = _FakeUpsamplePipe()
    monkeypatch.setattr(gen, "_load_pipeline", lambda: pipe)
    image, meta = gen.generate("short prompt", num_inference_steps=4)
    assert pipe.calls[0]["output"] == ["images", "prompt"]
    assert meta["caption"] == "an expanded scene caption"
    assert image.size == (4, 4)


def test_generate_without_upsample_has_no_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    gen, pipe = _gen_with_fake_pipe(monkeypatch)
    _, meta = gen.generate("p", num_inference_steps=4)
    assert pipe.calls[0]["output"] == "images"
    assert "caption" not in meta
