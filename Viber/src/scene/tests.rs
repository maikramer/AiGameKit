//! Testes da API de edição: cada operação, a sua inversa (undo), lotes atómicos, redo e o
//! roundtrip pelo disco.

use std::collections::BTreeMap;

use super::doc::tests::fixture;
use super::doc::{NodeRef, SceneDoc};
use super::ops::{EditOp, History, apply_batch, parse_ops};
use super::{describe, find, list};
use crate::xml::emit::emit_document;

fn load() -> (tempfile::TempDir, SceneDoc) {
    let tmp = tempfile::tempdir().unwrap();
    let doc = SceneDoc::load(&fixture(tmp.path())).unwrap();
    (tmp, doc)
}

/// Snapshot comparável de todos os ficheiros.
fn snapshot(doc: &SceneDoc) -> Vec<String> {
    doc.files
        .iter()
        .map(|file| emit_document(&file.doc))
        .collect()
}

fn set(node: &str, pairs: &[(&str, Option<&str>)]) -> EditOp {
    EditOp::Set {
        node: node.into(),
        attrs: pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.map(str::to_owned)))
            .collect::<BTreeMap<_, _>>(),
    }
}

/// Aplica, verifica, desfaz e confirma que o documento voltou ao ponto de partida.
fn roundtrip(ops: &[EditOp], check: impl FnOnce(&SceneDoc)) {
    let (_tmp, mut doc) = load();
    let before = snapshot(&doc);
    let mut history = History::default();
    history.apply(&mut doc, "test", ops).unwrap();
    check(&doc);
    let after = snapshot(&doc);
    history.undo(&mut doc).unwrap().expect("undo");
    assert_eq!(snapshot(&doc), before, "undo devolve o documento original");
    history.redo(&mut doc).unwrap().expect("redo");
    assert_eq!(snapshot(&doc), after, "redo repete exatamente");
}

#[test]
fn set_changes_and_removes_attributes() {
    roundtrip(
        &[set(
            "name=hero",
            &[
                ("translation", Some("1 2 3")),
                ("scale", Some("2")),
                ("name", None),
            ],
        )],
        |doc| {
            let hero = doc.node(&NodeRef::new(0, vec![0])).unwrap();
            assert_eq!(hero.attr("translation"), Some("1 2 3"));
            assert_eq!(hero.attr("scale"), Some("2"));
            assert_eq!(hero.attr("name"), None);
        },
    );
}

#[test]
fn set_on_file_root_edits_root_attributes() {
    roundtrip(&[set("#", &[("static", Some("true"))])], |doc| {
        assert_eq!(doc.attr(&NodeRef::root(0), "static"), Some("true"));
    });
}

#[test]
fn set_text_replaces_direct_text() {
    roundtrip(
        &[EditOp::SetText {
            node: "name=hero".into(),
            text: "olá".into(),
        }],
        |doc| assert_eq!(doc.node(&NodeRef::new(0, vec![0])).unwrap().text, "olá"),
    );
}

#[test]
fn insert_several_elements_at_index() {
    roundtrip(
        &[EditOp::Insert {
            parent: "name=props".into(),
            index: Some(1),
            xml: r#"<Entity name="a" /><Entity name="b"><Cuboid /></Entity>"#.into(),
        }],
        |doc| {
            let props = doc.children(&NodeRef::new(0, vec![1])).unwrap();
            let names: Vec<_> = props.iter().map(|n| n.attr("name").unwrap_or("")).collect();
            assert_eq!(names, ["crate", "a", "b", "barrel"]);
            assert_eq!(props[2].children[0].tag, "Cuboid");
        },
    );
}

#[test]
fn insert_into_include_goes_to_the_included_file() {
    roundtrip(
        &[EditOp::Insert {
            parent: "#2".into(),
            index: None,
            xml: r#"<Entity name="cart" />"#.into(),
        }],
        |doc| {
            assert_eq!(doc.children(&NodeRef::root(1)).unwrap().len(), 3);
            assert!(doc.files[1].dirty && !doc.files[0].dirty);
        },
    );
}

