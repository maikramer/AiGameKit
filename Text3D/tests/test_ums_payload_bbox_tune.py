"""UMS payload: octree/steps omitidos → size-tune pode subir o eixo."""

from __future__ import annotations

from text3d.ums_payload import build_generate_request


def test_omit_octree_and_steps_when_unset() -> None:
    p = build_generate_request(
        from_image="/i.png",
        output="/o.glb",
        size_m=[10.0, 5.0, 6.0],
        category="building",
        bbox_tune=True,
    )
    assert "octree_resolution" not in p
    assert "steps" not in p
    assert "num_chunks" not in p
    assert p.get("bbox_tune") is True
    assert p.get("size_m") == [10.0, 5.0, 6.0]


def test_explicit_octree_still_in_payload() -> None:
    p = build_generate_request(
        from_image="/i.png",
        output="/o.glb",
        octree_resolution=448,
        steps=40,
        num_chunks=12000,
    )
    assert p["octree_resolution"] == 448
    assert p["steps"] == 40
    assert p["num_chunks"] == 12000
