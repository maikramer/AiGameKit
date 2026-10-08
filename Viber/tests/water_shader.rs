//! Water shader regression harness — WESL compile + Naga parse + validation,
//! no engine, window, or assets (same contract as `tests/chunk_shader.rs`).
//!
//! O `water.wesl` leva um VERTEX shader completo (a extensão
//! `MaterialExtension::vertex_shader()` SUBSTITUI o base do bevy): é a peça
//! mais frágil do passe de água (replica do `mesh.wesl` + deslocamento de
//! onda), por isso este harness compila o template inteiro sob os defines da
//! pipeline real da água (malha com posições/normais/uvs/cores) — um erro de
//! sintaxe/tipos falha `cargo test` em vez de crashar o driver na criação do
//! pipeline.
//!
//! IMPORTANT: os stubs NÃO são o layout real do Bevy — provam a consistência
//! interna do shader, não a compatibilidade com o pipeline layout.

mod common;

use naga::ShaderStage;

/// Stubs mínimos explícitos para os módulos que o water.wesl importa
/// (caminhos do bevy 0.20 — `bevy_pbr::render::*`). O alias `vt::` é
/// resolvido NATIVAMENTE pelo wesl (não precisa do truque textual antigo).
const STUBS: [(&str, &str); 9] = [
    (
        "bevy_pbr::render::forward_io",
        "struct Vertex {\n\
         \x20   @builtin(instance_index) instance_index: u32,\n\
         \x20   @location(0) position: vec3<f32>,\n\
         \x20   @location(1) normal: vec3<f32>,\n\
         \x20   @location(2) uv: vec2<f32>,\n\
         \x20   @location(5) color: vec4<f32>,\n\
         };\n\
         struct VertexOutput {\n\
         \x20   @builtin(position) position: vec4<f32>,\n\
         \x20   @location(0) world_position: vec4<f32>,\n\
         \x20   @location(1) world_normal: vec3<f32>,\n\
         \x20   @location(2) uv: vec2<f32>,\n\
         \x20   @location(5) color: vec4<f32>,\n\
         };\n\
         struct FragmentOutput { @location(0) color: vec4<f32>, };\n\
         fn decompress_vertex(vertex: Vertex, instance_index: u32) -> Vertex {\n\
         \x20   return vertex;\n\
         }",
    ),
    (
        "bevy_pbr::render::pbr_fragment",
        "import bevy_pbr::render::forward_io::VertexOutput;\n\
         struct PbrMaterialStub {\n\
         \x20   flags: u32,\n\
         \x20   alpha_cutoff: f32,\n\
         \x20   base_color: vec4<f32>,\n\
         \x20   perceptual_roughness: f32,\n\
         \x20   emissive: vec4<f32>,\n\
         };\n\
         struct PbrInput {\n\
         \x20   material: PbrMaterialStub,\n\
         \x20   frag_coord: vec4<f32>,\n\
         \x20   world_position: vec4<f32>,\n\
         \x20   world_normal: vec3<f32>,\n\
         \x20   N: vec3<f32>,\n\
         \x20   V: vec3<f32>,\n\
         };\n\
         fn pbr_input_from_standard_material(in: VertexOutput, is_front: bool) -> PbrInput {\n\
         \x20   var p: PbrInput;\n\
         \x20   p.material.flags = 0u;\n\
         \x20   p.material.base_color = in.color;\n\
         \x20   p.material.perceptual_roughness = 0.1;\n\
         \x20   p.material.emissive = vec4<f32>(0.0);\n\
         \x20   p.frag_coord = in.position;\n\
         \x20   p.world_position = in.world_position;\n\
         \x20   p.world_normal = in.world_normal;\n\
         \x20   p.N = in.world_normal;\n\
         \x20   p.V = vec3<f32>(0.0, 0.0, 1.0);\n\
         \x20   return p;\n\
         }",
    ),
    (
        "bevy_pbr::render::pbr_functions",
        "import bevy_pbr::render::pbr_fragment::PbrInput;\n\
         fn alpha_discard(material_flags: u32, alpha_cutoff: f32, color: vec4<f32>) -> vec4<f32> {\n\
         \x20   return color;\n\
         }\n\
         fn apply_pbr_lighting(pbr_input: PbrInput) -> vec4<f32> {\n\
         \x20   return pbr_input.material.base_color;\n\
         }\n\
         fn main_pass_post_lighting_processing(pbr_input: PbrInput, input_color: vec4<f32>) -> vec4<f32> {\n\
         \x20   return input_color;\n\
         }",
    ),
    (
        "bevy_pbr::render::mesh_view_bindings",
        "struct View {\n\
         \x20   world_position: vec3<f32>,\n\
         \x20   world_from_view: mat4x4<f32>,\n\
         \x20   clip_from_view: mat4x4<f32>,\n\
         };\n\
         @group(0) @binding(0) var<uniform> view: View;\n\
         struct Globals { time: f32, };\n\
         @group(0) @binding(11) var<uniform> globals: Globals;",
    ),
    (
        "bevy_pbr::render::view_transformations",
        "fn position_world_to_clip(world_position: vec3<f32>) -> vec4<f32> {\n\
         \x20   return vec4<f32>(world_position, 1.0);\n\
         }",
    ),
    (
        "bevy_pbr::render::mesh_bindings",
        "struct MeshBindStub {\n\
         \x20   first_vertex_index: u32,\n\
         \x20   morph_descriptor_index: u32,\n\
         \x20   material_and_lightmap_bind_group_slot: u32,\n\
         \x20   flags: u32,\n\
         }\n\
         @group(2) @binding(4) var<storage, read> mesh: array<MeshBindStub>;",
    ),
    (
        "bevy_pbr::render::mesh_functions",
        "fn get_world_from_local(instance_index: u32) -> mat4x4<f32> {\n\
         \x20   return mat4x4<f32>(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0);\n\
         }\n\
         fn mesh_position_local_to_world(world_from_local: mat4x4<f32>, position: vec4<f32>) -> vec4<f32> {\n\
         \x20   return world_from_local * position;\n\
         }\n\
         fn mesh_normal_local_to_world(normal: vec3<f32>, instance_index: u32) -> vec3<f32> {\n\
         \x20   return normal;\n\
         }\n\
         fn mesh_tangent_local_to_world(world_from_local: mat4x4<f32>, tangent: vec4<f32>, instance_index: u32) -> vec4<f32> {\n\
         \x20   return tangent;\n\
         }",
    ),
    (
        "bevy_pbr::render::skinning",
        // a água nunca é skinned — o @if(SKINNED) desliga-se nos combos do
        // harness, mas o módulo tem de existir para o import resolver.
        "fn skin_model(joint_indices: vec4<u32>, joint_weights: vec4<f32>, instance_index: u32) -> mat4x4<f32> {\n\
         \x20   return mat4x4<f32>(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0);\n\
         }\n\
         fn skin_normals(world_from_local: mat4x4<f32>, normal: vec3<f32>) -> vec3<f32> {\n\
         \x20   return normal;\n\
         }",
    ),
    (
        "bevy_pbr::render::morph",
        "fn morph_position(vertex_index: u32, i: u32, morph_descriptor_index: u32) -> vec3<f32> {\n\
         \x20   return vec3<f32>(0.0);\n\
         }\n\
         fn morph_normal(vertex_index: u32, i: u32, morph_descriptor_index: u32) -> vec3<f32> {\n\
         \x20   return vec3<f32>(0.0);\n\
         }\n\
         fn morph_tangent(vertex_index: u32, i: u32, morph_descriptor_index: u32) -> vec3<f32> {\n\
         \x20   return vec3<f32>(0.0);\n\
         }\n\
         fn layer_count(morph_descriptor_index: u32) -> u32 {\n\
         \x20   return 0u;\n\
         }\n\
         fn weight_at(i: u32, morph_descriptor_index: u32) -> f32 {\n\
         \x20   return 0.0;\n\
         }",
    ),
];