#[test]
fn remove_restores_subtree_on_undo() {
    roundtrip(
        &[EditOp::Remove {
            node: "name=props".into(),
        }],
        |doc| {
            assert_eq!(doc.children(&NodeRef::root(0)).unwrap().len(), 2);
            // O Include desceu um índice: o mapa de includes acompanhou.
            assert_eq!(doc.include_target(&NodeRef::new(0, vec![1])), Some(1));
        },
    );
}

#[test]
fn move_within_file_and_back() {
    roundtrip(
        &[EditOp::Move {
            node: "name=hero".into(),
            parent: "name=props".into(),
            index: Some(0),
        }],
        |doc| {
            // hero saiu do índice 0: props passou a #0 e hero é o seu 1.º filho.
            let moved = doc.node(&NodeRef::new(0, vec![0, 0])).unwrap();
            assert_eq!(moved.attr("name"), Some("hero"));
        },
    );
}

#[test]
fn move_across_files() {
    roundtrip(
        &[EditOp::Move {
            node: "name=well".into(),
            parent: "name=props".into(),
            index: None,
        }],
        |doc| {
            assert_eq!(doc.children(&NodeRef::root(1)).unwrap().len(), 1);
            assert_eq!(doc.children(&NodeRef::new(0, vec![1])).unwrap().len(), 3);
            assert!(doc.files[0].dirty && doc.files[1].dirty);
        },
    );
}

#[test]
fn move_into_own_subtree_is_refused() {
    let (_tmp, mut doc) = load();
    let error = apply_batch(
        &mut doc,
        "bad",
        &[EditOp::Move {
            node: "name=props".into(),
            parent: "#1/0".into(),
            index: None,
        }],
    )
    .unwrap_err();
    assert!(format!("{error:#}").contains("dentro de si próprio"));
}

#[test]
fn duplicate_places_copy_after_original() {
    roundtrip(
        &[EditOp::Duplicate {
            node: "name=barrel".into(),
        }],
        |doc| {
            let props = doc.children(&NodeRef::new(0, vec![1])).unwrap();
            assert_eq!(props.len(), 3);
            assert_eq!(props[2].attr("name"), Some("barrel"));
        },
    );
}

#[test]
fn failing_batch_leaves_document_untouched() {
    let (_tmp, mut doc) = load();
    let before = snapshot(&doc);
    let ops = [
        set("name=hero", &[("translation", Some("9 9 9"))]),
        EditOp::Remove {
            node: "name=props".into(),
        },
        EditOp::Remove {
            node: "name=ghost".into(),
        },
    ];
    let error = apply_batch(&mut doc, "partial", &ops).unwrap_err();
    assert!(format!("{error:#}").contains("operação #2"), "{error:#}");
    assert_eq!(snapshot(&doc), before);
}

#[test]
fn redo_is_cleared_by_a_new_edit() {
    let (_tmp, mut doc) = load();
    let mut history = History::default();
    history
        .apply(&mut doc, "a", &[set("name=hero", &[("scale", Some("2"))])])
        .unwrap();
    history.undo(&mut doc).unwrap();
    assert!(history.can_redo());
    history
        .apply(&mut doc, "b", &[set("name=hero", &[("scale", Some("3"))])])
        .unwrap();
    assert!(!history.can_redo());
    assert_eq!(history.undo_label(), Some("b"));
}

#[test]
fn edits_survive_save_and_reload() {
    let (_tmp, mut doc) = load();
    let mut history = History::default();
    history
        .apply(
            &mut doc,
            "edit",
            &[
                set("name=well", &[("translation", Some("5 0 2"))]),
                EditOp::Insert {
                    parent: "#".into(),
                    index: None,
                    xml: r#"<Entity name="new" />"#.into(),
                },
            ],
        )
        .unwrap();
    assert_eq!(doc.save().unwrap(), 2);
    let reloaded = SceneDoc::load(doc.world_path()).unwrap();
    assert_eq!(snapshot(&reloaded), snapshot(&doc));
}

