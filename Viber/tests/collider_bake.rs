//! Provas do bake de colliders de malha (`src/physics.rs`).
//!
//! # Causa raiz dos 326 `physics: bake falhou` na vila
//!
//! Os `*_collision.glb` do pipeline são **POSITION-only** (um único atributo
//! `POSITION`, sem `NORMAL` — confirmado no chunk JSON do GLB). O loader glTF
//! do bevy 0.19, na ausência de `NORMAL`, faz `mesh.duplicate_vertices()` +
//! `mesh.compute_flat_normals()` (`bevy_gltf-0.19.1/src/loader/mod.rs:826`) —
//! e `duplicate_vertices` **remove o buffer de índices**
//! (`bevy_mesh-0.19.1/src/mesh.rs`: `self.indices.replace(None)`).
//!
//! O caminho antigo, `Collider::from_bevy_mesh`, passa por
//! `extract_mesh_vertices_indices`, que exige `mesh.indices()?` — devolve
//! `None` para TODA essa classe de assets, e o bake falhava para cada prop
//! da vila (as 326 linhas de warn).
//!
//! # Hipótese TopologyError — refutada neste parry
//!
//! No parry3d 0.30.2 (`parry3d-0.30.2-glamx0.2-b`,
//! `src/shape/trimesh.rs`), `TriMesh::with_flags` faz `let _ =
//! result.set_flags(flags)` — **engole** o `TopologyError` (arestas com >2
//! triângulos, winding inconsistente, triângulos degenerados) e só devolve
//! `Err` com `EmptyIndices`. Os testes abaixo provam-no: dois cubos soldados
//! (não-manifold após merge) e triângulos com winding inconsistente bakeam
//! com sucesso com `MERGE_DUPLICATE_VERTICES`. O `TopologyError` só é
//! observável chamando `TriMesh::set_flags` diretamente.

use bevy::asset::RenderAssetUsages;
use bevy::mesh::{Indices, Mesh};
use bevy::prelude::*;
use bevy::render::mesh::PrimitiveTopology;
use bevy_rapier3d::prelude::*;

use viber::physics::{bake_trimesh_escalating, collider_from_mesh, mesh_vertices_indices};

/// GLB real que gerou os 326 warnings (`viber run` do simple-rpg) — servido
/// do pool partilhado (docs/ASSETS.md), já sem espelho por exemplo.
fn crate_glb() -> std::path::PathBuf {
    viber::meshopt::shared_asset_pool()
        .expect("pool partilhado com assets/meshes")
        // wooden_crate saiu do catálogo na regeneração PBR do pool (2026-09-08);
        // o market_stall_collision serve o mesmo contrato (POSITION+índices).
        .join("assets/meshes/village/market_stall_collision.glb")
}

// ------------------------------------------------------------------ helpers

