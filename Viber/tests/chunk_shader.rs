//! Terrain chunk shader regression harness — Naga parse + validation, no
//! engine, window, or assets (same contract as `tests/sky_shader.rs`).
//!
//! The chunk material's WGSL is specialized per world (CONFIG block) and
//! compiled under shader defines (`BINDLESS`, `VERTEX_COLORS`) that Bevy
//! resolves before Naga. This harness resolves them textually with explicit
//! minimal import stubs and validates every combination, so a syntax/type
//! slip in the wall skin (triplanar, strata, streaks, moss, wall space)
//! fails `cargo test` instead of crashing at material load.
//!
//! IMPORTANT: the stubs are NOT the real Bevy view/mesh layout — they prove
//! the shader's internal consistency, not pipeline-layout compatibility.

use naga::valid::{Capabilities, ValidationFlags};

/// Explicit minimal stubs for the `#import`s the chunk shader uses.
const IMPORTS: [(&str, &str); 9] = [
    (
        "#import bevy_pbr::forward_io::{VertexOutput, FragmentOutput}",
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
        "#import bevy_pbr::mesh_view_bindings::view",
        "struct View { world_position: vec4<f32>, clip_from_view: mat4x4<f32>, };\n\
         @group(1) @binding(0) var<uniform> view: View;",
    ),
    (
        "#import bevy_pbr::pbr_types::{pbr_input_new, STANDARD_MATERIAL_FLAGS_FOG_ENABLED_BIT}",
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
        "#import bevy_pbr::pbr_functions::{apply_pbr_lighting, main_pass_post_lighting_processing, calculate_view}",
        "fn apply_pbr_lighting(pbr_input: PbrInput) -> vec4<f32> {\n\
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
        "#import bevy_pbr::lighting::perceptualRoughnessToRoughness",
        "fn perceptualRoughnessToRoughness(perceptual_roughness: f32) -> f32 {\n\
         \x20   return perceptual_roughness * perceptual_roughness;\n\
         }",
    ),
    (
        "#import bevy_render::bindless::{bindless_textures_2d, bindless_samplers_filtering}",
        // Grupo 4: o grupo 3 passou a ser o MATERIAL (MATERIAL_BIND_GROUP_INDEX)
        // — os arrays de bindless do stub têm de ficar fora dele.
        "@group(4) @binding(0) var bindless_textures_2d: binding_array<texture_2d<f32>>;\n\
         @group(4) @binding(1) var bindless_samplers_filtering: binding_array<sampler>;",
    ),
    (
        "#import bevy_pbr::mesh_bindings::mesh",
        "struct MeshBindStub { material_and_lightmap_bind_group_slot: u32, flags: u32, }\n\
         @group(2) @binding(4) var<storage, read> mesh: array<MeshBindStub>;",
    ),
    (
        "#import bevy_pbr::mesh_view_bindings::screen_space_ambient_occlusion_texture",
        "@group(1) @binding(1) var screen_space_ambient_occlusion_texture: texture_2d<f32>;",
    ),
    (
        "#import bevy_pbr::ssao_utils::ssao_multibounce",
        "fn ssao_multibounce(visibility: f32, base_color: vec3<f32>) -> vec3<f32> {\n\
         \x20   return vec3<f32>(visibility);\n\
         }",
    ),
];

/// Resolves `#import` lines to stubs, `#ifdef/#else/#endif` against
/// `defines`, and Bevy's `#{...}` substitution placeholder.
fn standalone(source: &str, defines: &[&str]) -> String {
    let mut stack: Vec<bool> = Vec::new();
    let mut out = String::with_capacity(source.len());
    for line in source.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("#ifdef ") {
            let name = rest.split_whitespace().next().unwrap_or("");
            stack.push(defines.contains(&name));
            continue;
        }
        if trimmed.starts_with("#else") {
            if let Some(active) = stack.last_mut() {
                *active = !*active;
            }
            continue;
        }
        if trimmed.starts_with("#endif") {
            stack.pop();
            continue;
        }
        if !stack.iter().all(|active| *active) {
            continue;
        }
        if trimmed.starts_with("#import") {
            let stub = IMPORTS
                .iter()
                .find(|(import, _)| *import == trimmed)
                .unwrap_or_else(|| {
                    panic!(
                        "unsupported shader directive: {line}; extend the explicit harness contract"
                    )
                })
                .1;
            out.push_str(stub);
            out.push('\n');
            continue;
        }
        // O valor REAL que o bevy 0.19 substitui no runtime (material.rs:
        // MATERIAL_BIND_GROUP_INDEX = 3) — manter o placeholder a sincronizar
        // com esta constante nos dois lados.
        out.push_str(&line.replace(
            "#{MATERIAL_BIND_GROUP}",
            &bevy::pbr::MATERIAL_BIND_GROUP_INDEX.to_string(),
        ));
        out.push('\n');
    }
    assert!(stack.is_empty(), "unbalanced #ifdef in the chunk shader");
    out
}

fn validate(source: &str) -> naga::Module {
    let module = naga::front::wgsl::parse_str(source)
        .unwrap_or_else(|error| panic!("{}", error.emit_to_string(source)));
    naga::valid::Validator::new(ValidationFlags::all(), Capabilities::all())
        .validate(&module)
        .unwrap_or_else(|error| panic!("{}", error.emit_to_string(source)));
    module
}

#[test]
fn chunk_shader_validates_in_every_define_combination() {
    let template = include_str!("../src/terrain/chunk.wgsl");
    for defines in [
        vec!["BINDLESS", "VERTEX_COLORS"], // the live `run` configuration
        vec!["BINDLESS", "VERTEX_COLORS", "DISTANCE_FOG"], // idem, com câmara com `DistanceFog` (default do `run`)
        vec!["BINDLESS", "VERTEX_COLORS", "SCREEN_SPACE_AMBIENT_OCCLUSION"], // idem, com SSAO na câmara (default do `run`)
        vec!["BINDLESS"], // chunk meshes always carry colors — but the gate compiles either way
        vec!["VERTEX_COLORS"], // portable non-bindless fallback
        vec!["DISTANCE_FOG"], // fog sem bindless (câmara com DistanceFog, driver sem bindless)
        vec!["SCREEN_SPACE_AMBIENT_OCCLUSION"], // SSAO sem bindless
        vec![],
    ] {
        validate(&standalone(template, &defines));
    }
}

/// O bloco `#ifdef DISTANCE_FOG` chama `apply_fog(view_bindings::fog, …)` —
/// a referência é ao NAMESPACE que só existe se o template importar
/// `bevy_pbr::mesh_view_bindings as view_bindings`. Sem o alias, o compose
/// (naga_oil) falha com `ImportNotFound("view_bindings")`, o material perde
/// o pipeline e NENHUM chunk desenha (só o clear-color no lugar do chão —
/// regressão de 2026-09-06). O harness textual não compõe namespaces, por
/// isto fica preso aqui.
#[test]
fn fog_block_imports_the_view_bindings_namespace() {
    let template = include_str!("../src/terrain/chunk.wgsl");
    if template.contains("view_bindings::fog") {
        assert!(
            template.contains("#import bevy_pbr::mesh_view_bindings as view_bindings"),
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
    // (`#{MATERIAL_BIND_GROUP}` survives the CONFIG rewrite on purpose —
    // Bevy substitutes it at load; `standalone` resolves it for Naga.)
    for defines in [vec!["BINDLESS", "VERTEX_COLORS"], vec![]] {
        validate(&standalone(&specialized, &defines));
    }
}