#[test]
fn ops_parse_from_agent_json() {
    let ops = parse_ops(
        r#"[
            {"op": "set", "node": "name=well", "attrs": {"translation": "4 0 3", "scale": null}},
            {"op": "insert", "parent": "world/village.xml#", "xml": "<Entity name=\"cart\" />"},
            {"op": "duplicate", "node": "name=hero"}
        ]"#,
    )
    .unwrap();
    assert_eq!(ops.len(), 3);
    assert!(matches!(&ops[0], EditOp::Set { attrs, .. } if attrs["scale"].is_none()));
    assert_eq!(parse_ops(r#"{"ops": []}"#).unwrap(), vec![]);
    assert!(parse_ops(r#"[{"op": "explode"}]"#).is_err());
    assert!(parse_ops(r##"[{"op": "remove", "node": "#0", "extra": 1}]"##).is_err());
}

#[test]
fn queries_expose_selectors_for_agents() {
    let (_tmp, doc) = load();
    let top = list(&doc, &NodeRef::root(0), 1);
    assert_eq!(top[2]["tag"], "Include");
    assert_eq!(top[2]["has_children"], true);
    let village = list(&doc, &NodeRef::new(0, vec![2]), 1);
    assert_eq!(village[0]["selector"], "world/village.xml#0");
    let deep = list(&doc, &NodeRef::root(0), 2);
    assert_eq!(deep[1]["children"][1]["attrs"]["name"], "barrel");
    let well = describe(&doc, &doc.resolve("name=well").unwrap());
    assert_eq!(well["attrs"]["translation"], "4 0 2");
    let crates = find(&doc, Some("entity"), Some(("name", "crate")), 10);
    assert_eq!(crates.as_array().unwrap().len(), 2);
    let named = find(&doc, None, Some(("name", "")), 2);
    assert_eq!(named.as_array().unwrap().len(), 2, "limite respeitado");
}

#[test]
fn parsed_entities_point_back_to_their_xml_nodes() {
    let (_tmp, doc) = load();
    let loaded = crate::xml::include::load_world(doc.world_path()).unwrap();
    let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();

    fn find<'a>(
        specs: &'a [crate::recipes::EntitySpec],
        name: &str,
    ) -> Option<&'a crate::recipes::EntitySpec> {
        specs.iter().find_map(|spec| {
            (spec.name.as_deref() == Some(name))
                .then_some(spec)
                .or_else(|| find(&spec.children, name))
        })
    }
    for (name, selector) in [
        ("hero", "#0"),
        ("barrel", "#1/1"),
        ("well", "world/village.xml#0"),
    ] {
        let spec = find(&parsed.entities, name).unwrap_or_else(|| panic!("{name} parseado"));
        let source = spec.source.as_ref().expect("origem carimbada");
        assert_eq!(
            doc.node_ref_for(source),
            Some(doc.resolve(selector).unwrap()),
            "{name}"
        );
        assert_eq!(
            *doc.origin_of(&doc.resolve(selector).unwrap()).file,
            *source.file
        );
    }
}

// ── Fase 3: FileSystem + catálogo Add Node + drag-drop de GLB ─────────────────────────

#[test]
fn file_tree_scans_dirs_first_classifies_and_skips() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("world.xml"), "<world />").unwrap();
    std::fs::write(root.join("Zebra.xml"), "<world />").unwrap();
    std::fs::write(root.join("ant.xml"), "<world />").unwrap();
    std::fs::write(root.join(".hidden.xml"), "<world />").unwrap();
    std::fs::create_dir_all(root.join("scripts")).unwrap();
    std::fs::write(root.join("scripts/a.lua"), "--").unwrap();
    std::fs::create_dir_all(root.join("target/debug")).unwrap();
    std::fs::write(root.join("target/debug/junk.xml"), "<world />").unwrap();

    let entries = super::files::scan(root, 6);
    // Pastas primeiro, ficheiros por ordem alfabética case-insensitive, hidden/target fora.
    let names: Vec<&str> = entries.iter().map(|e| e.name.as_str()).collect();
    assert_eq!(names, ["scripts", "ant.xml", "world.xml", "Zebra.xml"]);
    let scripts = &entries[0];
    assert_eq!(scripts.kind, super::files::FileKind::Dir);
    assert_eq!(scripts.children[0].kind, super::files::FileKind::Script);
    assert_eq!(entries[2].kind, super::files::FileKind::World);
    assert_eq!(entries[1].kind, super::files::FileKind::Scene);
    assert!(entries[1].kind.is_scene());
}

#[test]
fn file_tree_json_and_filters_are_stable() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("world.static.xml"), "<world />").unwrap();
    std::fs::create_dir_all(root.join("assets/meshes")).unwrap();
    std::fs::write(root.join("assets/meshes/hero.glb"), "").unwrap();
    std::fs::create_dir_all(root.join("bake")).unwrap();
    std::fs::write(root.join("bake/terrain.baked.ahgt"), "").unwrap();
    std::fs::create_dir_all(root.join("bake/veg")).unwrap();
    std::fs::write(root.join("bake/veg/000.vinst"), "").unwrap();

    let entries = super::files::scan(root, 6);
    let json = super::files::to_json(&entries);
    assert_eq!(json[0]["kind"], "dir");
    let mut glbs = Vec::new();
    super::files::paths_of(&entries, super::files::FileKind::Gltf, "", &mut glbs);
    assert_eq!(glbs, ["assets/meshes/hero.glb"]);
    let mut heights = Vec::new();
    super::files::paths_of(
        &entries,
        super::files::FileKind::Heightmap,
        "",
        &mut heights,
    );
    assert_eq!(heights, ["bake/terrain.baked.ahgt"]);
    let mut instances = Vec::new();
    super::files::paths_of(
        &entries,
        super::files::FileKind::Instances,
        "",
        &mut instances,
    );
    assert_eq!(instances, ["bake/veg/000.vinst"]);
    // world.static.xml é ponto de entrada, não cena qualquer
    let mut worlds = Vec::new();
    super::files::paths_of(&entries, super::files::FileKind::World, "", &mut worlds);
    assert_eq!(worlds, ["world.static.xml"]);
}