fn tri_mesh(positions: Vec<[f32; 3]>, indices: Option<Vec<u16>>) -> Mesh {
    let mut mesh = Mesh::new(
        PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
    if let Some(indices) = indices {
        mesh.insert_indices(Indices::U16(indices));
    }
    mesh
}

/// Os 8 vértices e 12 triângulos de uma caixa `min..max`, um por face
/// (convenção glTF: CCW visto de fora). O winding não é validado pelo parry
/// sem flags de topologia — serve para geometria saudável e para soldar.
fn box_geometry(min: Vec3, max: Vec3) -> (Vec<[f32; 3]>, Vec<u16>) {
    let v = [
        [min.x, min.y, min.z],
        [max.x, min.y, min.z],
        [max.x, max.y, min.z],
        [min.x, max.y, min.z],
        [min.x, min.y, max.z],
        [max.x, min.y, max.z],
        [max.x, max.y, max.z],
        [min.x, max.y, max.z],
    ];
    let idx: Vec<u16> = vec![
        0, 2, 1, 0, 3, 2, // -Z
        4, 5, 6, 4, 6, 7, // +Z
        0, 1, 5, 0, 5, 4, // -Y
        2, 3, 7, 2, 7, 6, // +Y
        0, 4, 7, 0, 7, 3, // -X
        1, 2, 6, 1, 6, 5, // +X
    ];
    (v.to_vec(), idx)
}

fn to_rapier(positions: &[[f32; 3]], indices: &[u16]) -> (Vec<Vec3>, Vec<[u32; 3]>) {
    let vtx = positions.iter().map(|v| Vec3::from(*v)).collect();
    let tris = indices
        .chunks_exact(3)
        .map(|c| [c[0] as u32, c[1] as u32, c[2] as u32])
        .collect();
    (vtx, tris)
}

// ------------------------------------------------- (a) cubo simples saudável

#[test]
fn simple_indexed_cube_bakes_with_merge_flags() {
    let (verts, idx) = box_geometry(Vec3::NEG_ONE, Vec3::ONE);
    let mesh = tri_mesh(verts.clone(), Some(idx.clone()));

    // O caminho do bevy_rapier (`from_bevy_mesh`, o antigo) bakeia.
    assert!(
        Collider::from_bevy_mesh(
            &mesh,
            &ComputedColliderShape::TriMesh(TriMeshFlags::MERGE_DUPLICATE_VERTICES)
        )
        .is_some(),
        "cubo indexado saudável bakeia com from_bevy_mesh"
    );
    // O caminho nosso (mesh_vertices_indices + escada) também.
    assert!(collider_from_mesh(&mesh, false).is_some());

    // E o parry diretamente, com as flags do 1.º degrau.
    let (vtx, tris) = to_rapier(&verts, &idx);
    assert!(
        Collider::trimesh_with_flags(
            vtx,
            tris,
            TriMeshFlags::MERGE_DUPLICATE_VERTICES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES
        )
        .is_ok()
    );
}

// -------------------------- (b) cubos soldados — hipótese TopologyError

/// Dois cubos a tocar em x=0, cada um com os seus 8 vértices (a face
/// partilhada fica duplicada). Após `MERGE_DUPLICATE_VERTICES` os vértices
/// soldam e as arestas do quadrado de contacto ficam com 4 triângulos
/// incidentes — malha NÃO-manifold, exatamente o cenário da hipótese
/// TopologyError.
fn fused_cubes() -> (Vec<[f32; 3]>, Vec<u16>) {
    let (va, ia) = box_geometry(Vec3::new(-1.5, -0.5, -0.5), Vec3::new(-0.5, 0.5, 0.5));
    let (vb, ib) = box_geometry(Vec3::new(-0.5, -0.5, -0.5), Vec3::new(0.5, 0.5, 0.5));
    let mut verts = va;
    verts.extend(vb);
    let mut idx = ia;
    idx.extend(ib.iter().map(|i| i + 8));
    (verts, idx)
}

#[test]
fn fused_non_manifold_cubes_do_not_fail_with_merge_flags() {
    let (verts, idx) = fused_cubes();
    let mesh = tri_mesh(verts.clone(), Some(idx.clone()));
    let (vtx, tris) = to_rapier(&verts, &idx);

    let result = Collider::trimesh_with_flags(
        vtx.clone(),
        tris.clone(),
        TriMeshFlags::MERGE_DUPLICATE_VERTICES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES,
    );
    assert!(
        result.is_ok(),
        "hipótese TopologyError REFUTADA: parry 0.30.2 engole o erro de topologia — {result:?}"
    );
    assert!(
        collider_from_mesh(&mesh, false).is_some(),
        "o bake nosso também não falha para cubos soldados"
    );

    // Onde o TopologyError VIVERIA: computando a topologia half-edge
    // diretamente (`set_flags`), a malha não-manifold devolve Err — é o erro
    // que o `with_flags` engole.
    let mut raw = bevy_rapier3d::parry::shape::TriMesh::with_flags(
        vtx,
        tris,
        TriMeshFlags::MERGE_DUPLICATE_VERTICES,
    )
    .expect("merge em si não falha");
    let topology = raw.set_flags(TriMeshFlags::HALF_EDGE_TOPOLOGY);
    assert!(
        topology.is_err(),
        "a malha soldada é realmente não-manifold: {topology:?}"
    );
}

/// Triângulos adjacentes que percorrem a aresta partilhada no MESMO sentido —
/// o outro tipo de `TopologyError` (`BadAdjacentTrianglesOrientation`).
#[test]
fn inconsistent_winding_only_errors_when_topology_is_computed() {
    // Edge (0→1) percorrido nos dois triângulos no mesmo sentido.
    let verts = vec![
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [1.0, 1.0, 1.0],
    ];
    let idx: Vec<u16> = vec![0, 1, 2, 0, 1, 3];
    let (vtx, tris) = to_rapier(&verts, &idx);

    assert!(
        Collider::trimesh_with_flags(
            vtx.clone(),
            tris.clone(),
            TriMeshFlags::MERGE_DUPLICATE_VERTICES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES
        )
        .is_ok(),
        "sem flags de topologia, winding inconsistente NÃO falha o bake"
    );

    let mut raw =
        bevy_rapier3d::parry::shape::TriMesh::with_flags(vtx, tris, TriMeshFlags::empty())
            .expect("construção em si não falha");
    let topology = raw.set_flags(TriMeshFlags::HALF_EDGE_TOPOLOGY);
    assert!(
        topology.is_err(),
        "com HALF_EDGE_TOPOLOGY o winding inconsistente é apanhadado: {topology:?}"
    );
}

// ------------------------------------------- (c) degenerados e quase-degenerados

#[test]
fn degenerate_triangles_are_deleted_not_fatal() {
    // Triângulo com índice repetido: [0,0,1].
    let verts = vec![[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]];
    let idx: Vec<u16> = vec![0, 0, 1, 0, 1, 2];
    let (vtx, tris) = to_rapier(&verts, &idx);

    let baked = Collider::trimesh_with_flags(
        vtx.clone(),
        tris.clone(),
        TriMeshFlags::MERGE_DUPLICATE_VERTICES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES,
    )
    .expect("DELETE_DEGENERATE apaga o triângulo degenerado em vez de falhar");
    let remaining = baked
        .raw
        .as_trimesh()
        .expect("o resultado é um trimesh")
        .indices()
        .len();
    assert_eq!(remaining, 1, "resta só o triângulo válido");

    // Área quase nula (~1e-6) com vértices DISTINTOS não é "degenerado" para
    // o parry (só índices repetidos contam) — bakeia normalmente.
    let tiny = vec![[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.000001, 0.0]];
    let (vtx, tris) = to_rapier(&tiny, &[0, 1, 2]);
    assert!(
        Collider::trimesh_with_flags(
            vtx,
            tris,
            TriMeshFlags::MERGE_DUPLICATE_VERTICES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES
        )
        .is_ok(),
        "triângulo de área 1e-6 com vértices distintos não é eliminado nem falha"
    );
}

/// Meshes não-indexados (a forma que o bevy produz para GLBs POSITION-only):
/// `from_bevy_mesh` recusa, o nosso extractor recupera a geometria exata.
#[test]
fn unindexed_mesh_fails_from_bevy_mesh_but_bakes_via_extractor() {
    let (verts, idx) = box_geometry(Vec3::ZERO, Vec3::ONE);
    let mut expanded = Vec::with_capacity(idx.len());
    for &i in &idx {
        expanded.push(verts[i as usize]);
    }
    let mesh = tri_mesh(expanded, None); // sem índices, como o loader deixa

    assert!(
        Collider::from_bevy_mesh(
            &mesh,
            &ComputedColliderShape::TriMesh(TriMeshFlags::MERGE_DUPLICATE_VERTICES)
        )
        .is_none(),
        "premissa: from_bevy_mesh exige buffer de índices"
    );
    assert!(collider_from_mesh(&mesh, false).is_some());
    assert!(mesh_vertices_indices(&mesh).is_some());
}

// --------------------------------------------------- GLB real — causa raiz

/// Parser GLB mínimo (std-only): devolve as posições e os índices do 1.º
/// primitivo do 1.º mesh. Os `*_collision.glb` do pipeline são todos
/// POSITION-only + indexados, por isso um leitor direto de accessors chega.
fn parse_glb(path: &str) -> (Vec<[f32; 3]>, Vec<u32>) {
    let data = std::fs::read(path).unwrap_or_else(|e| panic!("GLB legível: {e}"));
    assert_eq!(&data[0..4], b"glTF", "magic glTF");
    let mut off = 12usize;
    let mut json = String::new();
    let mut bin: &[u8] = &[];
    while off + 8 <= data.len() {
        let len = u32::from_le_bytes(data[off..off + 4].try_into().unwrap()) as usize;
        let ctype = u32::from_le_bytes(data[off + 4..off + 8].try_into().unwrap());
        let chunk = &data[off + 8..off + 8 + len];
        match ctype {
            0x4E4F_534A => json = String::from_utf8(chunk.to_vec()).expect("JSON chunk"),
            0x004E_4942 => bin = chunk,
            _ => {}
        }
        off += 8 + len;
    }

    fn objects_in_array<'j>(json: &'j str, array_key: &str) -> Vec<&'j str> {
        let key = format!("\"{array_key}\"");
        let at = json
            .find(&key)
            .unwrap_or_else(|| panic!("chave `{array_key}`"));
        let arr = json[at..].find('[').unwrap() + at;
        let bytes = json.as_bytes();
        let mut objs = Vec::new();
        let mut depth = 0usize;
        let mut start = 0usize;
        let mut i = arr + 1;
        while i < bytes.len() {
            match bytes[i] {
                b'{' => {
                    if depth == 0 {
                        start = i;
                    }
                    depth += 1;
                }
                b'}' => {
                    depth -= 1;
                    if depth == 0 {
                        objs.push(&json[start..=i]);
                    }
                }
                b']' if depth == 0 => break,
                _ => {}
            }
            i += 1;
        }
        objs
    }

    fn field_int(obj: &str, key: &str) -> Option<u64> {
        let pat = format!("\"{key}\"");
        let at = obj.find(&pat)?;
        let after = &obj[at + pat.len()..];
        let colon = after.find(':')?;
        let rest = after[colon + 1..].trim_start();
        let end = rest
            .find(|c: char| !c.is_ascii_digit())
            .unwrap_or(rest.len());
        rest[..end].parse().ok()
    }

    fn read_vec3(accessor: &str, views: &[&str], bin: &[u8]) -> Vec<[f32; 3]> {
        let count = field_int(accessor, "count").expect("accessor count") as usize;
        let view = &views[field_int(accessor, "bufferView").expect("bufferView") as usize];
        let voff = field_int(view, "byteOffset").unwrap_or(0) as usize;
        let aoff = field_int(accessor, "byteOffset").unwrap_or(0) as usize;
        let stride = field_int(view, "byteStride").unwrap_or(12) as usize;
        (0..count)
            .map(|i| {
                let base = voff + aoff + i * stride;
                let le =
                    |s: usize| f32::from_le_bytes(bin[base + s..base + s + 4].try_into().unwrap());
                [le(0), le(4), le(8)]
            })
            .collect()
    }

    fn read_indices(accessor: &str, views: &[&str], bin: &[u8]) -> Vec<u32> {
        let count = field_int(accessor, "count").expect("accessor count") as usize;
        let component = field_int(accessor, "componentType").expect("componentType");
        let view = &views[field_int(accessor, "bufferView").expect("bufferView") as usize];
        let voff = field_int(view, "byteOffset").unwrap_or(0) as usize;
        let aoff = field_int(accessor, "byteOffset").unwrap_or(0) as usize;
        let (width, stride) = match component {
            5123 => (2usize, field_int(view, "byteStride").unwrap_or(2) as usize),
            5125 => (4usize, field_int(view, "byteStride").unwrap_or(4) as usize),
            other => panic!("componentType de índice inesperado: {other}"),
        };
        (0..count)
            .map(|i| {
                let base = voff + aoff + i * stride;
                match width {
                    2 => u16::from_le_bytes(bin[base..base + 2].try_into().unwrap()) as u32,
                    _ => u32::from_le_bytes(bin[base..base + 4].try_into().unwrap()),
                }
            })
            .collect()
    }

    let meshes = objects_in_array(&json, "meshes");
    let primitives = objects_in_array(meshes[0], "primitives");
    let primitive = primitives[0];
    let accessors = objects_in_array(&json, "accessors");
    let views = objects_in_array(&json, "bufferViews");

    let pos_acc = &accessors[field_int(primitive, "POSITION").expect("POSITION") as usize];
    let idx_acc = &accessors[field_int(primitive, "indices").expect("indices") as usize];
    let positions = read_vec3(pos_acc, &views, bin);
    let indices = read_indices(idx_acc, &views, bin);
    (positions, indices)
}

