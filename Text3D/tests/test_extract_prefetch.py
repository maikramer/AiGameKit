"""Testes do iterador de chunks com prefetch (path CPU síncrono, sem GPU)."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("trimesh")


def _base_extractor():
    from text3d.hy3dshape_paths import ensure_hy3dshape_on_path

    ensure_hy3dshape_on_path(quiet=True)
    from hy3dshape.models.autoencoders.inference_utils.extract_geometry_base import (
        BaseGeometryExtractor,
    )

    class _Dummy(BaseGeometryExtractor):
        def extract_geometry(self, sdf_func, **kwargs):  # pragma: no cover
            raise NotImplementedError

    return _Dummy(device=torch.device("cpu"))


class TestIterQueryChunks:
    def test_cpu_roundtrip_preserves_data(self):
        ext = _base_extractor()
        samples = torch.arange(30, dtype=torch.float32).reshape(10, 3)
        chunks = list(ext.iter_query_chunks(samples, 4, disable_tqdm=True))
        assert [c.shape[0] for c in chunks] == [4, 4, 2]
        assert torch.equal(torch.cat(chunks, dim=0), samples)

    def test_single_chunk(self):
        ext = _base_extractor()
        samples = torch.zeros(5, 3)
        chunks = list(ext.iter_query_chunks(samples, 100, disable_tqdm=True))
        assert len(chunks) == 1
        assert chunks[0].shape == (5, 3)

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA indisponível")
    def test_cuda_prefetch_roundtrip(self):
        from text3d.hy3dshape_paths import ensure_hy3dshape_on_path

        ensure_hy3dshape_on_path(quiet=True)
        from hy3dshape.models.autoencoders.inference_utils.extract_geometry_base import (
            BaseGeometryExtractor,
        )

        class _Dummy(BaseGeometryExtractor):
            def extract_geometry(self, sdf_func, **kwargs):  # pragma: no cover
                raise NotImplementedError

        ext = _Dummy(device=torch.device("cuda"))
        samples = torch.randn(1000, 3)
        chunks = list(ext.iter_query_chunks(samples, 128, disable_tqdm=True))
        assert all(c.is_cuda for c in chunks)
        got = torch.cat([c.cpu() for c in chunks], dim=0)
        assert torch.allclose(got, samples)