#[test]
fn catalog_snippets_parse_and_are_accepted_by_the_parser() {
    for template in super::catalog::templates() {
        let nodes = crate::xml::parse_fragment(template.snippet)
            .unwrap_or_else(|error| panic!("{}: {error}", template.tag));
        assert_eq!(nodes.len(), 1, "{}: snippet com um elemento", template.tag);
        let parsed = crate::recipes::parse_world(&[], &nodes)
            .unwrap_or_else(|error| panic!("{}: {error}", template.tag));
        assert!(
            parsed.skipped_tags.is_empty(),
            "{}: tag rejeitada ({:?})",
            template.tag,
            parsed.skipped_tags
        );
        assert!(
            parsed.warnings.is_empty(),
            "{}: {:?}",
            template.tag,
            parsed.warnings
        );
    }
}

#[test]
fn catalog_tags_are_known_and_snippet_fallback_is_generic() {
    for template in super::catalog::templates() {
        assert!(
            crate::recipes::KNOWN_TAGS.contains(&template.tag),
            "{} não está em KNOWN_TAGS",
            template.tag
        );
    }
    assert_eq!(
        super::catalog::template_for("Cuboid").unwrap().tag,
        "cuboid"
    );
    assert_eq!(
        super::catalog::snippet_for("cliff"),
        "<cliff />",
        "tag conhecida fora do catálogo usa o snippet genérico"
    );
}

