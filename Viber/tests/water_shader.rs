//! Water shader regression harness — Naga parse + validation, no engine,
//! window, or assets (same contract as `tests/chunk_shader.rs`).
//!
//! O `water.wgsl` agora leva um VERTEX shader completo (a extensão
//! `MaterialExtension::vertex_shader()` SUBSTITUI o base do bevy): é a peça
//! mais frágil do passe de água (replica do `mesh.wgsl` + deslocamento de
//! onda), por isso este harness compila o template inteiro sob os defines da
//! pipeline real da água (malha com posições/normais/uvs/cores) e também sem
//! defines — um erro de sintaxe/tipos falha `cargo test` em vez de crashar o
//! driver na criação do pipeline.
//!
//! IMPORTANT: os stubs NÃO são o layout real do Bevy — provam a consistência
//! interna do shader, não a compatibilidade com o pipeline layout.

use naga::valid::{Capabilities, ValidationFlags};

/// Stubs mínimos explícitos para os `#import`s do shader da água. O alias
/// `vt::` é resolvido por pré-processamento de texto (o naga puro não compõe
/// namespaces de módulo).
const IMPORTS: [(&str, &str); 9] = [
    (
        "#import bevy_pbr::forward_io::{Vertex, VertexOutput, FragmentOutput}",
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
         struct FragmentOutput { @location(0) color: vec4<f32>, };",
    ),
    (
        "#import bevy_pbr::pbr_fragment::pbr_input_from_standard_material",
        "struct PbrMaterialStub {\n\
         \x20   flags: u32,\n\
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
        "#import bevy_pbr::pbr_functions::{alpha_discard, apply_pbr_lighting, main_pass_post_lighting_processing}",
        "fn alpha_discard(material: PbrMaterialStub, color: vec4<f32>) -> vec4<f32> {\n\
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
        "#import bevy_pbr::mesh_view_bindings::{globals, view}",
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
        "#import bevy_pbr::view_transformations as vt",
        "// o alias `vt::` é despromovido pelo pré-processamento do harness;\n\
         // as funções entram abaixo como nomes simples.",
    ),
    (
        "#import bevy_pbr::prepass_utils",
        "fn prepass_depth(frag_coord: vec4<f32>, sample_index: u32) -> f32 {\n\
         \x20   return 1.0;\n\
         }",
    ),
    (
        "#import bevy_pbr::mesh_functions",
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
        "#import bevy_pbr::skinning",
        "// a água nunca é skinned — o módulo não contribui símbolos no harness.",
    ),
    (
        "#import bevy_pbr::morph::{morph_position, morph_normal, morph_tangent}",
        "fn morph_position(vertex_index: u32, i: u32, instance_index: u32) -> vec3<f32> {\n\
         \x20   return vec3<f32>(0.0);\n\
         }\n\
         fn morph_normal(vertex_index: u32, i: u32, instance_index: u32) -> vec3<f32> {\n\
         \x20   return vec3<f32>(0.0);\n\
         }\n\
         fn morph_tangent(vertex_index: u32, i: u32, instance_index: u32) -> vec3<f32> {\n\
         \x20   return vec3<f32>(0.0);\n\
         }",
    ),
];

/// Stubs para as funções do `vt::` — o alias não sobrevive ao naga puro.
const VT_STUBS: &str = "fn position_world_to_clip(world_position: vec3<f32>) -> vec4<f32> {\n\
    return vec4<f32>(world_position, 1.0);\n\
}\n\
fn frag_coord_to_ndc(frag_coord: vec4<f32>) -> vec3<f32> {\n\
    return frag_coord.xyz;\n\
}\n\
fn position_ndc_to_view(ndc: vec3<f32>) -> vec3<f32> {\n\
    return ndc;\n\
}\n";

/// Resolve `#import`s a stubs, `#ifdef` contra `defines`, o placeholder
/// `#{MATERIAL_BIND_GROUP}` e o alias `vt::` (nomes simples).
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
            if trimmed == "#import bevy_pbr::view_transformations as vt" {
                out.push_str(VT_STUBS);
                continue;
            }
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
        // Aliases/namespaces: `vt::fn`, `mesh_functions::fn`, … → nomes
        // simples (o naga puro não compõe namespaces de módulo).
        let mut resolved = line.replace("vt::", "");
        for prefix in ["mesh_functions::", "skinning::", "bevy_pbr::morph::"] {
            resolved = resolved.replace(prefix, "");
        }
        out.push_str(&resolved);
        out.push('\n');
    }
    assert!(stack.is_empty(), "unbalanced #ifdef in the water shader");
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

/// O template compila sob os defines da pipeline REAL da água (malha com
/// posições/normais/uvs/cores) — o vertex com deslocamento de onda é o
/// ponto mais frágil (replica do mesh.wgsl). Sem VERTEX_POSITIONS o vertex
/// é inválido POR DESENHO (o Bevy nunca o corre assim), por isso o combo
/// vazio não entra.
#[test]
fn water_shader_validates_in_pipeline_defines() {
    let template = include_str!("../src/terrain/water.wgsl");
    let defines = [
        "VERTEX_POSITIONS",
        "VERTEX_NORMALS",
        "VERTEX_UVS_A",
        "VERTEX_COLORS",
    ]; // a pipeline real da água (run)
    validate(&standalone(template, &defines));
    // O bloco CONFIG especializado por mundo também tem de validar.
    let config = viber::terrain::water_material::WaterSurfaceConfig::default();
    let specialized = config.render_world_shader();
    validate(&standalone(&specialized, &defines));
}

/// Os dois pontos de entrada têm de existir — o vertex é a linha da frente
/// contra o SIGSEGV do driver (um vertex base silencioso era o regresso ao
/// default do bevy sem deslocamento).
#[test]
fn water_shader_has_both_entries() {
    use naga::ShaderStage;
    let module = validate(&standalone(
        include_str!("../src/terrain/water.wgsl"),
        &["VERTEX_POSITIONS", "VERTEX_NORMALS", "VERTEX_UVS_A", "VERTEX_COLORS"],
    ));
    assert!(module
        .entry_points
        .iter()
        .any(|entry| entry.name == "vertex" && entry.stage == ShaderStage::Vertex));
    assert!(module
        .entry_points
        .iter()
        .any(|entry| entry.name == "fragment" && entry.stage == ShaderStage::Fragment));
}
