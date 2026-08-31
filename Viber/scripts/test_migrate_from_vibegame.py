"""Tests for scripts/migrate_from_vibegame.py (VibeGame -> Viber world migration).

Each rule of the conversion has a small inline fixture here; this file is the
executable spec of the migration.  Stdlib + pytest only.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("migrate_from_vibegame", _HERE / "migrate_from_vibegame.py")
mig = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mig  # needed by dataclasses on module lookup
_SPEC.loader.exec_module(mig)


def migrate(content: str, root_attrs: dict[str, str] | None = None) -> tuple[ET.Element, mig.MigrationContext]:
    """Convert a world snippet and return (output root element, context)."""
    ctx = mig.MigrationContext()
    return mig.build_world(content, root_attrs, ctx), ctx


def first(root: ET.Element, tag: str) -> ET.Element:
    found = root.find(f".//{tag}")
    assert found is not None, f"expected a <{tag}> in output:\n{ET.tostring(root, encoding='unicode')}"
    return found


class TestUniversalRenames:
    def test_gameobject_becomes_entity_with_renamed_attrs(self) -> None:
        root, ctx = migrate(
            '<GameObject name="hero" tag="player" pos="1 2 3" scale="2" '
            'script="hero.ts" euler="0 45 0" layer="actors" id="7" />'
        )
        entity = first(root, "Entity")
        assert entity.get("name") == "hero"
        assert entity.get("tag") == "player"
        assert entity.get("translation") == "1 2 3"
        assert entity.get("scale") == "2"
        assert entity.get("script") == "hero.lua"
        assert entity.get("euler") == "0 45 0"
        assert entity.get("layer") is None and entity.get("id") is None
        assert ctx.dropped_attrs["layer"] == 1
        assert ctx.dropped_attrs["id"] == 1

    def test_rotation_three_components_becomes_euler_degrees(self) -> None:
        root, _ = migrate('<GameObject rot="0.5 1.0 1.5" />')
        assert first(root, "Entity").get("euler") == "0.5 1.0 1.5"

    def test_rotation_four_components_stays_quaternion(self) -> None:
        root, _ = migrate('<GameObject rotation="0 0.7071 0 0.7071" />')
        assert first(root, "Entity").get("rotation") == "0 0.7071 0 0.7071"

    def test_place_attr_becomes_translation(self) -> None:
        root, ctx = migrate('<GameObject place="at: 859 281; align-to-terrain: 0" />')
        assert first(root, "Entity").get("translation") == "859 0 281"
        assert ctx.dropped_attrs["place:align-to-terrain"] == 1

    def test_case_insensitive_input_tags_and_attrs(self) -> None:
        root, _ = migrate('<GAMEOBJECT POS="4 5 6" NAME="gate" />')
        entity = first(root, "Entity")
        assert entity.get("translation") == "4 5 6"
        assert entity.get("name") == "gate"


class TestPrimitives:
    def test_box_size_broadcast_and_material(self) -> None:
        root, _ = migrate('<Box size="4" color="#ffffff" metalness="0.2" roughness="0.3" />')
        cuboid = first(root, "Cuboid")
        assert cuboid.get("half-size") == "2 2 2"
        assert cuboid.get("base-color") == "#ffffff"
        assert cuboid.get("metallic") == "0.2"
        assert cuboid.get("roughness") == "0.3"

    def test_box_extents_are_halved(self) -> None:
        root, _ = migrate('<Box size="2 4 6" />')
        assert first(root, "Cuboid").get("half-size") == "1 2 3"

    def test_sphere_radius_from_size(self) -> None:
        root, _ = migrate('<Sphere pos="0 1 0" size="2" color="#123456" />')
        sphere = first(root, "Sphere")
        assert sphere.get("radius") == "1"
        assert sphere.get("translation") == "0 1 0"
        assert sphere.get("base-color") == "#123456"

    def test_cylinder_equal_radii(self) -> None:
        root, _ = migrate('<Cylinder size="2 2 8" />')
        cyl = first(root, "Cylinder")
        assert cyl.get("radius") == "2"
        assert cyl.get("half-height") == "4"

    def test_cylinder_taper_warns_and_uses_top_radius(self) -> None:
        root, ctx = migrate('<Cylinder size="2 1 8" />')
        cyl = first(root, "Cylinder")
        assert cyl.get("radius") == "2"
        comments = [c for c in cyl if not isinstance(c.tag, str)]
        assert any("taper" in (c.text or "") for c in comments)
        assert any("taperado" in note for note in ctx.notes)

    def test_plane_gets_minus_ninety_euler(self) -> None:
        root, _ = migrate('<Plane size="4 6" color="#336699" />')
        plane = first(root, "Plane")
        assert plane.get("half-size") == "2 3"
        assert plane.get("euler") == "-90 0 0"
        assert plane.get("base-color") == "#336699"

    def test_pad_becomes_flat_plane_and_drops_pad_attrs(self) -> None:
        root, ctx = migrate('<Pad size="24 18" edge-feather="0.1" corner-radius="2" edge-noise="0.3" />')
        plane = first(root, "Plane")
        assert plane.get("half-size") == "12 9"
        assert plane.get("euler") is None
        for attr in ("edge-feather", "corner-radius", "edge-noise"):
            assert plane.get(attr) is None
            assert attr in ctx.dropped_attrs

    def test_opacity_and_textures_drop_with_note(self) -> None:
        root, ctx = migrate('<Box size="2 2 2" opacity="0" texture-url="/a.png" normal-map-url="/n.png" />')
        cuboid = first(root, "Cuboid")
        assert cuboid.get("opacity") is None
        assert cuboid.get("texture-url") is None
        assert cuboid.get("normal-map-url") is None
        assert "opacity" in ctx.dropped_attrs
        assert any("opacity < 1" in note for note in ctx.notes)


class TestComposition:
    def test_composition_bridges_into_group_with_local_children(self) -> None:
        root, _ = migrate(
            '<Composition pos="5 0 6" scale="2">'
            '  <Box pos="1 2 3" size="2 2 2" rotation="0.7853981633974483 0 0" color="#ffffff" />'
            '  <ParticleSystem preset="fire" />'
            "</Composition>"
        )
        group = first(root, "Group")
        assert group.get("translation") == "5 0 6"
        assert group.get("scale") == "2"
        cuboid = first(group, "Cuboid")
        assert cuboid.get("translation") == "1 2 3"  # local, parent composes
        assert cuboid.get("euler") == "45 0 0"  # radians -> degrees
        # non-primitive children stay inside the bridge Group, verbatim
        particles = first(group, "ParticleSystem")
        assert particles.get("preset") == "fire"

    def test_composition_child_plane_tilt(self) -> None:
        root, _ = migrate('<Composition><Plane size="4 6" /></Composition>')
        assert first(root, "Plane").get("euler") == "-90 0 0"

    def test_composition_rotation_warns_only_with_offset_primitives(self) -> None:
        root, _ = migrate('<Composition rotation="0 90 0"><Box pos="1 0 0" size="1" /></Composition>')
        group = first(root, "Group")
        comments = [c for c in group if not isinstance(c.tag, str)]
        assert any("rotation != 0" in (c.text or "") for c in comments)

        root2, _ = migrate('<Composition rotation="0 90 0"><Box size="1" /></Composition>')
        comments2 = [c for c in first(root2, "Group") if not isinstance(c.tag, str)]
        assert not any("rotation != 0" in (c.text or "") for c in comments2)

    def test_composition_place_becomes_group_translation(self) -> None:
        root, _ = migrate('<Composition place="at: 857 226; align-to-terrain: 0"><Box size="1" /></Composition>')
        assert first(root, "Group").get("translation") == "857 0 226"


class TestLightsAndCamera:
    def test_pointlight_scale_and_drops(self) -> None:
        root, ctx = migrate('<PointLight pos="0 3 0" color="#ffd9a0" intensity="2.5" distance="13" cast-shadow="1" />')
        light = first(root, "PointLight")
        assert light.get("intensity") == "2000"  # 2.5 x 800
        assert light.get("translation") == "0 3 0"
        assert light.get("shadows") == "true"
        assert light.get("distance") is None
        assert "distance" in ctx.dropped_attrs

    def test_pointlight_prop_string_fills_element(self) -> None:
        root, _ = migrate(
            '<PointLight pos="0 2.2 0" point-light="color: 0xffa040; intensity: 2.4; distance: 13; decay: 2" />'
        )
        light = first(root, "PointLight")
        assert light.get("color") == "0xffa040"
        assert light.get("intensity") == "1920"
        assert light.get("distance") is None and light.get("decay") is None

    def test_thirdpersoncamera_pitch_radians_to_degrees(self) -> None:
        root, ctx = migrate(
            '<ThirdPersonCamera target="hero" distance="3.3" height="1.55" pitch="0.32" '
            'fov="64" mouse-sensitivity="0.003" follow-lag="0.18" />'
        )
        cam = first(root, "OrbitCamera")
        assert cam.get("target") == "hero"
        assert cam.get("distance") == "3.3"
        assert cam.get("height") == "1.55"
        assert cam.get("pitch") == "18.3"
        assert cam.get("fov") is None and cam.get("mouse-sensitivity") is None
        assert "fov" in ctx.dropped_attrs and "follow-lag" in ctx.dropped_attrs


class TestComponentAttrStrings:
    def test_renderer_box_becomes_cuboid_child(self) -> None:
        root, _ = migrate('<GameObject name="crate" pos="1 2 3" renderer="shape: box; size: 2 4 6; color: #ff0000" />')
        entity = first(root, "Entity")
        assert entity.get("renderer") is None
        cuboid = first(entity, "Cuboid")
        assert cuboid.get("half-size") == "1 2 3"
        assert cuboid.get("base-color") == "#ff0000"
        # the primitive inherits the entity transform (no duplicated transform)
        assert cuboid.get("translation") is None

    def test_mesh_renderer_alias_and_sphere(self) -> None:
        root, _ = migrate('<GameObject mesh-renderer="shape: sphere; size: 3; color: #00ff00" />')
        sphere = first(root, "Sphere")
        assert sphere.get("radius") == "1.5"
        assert sphere.get("base-color") == "#00ff00"

    def test_renderer_default_size(self) -> None:
        root, _ = migrate('<GameObject renderer="shape: box" />')
        assert first(root, "Cuboid").get("half-size") == "0.5 0.5 0.5"

    def test_directional_light_attr_string(self) -> None:
        root, ctx = migrate(
            '<GameObject directional-light="color: 0xffffff; intensity: 3; direction: 0 -1 0; cast-shadow: 1" />'
        )
        entity = first(root, "Entity")
        light = first(entity, "DirectionalLight")
        assert light.get("color") == "0xffffff"
        assert light.get("illuminance") == "30000"  # 3 x 10000
        assert light.get("direction") == "0 -1 0"
        assert "directional-light:cast-shadow" in ctx.dropped_attrs

    def test_ambient_light_attr_string(self) -> None:
        root, ctx = migrate('<GameObject ambient-light="skyColor: 0xc8d8e8; groundColor: 0xa89878; intensity: 0.22" />')
        ambient = first(first(root, "Entity"), "AmbientLight")
        assert ambient.get("color") == "0xc8d8e8"
        assert ambient.get("brightness") == "110"  # 0.22 x 500
        assert "ambient-light:ground-color" in ctx.dropped_attrs


class TestGltfScene:
    def test_gltfloader_becomes_gltfscene_with_url_kept(self) -> None:
        root, _ = migrate('<GLTFLoader url="/assets/meshes/hero.glb" pos="1 2 3" scale="1.5" name="hero-mesh" />')
        scene = first(root, "GltfScene")
        assert scene.tag == "GltfScene"
        assert scene.get("url") == "/assets/meshes/hero.glb"
        assert scene.get("translation") == "1 2 3"
        assert scene.get("scale") == "1.5"
        assert scene.get("name") == "hero-mesh"

    def test_gltfloader_rotation_rules(self) -> None:
        root, _ = migrate('<GLTFLoader url="/a.glb" rot="0 45 0" />')
        assert first(root, "GltfScene").get("euler") == "0 45 0"
        root2, _ = migrate('<GLTFLoader url="/a.glb" rotation="0 0.7071 0 0.7071" />')
        assert first(root2, "GltfScene").get("rotation") == "0 0.7071 0 0.7071"

    def test_gltfloader_lod_and_shadow_attrs_drop(self) -> None:
        root, ctx = migrate(
            '<GLTFLoader url="/a.glb" role="visual" lod1-url="/a_lod1.glb" lod2-url="/a_lod2.glb" '
            'lod-threshold-near="53" lod-threshold-mid="130" cast-shadow="1" merge="1" />'
        )
        scene = first(root, "GltfScene")
        assert scene.get("role") is None
        assert scene.get("lod1-url") is None
        assert scene.get("lod-threshold-near") is None
        assert scene.get("cast-shadow") is None
        assert scene.get("merge") is None
        for attr in ("role", "lod1-url", "lod2-url", "lod-threshold-near", "lod-threshold-mid", "cast-shadow", "merge"):
            assert attr in ctx.dropped_attrs

    def test_gltfloader_collider_animation_prefixes_drop(self) -> None:
        root, ctx = migrate(
            '<GLTFLoader url="/a.glb" collider-shape="trimesh" collider-mesh-url="/a_col.glb" '
            'play-animations="idle" animation-speed="1.2" speed="1" visibility="visible" />'
        )
        scene = first(root, "GltfScene")
        assert scene.get("collider-shape") is None
        assert scene.get("play-animations") is None
        for attr in (
            "collider-shape",
            "collider-mesh-url",
            "play-animations",
            "animation-speed",
            "speed",
            "visibility",
        ):
            assert attr in ctx.dropped_attrs

    def test_gltfloader_script_rewritten_and_case_insensitive(self) -> None:
        root, _ = migrate('<gltfloader URL="/a.glb" script="spin.ts" />')
        scene = first(root, "GltfScene")
        assert scene.get("url") == "/a.glb"
        assert scene.get("script") == "spin.lua"


class TestVerbatimTags:
    def test_unknown_tag_passes_verbatim_and_is_counted(self) -> None:
        root, ctx = migrate('<Terrain world-size="4000" pos="1 2 3"><NavMesh /></Terrain>')
        terrain = first(root, "Terrain")
        assert terrain.get("world-size") == "4000"  # attrs untouched
        assert terrain.get("pos") is not None  # NOT renamed outside mapped tags
        assert first(terrain, "NavMesh") is not None
        assert ctx.verbatim_tags["Terrain"] == 1

    def test_include_element_is_kept(self) -> None:
        root, _ = migrate('<Include src="/world/environment.xml"></Include>')
        assert first(root, "Include").get("src") == "/world/environment.xml"


class TestInputFormats:
    def test_index_html_scene_extraction(self) -> None:
        html = (
            "<html><head><title>t</title></head><body>"
            '<canvas id="game-canvas"></canvas>'
            '<Scene canvas="#game-canvas" sky="#8fc2ef" resume-audio-on-user-gesture="true">'
            '  <GameObject name="hero" pos="1 2 3" />'
            "</Scene>"
            '<script type="module" src="/src/main.ts"></script>'
            "</body></html>"
        )
        attrs, content = mig.extract_input(html)
        assert attrs == {"canvas": "#game-canvas", "sky": "#8fc2ef", "resume-audio-on-user-gesture": "true"}
        root, ctx = migrate(content, attrs)
        assert root.tag == "world"
        assert root.get("clear-color") == "#8fc2ef"
        assert root.get("canvas") is None and root.get("resume-audio-on-user-gesture") is None
        assert first(root, "Entity").get("translation") == "1 2 3"
        assert "canvas" in ctx.dropped_attrs

    def test_bare_boolean_attrs_are_normalized(self) -> None:
        assert 'enabled="true"' in mig.normalize_bare_bools("<Fog enabled></Fog>")
        assert 'shadows="true"' in mig.normalize_bare_bools("<PointLight shadows/>")
        # quoted values pass through untouched
        assert 'pos="1 2 3"' in mig.normalize_bare_bools('<Box pos="1 2 3"/>')

    def test_normalizer_is_comment_safe(self) -> None:
        src = '<!-- docs mention <Include src= and <Terrain> here --><Box size="2" />'
        normalized = mig.normalize_bare_bools(src)
        assert normalized.startswith("<!-- docs mention <Include src= and <Terrain> here -->")
        ET.fromstring(f"<w>{normalized}</w>")

    def test_fragment_xml_with_multiple_roots(self) -> None:
        root, ctx = migrate('<Group name="a" pos="0 0 0"></Group><GameObject pos="1 1 1" />')
        assert root.tag == "world"  # fragments are wrapped into a <world> root
        assert root.find("Group") is not None
        assert first(root, "Entity").get("translation") == "1 1 1"
        assert ctx.verbatim_tags == {}

    def test_scene_rooted_xml_uses_scene_attrs(self) -> None:
        root, _ = migrate('<Scene sky="#abcdef"><GameObject pos="0 0 0" /></Scene>')
        assert root.get("clear-color") == "#abcdef"
        assert root.find("Entity") is not None


class TestQuaternionHelpers:
    def test_euler_quat_roundtrip(self) -> None:
        for euler in [(30.0, 45.0, 60.0), (-90.0, 12.0, 33.0), (0.0, 0.0, 0.0)]:
            back = mig.quat_to_euler_deg(mig.quat_from_euler_deg(*euler))
            for got, want in zip(back, euler, strict=True):
                assert math.isclose(got, want, abs_tol=1e-6)

    def test_quat_rotate_z90_maps_x_to_y(self) -> None:
        q = mig.quat_from_euler_deg(0.0, 0.0, 90.0)
        rotated = mig.quat_rotate(q, (1.0, 0.0, 0.0))
        expected = (0.0, 1.0, 0.0)
        for got, want in zip(rotated, expected, strict=True):
            assert math.isclose(got, want, abs_tol=1e-12)

    def test_quat_matches_threejs_yaw_convention(self) -> None:
        # three.js right-handed y-up: yaw +90 makes an object's +Z face +X
        q = mig.quat_from_euler_deg(0.0, 90.0, 0.0)
        rotated = mig.quat_rotate(q, (0.0, 0.0, 1.0))
        for got, want in zip(rotated, (1.0, 0.0, 0.0), strict=True):
            assert math.isclose(got, want, abs_tol=1e-12)

    def test_euler_quat_roundtrip_gimbal_lock(self) -> None:
        back = mig.quat_to_euler_deg(mig.quat_from_euler_deg(0.0, 90.0, 0.0))
        assert math.isclose(back[1], 90.0, abs_tol=1e-6)

    def test_quat_mul_identity(self) -> None:
        q = mig.quat_from_euler_deg(12.0, 34.0, 56.0)
        identity = (1.0, 0.0, 0.0, 0.0)
        for got, want in zip(mig.quat_mul(q, identity), q, strict=True):
            assert math.isclose(got, want, abs_tol=1e-12)


class TestIncludeTree:
    def _make_tree(self, root: Path) -> tuple[Path, Path]:
        public = root / "public"
        (public / "world" / "cities").mkdir(parents=True)
        (public / "world" / "foo.xml").write_text(
            '<Group name="foo" pos="1 0 2">'
            '  <GameObject name="inner" pos="0 3 0" />'
            '  <Include src="cities/bar.xml"></Include>'
            "</Group>",
            encoding="utf-8",
        )
        (public / "world" / "cities" / "bar.xml").write_text(
            '<PointLight pos="0 5 0" intensity="1.5" />', encoding="utf-8"
        )
        index = root / "index.html"
        index.write_text(
            '<html><body><Scene sky="#112233"><Include src="/world/foo.xml"></Include></Scene></body></html>',
            encoding="utf-8",
        )
        return index, public

    def test_include_tree_mirrored_into_outdir(self, tmp_path: Path) -> None:
        index, public = self._make_tree(tmp_path)
        out = tmp_path / "viber-world"
        report = mig.migrate_tree(index, public, out)

        world = ET.parse(out / "world.xml").getroot()
        assert world.get("clear-color") == "#112233"
        assert world.find("Include").get("src") == "/world/foo.xml"  # leading / preserved

        foo = ET.parse(out / "world" / "foo.xml").getroot()
        group = foo.find("Group")
        assert group.get("translation") == "1 0 2"
        assert group.find("Entity").get("translation") == "0 3 0"
        assert group.find("Include").get("src") == "cities/bar.xml"  # relative preserved

        bar = ET.parse(out / "world" / "cities" / "bar.xml").getroot()
        assert bar.find("PointLight").get("intensity") == "1200"  # 1.5 x 800

        # post-order: included files are written before their includer
        assert [f.output for f in report.files] == [
            out / "world" / "cities" / "bar.xml",
            out / "world" / "foo.xml",
            out / "world.xml",
        ]
        assert report.unresolved_includes == []

    def test_include_without_public_is_reported(self, tmp_path: Path) -> None:
        index = tmp_path / "index.html"
        index.write_text('<Scene><Include src="/world/foo.xml"></Include></Scene>', encoding="utf-8")
        report = mig.migrate_tree(index, None, tmp_path / "out")
        assert report.unresolved_includes and "sem --public" in report.unresolved_includes[0]

    def test_missing_include_file_is_reported(self, tmp_path: Path) -> None:
        index = tmp_path / "index.html"
        index.write_text('<Scene><Include src="/world/gone.xml"></Include></Scene>', encoding="utf-8")
        report = mig.migrate_tree(index, tmp_path, tmp_path / "out")
        assert report.unresolved_includes and "não encontrado" in report.unresolved_includes[0]

    def test_include_cycle_terminates(self, tmp_path: Path) -> None:
        public = tmp_path / "public"
        public.mkdir()
        (public / "a.xml").write_text('<Include src="b.xml"></Include>', encoding="utf-8")
        (public / "b.xml").write_text('<Include src="a.xml"></Include>', encoding="utf-8")
        index = tmp_path / "index.html"
        index.write_text('<Scene><Include src="/a.xml"></Include></Scene>', encoding="utf-8")
        report = mig.migrate_tree(index, public, tmp_path / "out")  # must not hang
        assert len(report.files) == 3  # world.xml + a.xml + b.xml, each written once


class TestCli:
    def test_main_writes_default_world_xml(self, tmp_path, capsys):
        index, public = TestIncludeTree()._make_tree(tmp_path)
        out = tmp_path / "out-cli"
        code = mig.main([str(index), "--public", str(public), "-o", str(out)])
        assert code == 0
        assert (out / "world.xml").is_file()
        captured = capsys.readouterr().out
        assert "world.xml" in captured
