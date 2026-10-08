//! Terrain chunk shader regression harness — WESL compile + Naga parse +
//! validation, no engine, window, or assets.
//!
//! The chunk material's WESL is specialized per world (CONFIG block) and
//! compiled under shader defines (`BINDLESS`, `VERTEX_COLORS`) that Bevy
//! resolves through the WESL conditional-translation pass. This harness
//! compiles every combination with explicit minimal import stubs (same
//! machinery as the renderer), so a syntax/type slip in the wall skin
//! (triplanar, strata, streaks, moss, wall space) fails `cargo test` instead
//! of crashing at material load.
//!
//! IMPORTANT: the stubs are NOT the real Bevy view/mesh layout — they prove
//! the shader's internal consistency, not pipeline-layout compatibility.

mod common;

/// Explicit minimal stub MODULES for the imports the chunk shader uses
/// (caminhos do bevy 0.20 — `bevy_pbr::render::*`).
const STUBS: [(&str, &str); 8] = [
    (
        "bevy_pbr::render::forward_io",
        "struct VertexOutput {\n\
         \x20   @builtin(position) position: vec4<f32>,\n\
         \x20   @location(0) world_position: vec4<f32>,\n\
         \x20   @location(1) world_normal: vec3<f32>,\n\
         \x20   @location(2) color: vec4<f32>,\n\
         \x20   @location(3) instance_index: u32,\n\
         };\n\
         struct FragmentOutput { @location(0) color: vec4<f32>, };",
    ),
    (
        // `view` (item) e `screen_space_ambient_occlusion_texture` (item,
        // só com SSAO) vivem no MESMO módulo — o stub declara ambos.
        "bevy_pbr::render::mesh_view_bindings",
        "struct View { world_position: vec4<f32>, clip_from_view: mat4x4<f32>, };\n\
         @group(1) @binding(0) var<uniform> view: View;\n\
         @group(1) @binding(1) var screen_space_ambient_occlusion_texture: texture_2d<f32>;",
    ),
    (
        "bevy_pbr::render::pbr_types",
        "const STANDARD_MATERIAL_FLAGS_FOG_ENABLED_BIT: u32 = 1u << 8u;\n\
         struct PbrMaterialStub {\n\
         \x20   flags: u32,\n\
         \x20   base_color: vec4<f32>,\n\
         \x20   perceptual_roughness: f32,\n\
         };\n\
         struct PbrInput {\n\
         \x20   material: PbrMaterialStub,\n\
         \x20   diffuse_occlusion: vec3<f32>,\n\
         \x20   specular_occlusion: f32,\n\
         \x20   frag_coord: vec4<f32>,\n\
         \x20   world_position: vec4<f32>,\n\
         \x20   world_normal: vec3<f32>,\n\
         \x20   N: vec3<f32>,\n\
         \x20   V: vec3<f32>,\n\
         \x20   is_orthographic: bool,\n\
         \x20   flags: u32,\n\
         };\n\
         fn pbr_input_new() -> PbrInput {\n\
         \x20   var p: PbrInput;\n\
         \x20   p.material.flags = 0u;\n\
         \x20   p.material.base_color = vec4<f32>(1.0, 1.0, 1.0, 1.0);\n\
         \x20   p.material.perceptual_roughness = 0.5;\n\
         \x20   p.diffuse_occlusion = vec3<f32>(1.0, 1.0, 1.0);\n\
         \x20   p.specular_occlusion = 1.0;\n\
         \x20   p.frag_coord = vec4<f32>(0.0, 0.0, 0.0, 1.0);\n\
         \x20   p.world_position = vec4<f32>(0.0, 0.0, 0.0, 1.0);\n\
         \x20   p.world_normal = vec3<f32>(0.0, 0.0, 1.0);\n\
         \x20   p.N = vec3<f32>(0.0, 0.0, 1.0);\n\
         \x20   p.V = vec3<f32>(1.0, 0.0, 0.0);\n\
         \x20   p.is_orthographic = false;\n\
         \x20   p.flags = 0u;\n\
         \x20   return p;\n\
         }",
    ),
    (
        "bevy_pbr::render::pbr_functions",
        "import bevy_pbr::render::pbr_types::PbrInput;\n\
         fn apply_pbr_lighting(pbr_input: PbrInput) -> vec4<f32> {\n\
         \x20   return pbr_input.material.base_color;\n\
         }\n\
         fn main_pass_post_lighting_processing(pbr_input: PbrInput, input_color: vec4<f32>) -> vec4<f32> {\n\
         \x20   return input_color;\n\
         }\n\
         fn calculate_view(world_position: vec4<f32>, is_orthographic: bool) -> vec3<f32> {\n\
         \x20   return normalize(vec3<f32>(0.0, 0.0, 1.0));\n\
         }",
    ),
    (
        "bevy_pbr::render::pbr_lighting",
        "fn perceptualRoughnessToRoughness(perceptual_roughness: f32) -> f32 {\n\
         \x20   return perceptual_roughness * perceptual_roughness;\n\
         }",
    ),
    (
        "bevy_pbr::render::mesh_bindings",
        "struct MeshBindStub { material_and_lightmap_bind_group_slot: u32, flags: u32, }\n\
         @group(2) @binding(4) var<storage, read> mesh: array<MeshBindStub>;",
    ),
    (
        "bevy_pbr::ssao::utils",
        "fn ssao_multibounce(visibility: f32, base_color: vec3<f32>) -> vec3<f32> {\n\
         \x20   return vec3<f32>(visibility);\n\
         }",
    ),
    (
        // Grupo 4: o grupo 3 passou a ser o MATERIAL (MATERIAL_BIND_GROUP)
        // — os arrays de bindless do stub têm de ficar fora dele.
        "bevy_render::bindless",
        "@group(4) @binding(0) var bindless_textures_2d: binding_array<texture_2d<f32>>;\n\
         @group(4) @binding(1) var bindless_samplers_filtering: binding_array<sampler>;",
    ),
];