/// O template compila sob os defines da pipeline REAL da água (malha com
/// posições/normais/uvs/cores) — o vertex com deslocamento de onda é o
/// ponto mais frágil (replica do mesh.wesl).
fn pipeline_defines() -> Vec<&'static str> {
    vec![
        "VERTEX_POSITIONS",
        "VERTEX_NORMALS",
        "VERTEX_UVS_A",
        "VERTEX_COLORS",
    ]
}

#[test]
fn water_shader_validates_in_pipeline_defines() {
    let template = include_str!("../src/terrain/water.wesl");
    let defines = pipeline_defines();
    let wgsl = common::compile_wesl(template, &STUBS, &defines);
    common::validate(&wgsl);
    // O bloco CONFIG especializado por mundo também tem de validar.
    let config = viber::terrain::water_material::WaterSurfaceConfig::default();
    let specialized = config.render_world_shader();
    let wgsl = common::compile_wesl(&specialized, &STUBS, &defines);
    common::validate(&wgsl);
}

/// Os dois pontos de entrada têm de existir — o vertex é a linha da frente
/// contra o SIGSEGV do driver (um vertex base silencioso era o regresso ao
/// default do bevy sem deslocamento).
#[test]
fn water_shader_has_both_entries() {
    let module = common::validate(&common::compile_wesl(
        include_str!("../src/terrain/water.wesl"),
        &STUBS,
        &pipeline_defines(),
    ));
    assert!(
        module
            .entry_points
            .iter()
            .any(|entry| entry.name == "vertex" && entry.stage == ShaderStage::Vertex)
    );
    assert!(
        module
            .entry_points
            .iter()
            .any(|entry| entry.name == "fragment" && entry.stage == ShaderStage::Fragment)
    );
}
