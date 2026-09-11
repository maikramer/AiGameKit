//! Harness do shader do SSR da água (`src/water_ssr.rs` — Fase B).
//!
//! O WGSL é SELF-CONTAINED (zero `#import`): o passe traz as próprias
//! matrizes/cotas por uniforms, portanto o naga valida-o cru, sem stubs.
//! O contrato importante é de LAYOUT: `SsrView` (288 B) e `SsrParams`
//! (176 B) têm de medir EXATAMENTE o que o packing CPU produz
//! (`pack_view_uniform`/`pack_params_uniform`) — um campo a mais no WGSL
//! sem mexer no packing dá desalinhamento silencioso na GPU.

use naga::valid::{Capabilities, ValidationFlags};

const WGSL: &str = viber::water_ssr::WATER_SSR_WGSL;

#[test]
fn shader_validates_under_naga() {
    let module = naga::front::wgsl::parse_str(WGSL)
        .unwrap_or_else(|e| panic!("parse WGSL falhou: {e:?}"));
    let mut validator = naga::valid::Validator::new(ValidationFlags::all(), Capabilities::all());
    let info = validator
        .validate(&module)
        .unwrap_or_else(|e| panic!("validação naga falhou: {e:?}"));

    let entry = module
        .entry_points
        .iter()
        .find(|e| e.name == "fragment")
        .expect("entry point `fragment` ausente");
    assert_eq!(entry.stage, naga::ShaderStage::Fragment);
}

/// Layout drift: os tamanhos dos uniforms no WGSL têm de bater com o
/// packing CPU (288 B de view, 160 B de params).
#[test]
fn uniform_struct_sizes_match_cpu_packing() {
    let module =
        naga::front::wgsl::parse_str(WGSL).expect("parse WGSL falhou no teste de layout");

    let size_of = |name: &str| -> Option<u32> {
        module.types.iter().find_map(|(_, ty)| {
            if ty.name.as_deref() == Some(name) {
                match &ty.inner {
                    naga::TypeInner::Struct { span, .. } => Some(*span),
                    _ => None,
                }
            } else {
                None
            }
        })
    };

    assert_eq!(size_of("SsrView"), Some(288), "SsrView tem de medir 288 B");
    assert_eq!(
        size_of("SsrParams"),
        Some(176),
        "SsrParams tem de medir 176 B (168 úteis alinhados a 16)"
    );
}

/// As cotas são campos EXPLÍCITOS s0..s7 (sem array indexado): indexação
/// dinâmica de arrays em uniform crashes o compilador NV 595.84 — esta
/// guarda impede a regressão para o padrão `array<vec4, 8>`.
#[test]
fn surfaces_are_unrolled_fields_no_dynamic_arrays() {
    let module =
        naga::front::wgsl::parse_str(WGSL).expect("parse WGSL falhou no teste das superfícies");
    let mut uniform_members_with_array = 0;
    let mut s_fields = 0;
    for (_, ty) in module.types.iter() {
        if let naga::TypeInner::Struct { members, .. } = &ty.inner {
            if ty.name.as_deref() != Some("SsrParams") {
                continue;
            }
            for member in members {
                if member
                        .name
                        .as_deref()
                        .is_some_and(|n| {
                            n.len() == 2 && n.starts_with('s') && n.chars().nth(1).map_or(false, |c| c.is_ascii_digit())
                        })
                {
                    s_fields += 1;
                }
                if matches!(module.types[member.ty].inner, naga::TypeInner::Array { .. }) {
                    uniform_members_with_array += 1;
                }
            }
        }
    }
    assert_eq!(uniform_members_with_array, 0, "sem arrays em uniforms");
    assert_eq!(s_fields, 8, "SsrParams tem de ter os 8 campos s0..s7");
}