#[test]
fn chunk_shader_validates_in_every_define_combination() {
    let template = include_str!("../src/terrain/chunk.wesl");
    for defines in [
        vec!["BINDLESS", "VERTEX_COLORS"], // the live `run` configuration
        vec!["BINDLESS", "VERTEX_COLORS", "DISTANCE_FOG"], // idem, com câmara com `DistanceFog` (default do `run`)
        vec![
            "BINDLESS",
            "VERTEX_COLORS",
            "SCREEN_SPACE_AMBIENT_OCCLUSION",
        ], // idem, com SSAO na câmara (default do `run`)
        vec!["BINDLESS"], // chunk meshes always carry colors — but the gate compiles either way
        vec!["VERTEX_COLORS"], // portable non-bindless fallback
        vec!["DISTANCE_FOG"], // fog sem bindless (câmara com DistanceFog, driver sem bindless)
        vec!["SCREEN_SPACE_AMBIENT_OCCLUSION"], // SSAO sem bindless
        vec![],
    ] {
        let wgsl = common::compile_wesl(template, &STUBS, &defines);
        common::validate(&wgsl);
    }
}

/// O bloco de fog chama `apply_fog(view_bindings::fog, …)` — quando existia,
/// a referência era ao NAMESPACE que só existia com o import aliasado. O
/// chunk.wesl atual não usa fog direto (vem pelo
/// `main_pass_post_lighting_processing`), mas o guarda mantém-se: se o
/// namespace voltar a aparecer, o import aliasado tem de voltar também
/// (regressão de 2026-09-06: terreno invisível no run).
#[test]
fn fog_block_imports_the_view_bindings_namespace() {
    let template = include_str!("../src/terrain/chunk.wesl");
    if template.contains("view_bindings::fog") {
        assert!(
            template.contains("import bevy_pbr::render::mesh_view_bindings as view_bindings"),
            "apply_fog(view_bindings::fog, …) sem o import `as view_bindings` \
             quebra o compose do shader — terreno invisível no run"
        );
    }
}

#[test]
fn specialized_world_config_validates() {
    let config = viber::terrain::layer_material::TerrainChunkConfig {
        tri_slope: 0.357, // cliff-angle 50°
        tri_soft: 0.12,
        strata_spacing: 4.0,
        strata_strength: 0.25,
        rock_darken: 0.45,
        streaks: 0.55,
        moss: 0.4,
    };
    let specialized = config.render_world_shader();
    assert!(specialized.contains("const CFG_STREAK: f32 = 0.55;"));
    assert!(specialized.contains("const CFG_MOSS: f32 = 0.4;"));
    // (`constants::MATERIAL_BIND_GROUP` sobrevive à reescrita do CONFIG de
    // propósito — o compilador WESL resolve-o pelo módulo `constants`.)
    for defines in [vec!["BINDLESS", "VERTEX_COLORS"], vec![]] {
        let wgsl = common::compile_wesl(&specialized, &STUBS, &defines);
        common::validate(&wgsl);
    }
}