#[test]
fn terrain_paint_op_applies_saves_sidecar_and_undo_restores() {
    let (tmp, mut doc) = load();
    // o fixture não tem <Terrain>; dou-lhe um com um .ahgt mínimo
    use crate::terrain::heightmap::{AhgtMeta, HeightMapU16};
    let ahgt = tmp.path().join("bake.ahgt");
    let map = HeightMapU16::filled(65, 65, u16::MAX / 2);
    std::fs::write(
        &ahgt,
        map.to_ahgt(&AhgtMeta {
            world_size: Some(64.0),
            max_height: Some(50.0),
            ..Default::default()
        })
        .unwrap(),
    )
    .unwrap();
    // insere <Terrain heightmap="bake.ahgt"> no topo do world.xml
    apply_batch(
        &mut doc,
        "test",
        &[EditOp::Insert {
            parent: "#".into(),
            index: Some(0),
            xml: r#"<Terrain heightmap="bake.ahgt" />"#.into(),
        }],
    )
    .unwrap();

    let op = EditOp::TerrainPaint {
        at: [0.0, 0.0],
        radius: 6.0,
        layer: "dirt_trail".into(),
        amount: 1.0,
    };
    let mut history = History::default();
    let tx = history.apply(&mut doc, "paint", &[op]).unwrap();
    assert_eq!(tx.changes.len(), 1);
    let terrain = doc.terrain.as_ref().unwrap();
    assert!(terrain.paint_dirty);
    assert_eq!(
        terrain.paint.sample(0.0, 0.0).map(|(slot, _)| slot),
        Some(crate::terrain::splat::SLOT_DIRT_TRAIL)
    );
    // camada desconhecida é recusada sem sujar nada
    assert!(
        history
            .apply(
                &mut doc,
                "paint",
                &[EditOp::TerrainPaint {
                    at: [0.0, 0.0],
                    radius: 6.0,
                    layer: "pizza".into(),
                    amount: 1.0,
                }]
            )
            .is_err()
    );
    // gravar escreve o sidecar e limpa as flags
    doc.save().unwrap();
    let sidecar = crate::terrain::paint::paint_path(&ahgt);
    assert!(sidecar.exists(), "{}", sidecar.display());
    assert!(!doc.terrain.as_ref().unwrap().paint_dirty);
    // o undo devolve a camada limpa (e apaga o conteúdo pintado)
    history.undo(&mut doc).unwrap();
    assert_eq!(doc.terrain.as_ref().unwrap().paint.sample(0.0, 0.0), None);
}

#[test]
fn glb_drop_builds_canonical_entity_and_undo_restores() {
    let (_tmp, doc) = load();
    let op = super::catalog::glb_drop(
        &doc,
        "#",
        "/pool/examples/shared-assets/public/assets/meshes/barrel_lod0.glb",
        [1.5, 0.25, -3.0],
    )
    .unwrap();
    roundtrip(&[op], |doc| {
        let barrel = doc.resolve("name=barrel_lod0").unwrap();
        let node = doc.node(&barrel).unwrap();
        assert_eq!(node.attr("translation"), Some("1.5 0.25 -3"));
        let child = doc.children(&barrel).unwrap();
        assert_eq!(child[0].tag.to_ascii_lowercase(), "gltfscene");
        assert_eq!(child[0].attr("url"), Some("/assets/meshes/barrel_lod0.glb"));
    });
}

#[test]
fn glb_drop_into_a_moved_group_lands_at_the_world_point() {
    // `props` deslocado e rodado: a posição MUNDO pedida converte-se para o seu referencial.
    let (_tmp, mut doc) = load();
    apply_batch(
        &mut doc,
        "move props",
        &[set(
            "name=props",
            &[("translation", Some("10 2 0")), ("euler", Some("0 90 0"))],
        )],
    )
    .unwrap();
    let op = super::catalog::glb_drop(
        &doc,
        "name=props",
        "/assets/meshes/rock.glb",
        [10.0, 2.0, -5.0],
    )
    .unwrap();
    apply_batch(&mut doc, "drop", &[op]).unwrap();
    let rock = doc.resolve("name=rock").unwrap();
    let parent = rock.parent().unwrap();
    let world = super::pose::world_frame(&doc, &parent).transform_point3(
        super::pose::read_pose(doc.node(&rock).unwrap())
            .unwrap()
            .translation,
    );
    assert!(
        (world - bevy::math::Vec3::new(10.0, 2.0, -5.0)).length() < 1e-3,
        "{world:?}"
    );
}

#[test]
fn world_frame_follows_includes() {
    // O ficheiro incluído herda o referencial do sítio onde o <Include> está.
    let (_tmp, mut doc) = load();
    apply_batch(
        &mut doc,
        "wrap include",
        &[
            EditOp::Insert {
                parent: "#".into(),
                index: None,
                xml: r#"<Group name="town" translation="100 0 0" />"#.into(),
            },
            EditOp::Move {
                node: "#2".into(),
                parent: "name=town".into(),
                index: None,
            },
        ],
    )
    .unwrap();
    let village_root = NodeRef::root(1);
    let origin =
        super::pose::world_frame(&doc, &village_root).transform_point3(bevy::math::Vec3::ZERO);
    assert_eq!(origin, bevy::math::Vec3::new(100.0, 0.0, 0.0));
}