/// O teste que PINA a causa raiz, com o asset real dos 326 warnings:
///
/// 1. o GLB no disco é indexado e saudável — bakeia por qualquer caminho;
/// 2. o que o bevy 0.19 produz DELE (POSITION-only → sem NORMAL →
///    `duplicate_vertices` + flat normals) é um mesh NÃO-indexado — e nesse
///    `from_bevy_mesh` devolve `None`: o bake falhava, o prop ficava sem
///    collider, o player atravessava-o;
/// 3. o caminho atual (`mesh_vertices_indices` + escada de flags) bakeia o
///    mesmo mesh não-indexado com sucesso.
#[test]
fn real_crate_collision_glb_root_cause_and_fix() {
    let (positions, indices) = parse_glb(crate_glb().to_str().expect("utf8 path"));

    // Os dados já coletados à mão, agora garantidos pelo teste:
    // Valores do market_stall_collision.glb (o wooden_crate saiu do catálogo
    // na regeneração PBR do pool, 2026-09-08 — o contrato é o mesmo).
    assert_eq!(
        positions.len(),
        163,
        "vértices do market_stall_collision.glb"
    );
    assert_eq!(indices.len(), 966, "índices u16");
    assert!(
        positions.iter().all(|p| p.iter().all(|c| c.is_finite())),
        "sem NaN/inf na geometria"
    );

    // 1. A forma indexada (como está no ficheiro) é saudável.
    let verts_u16: Vec<[f32; 3]> = positions.clone();
    let idx_u16: Vec<u16> = indices.iter().map(|i| *i as u16).collect();
    let mesh_raw = tri_mesh(verts_u16.clone(), Some(idx_u16.clone()));
    assert!(
        Collider::from_bevy_mesh(
            &mesh_raw,
            &ComputedColliderShape::TriMesh(TriMeshFlags::MERGE_DUPLICATE_VERTICES)
        )
        .is_some(),
        "GLB indexado cru bakeia com from_bevy_mesh — a geometria não é o problema"
    );

    // 2. A forma que o bevy produz: cada índice expandido, SEM buffer de
    //    índices. É AQUI que o bake antigo morria.
    let mut expanded = Vec::with_capacity(indices.len());
    for &i in &indices {
        expanded.push(positions[i as usize]);
    }
    let mesh_bevy = tri_mesh(expanded, None);

    assert!(
        Collider::from_bevy_mesh(
            &mesh_bevy,
            &ComputedColliderShape::TriMesh(TriMeshFlags::MERGE_DUPLICATE_VERTICES)
        )
        .is_none(),
        "CAUSA RAIZ dos 326 warns: from_bevy_mesh devolve None para o mesh \
         não-indexado que o loader glTF produz de um GLB POSITION-only"
    );

    // 3. O caminho atual recupera a geometria e bakeia — os props da vila
    //    ficam com trimesh exato.
    let collider = collider_from_mesh(&mesh_bevy, false)
        .expect("o fix bakeia o mesh não-indexado do GLB real");
    assert!(
        collider_from_mesh(&mesh_bevy, true).is_some(),
        "o caminho convex (precompute) também bakeia"
    );

    // E a escada resolve logo no 1.º degrau (flags de hoje).
    let (vtx, tris) = mesh_vertices_indices(&mesh_bevy).expect("extrator lê o mesh bevy");
    assert_eq!(tris.len(), 322, "os 322 triângulos do GLB são recuperados");
    assert!(
        bake_trimesh_escalating(vtx, tris).is_ok(),
        "escada de flags: 1.º degrau basta para o GLB real"
    );
    let _ = collider; // só provámos que existe
}