#[test]
fn world_frame_composes_include_pose() {
    // <Include translation=…> vira Group na expansão: o referencial mundo dos nós do
    // ficheiro incluído passa a incluir essa pose (só a partir do documento, sem engine).
    let (_tmp, mut doc) = load();
    apply_batch(
        &mut doc,
        "move include",
        &[set("#2", &[("translation", Some("10 0 -5"))])],
    )
    .unwrap();
    let well = doc.resolve("world/village.xml#0").unwrap();
    let origin = super::pose::world_frame(&doc, &well).transform_point3(bevy::math::Vec3::ZERO);
    // well (4 0 2) local + include (10 0 -5).
    assert_eq!(origin, bevy::math::Vec3::new(14.0, 0.0, -3.0));
    let village_root = NodeRef::root(1);
    let origin =
        super::pose::world_frame(&doc, &village_root).transform_point3(bevy::math::Vec3::ZERO);
    assert_eq!(origin, bevy::math::Vec3::new(10.0, 0.0, -5.0));
}

#[test]
fn suggest_url_maps_the_public_pool_and_keeps_other_paths() {
    use super::catalog::suggest_url;
    assert_eq!(
        suggest_url("/x/y/shared-assets/public/assets/meshes/a.glb"),
        "/assets/meshes/a.glb"
    );
    assert_eq!(suggest_url("assets/meshes/a.glb"), "assets/meshes/a.glb");
    assert_eq!(suggest_url("/tmp/drop/b.glb"), "/tmp/drop/b.glb");
}

#[test]
fn canonicalize_rewrites_aliases_once_and_keeps_the_pose() {
    let (_tmp, mut doc) = load();
    apply_batch(
        &mut doc,
        "messy",
        &[
            set(
                "name=hero",
                &[
                    ("translation", None),
                    ("pos", Some("1.000001 2 3")),
                    ("rotation", Some("0 0.70710678 0 0.70710678")),
                ],
            ),
            set(
                "#1/0",
                &[("transform", Some("pos: 4 0 0; rotation: 0 -180 0"))],
            ),
        ],
    )
    .unwrap();
    let before: Vec<_> = ["name=hero", "#1/0"]
        .iter()
        .map(|sel| {
            let node = doc.resolve(sel).unwrap();
            super::pose::read_pose(doc.node(&node).unwrap()).unwrap()
        })
        .collect();
    let out = super::tools::canonicalize(&doc, None).unwrap();
    assert!(out.ops.len() >= 2, "{}", out.ops.len());
    apply_batch(&mut doc, &out.label, &out.ops).unwrap();
    let hero = doc
        .node(&doc.resolve("name=hero").unwrap())
        .unwrap()
        .clone();
    assert_eq!(hero.attr("pos"), None);
    assert_eq!(hero.attr("rotation"), None);
    assert_eq!(hero.attr("translation"), Some("1 2 3"));
    assert_eq!(hero.attr("euler"), Some("0 90 0"));
    for (sel, old) in ["name=hero", "#1/0"].iter().zip(before) {
        let now = super::pose::read_pose(doc.node(&doc.resolve(sel).unwrap()).unwrap()).unwrap();
        assert!(now.translation.distance(old.translation) < 1e-3, "{sel}");
        assert!(now.rotation.dot(old.rotation).abs() > 1.0 - 1e-5, "{sel}");
    }
    // Idempotente: a segunda passagem não tem nada a fazer.
    assert!(
        super::tools::canonicalize(&doc, None)
            .unwrap()
            .ops
            .is_empty()
    );
    // Nós sem pose autoral não ganham atributos.
    assert_eq!(
        doc.node(&doc.resolve("name=props").unwrap())
            .unwrap()
            .attr("translation"),
        None
    );
}