// --------------------------------------------------------- escada de flags

/// O motivo de falha lista TODAS as tentativas — é o que vai para o
/// warn-once por asset.
#[test]
fn escalation_ladder_reports_every_attempt_when_all_fail() {
    // Entrada impossível: sem vértices nem índices, os dois degraus de
    // trimesh falham (EmptyIndices) e o hull não tem volume.
    let outcome = bake_trimesh_escalating(Vec::new(), Vec::new());
    let reason = outcome.expect_err("sem geometria não há collider");
    assert!(
        reason.contains("MERGE_DUPLICATE_VERTICES|DELETE_DEGENERATE_TRIANGLES"),
        "motivo menciona o 1.º degrau: {reason}"
    );
    assert!(
        reason.contains("default (sem merge)"),
        "motivo menciona o 2.º degrau: {reason}"
    );
    assert!(
        reason.contains("convex hull"),
        "motivo menciona o último recurso: {reason}"
    );
}

/// Determinismo: para geometria saudável a escada devolve sucesso de forma
/// estável (o 1.º degrau — flags idênticas às de hoje — ganha sempre).
#[test]
fn escalation_ladder_is_deterministic_for_healthy_meshes() {
    let (verts, idx) = box_geometry(Vec3::NEG_ONE, Vec3::ONE);
    let (vtx, tris) = to_rapier(&verts, &idx);
    for _ in 0..3 {
        let baked = bake_trimesh_escalating(vtx.clone(), tris.clone())
            .expect("geometria saudável bakeia no 1.º degrau");
        assert!(
            baked.raw.as_trimesh().is_some(),
            "resultado é um trimesh exato, não hull"
        );
    }
}
