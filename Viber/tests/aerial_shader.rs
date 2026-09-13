//! Harness do shader da perspetiva aérea (`src/postfx.rs`, LOOP C).
//!
//! O WGSL é SELF-CONTAINED (zero `#import`), portanto o naga valida-o cru.
//! O contrato crítico é o LAYOUT: o struct `AerialUniform` tem de medir
//! EXACTAMENTE `AERIAL_UNIFORM_BYTES` (192 B — o packing CPU
//! `pack_aerial_uniform` escreve 2 mat4 + 3 vec4 por essa ordem); um campo
//! a mais no WGSL sem mexer no packing dá desalinhamento silencioso na GPU.

use naga::valid::{Capabilities, ValidationFlags};

const WGSL: &str = viber::postfx::AERIAL_WGSL;

#[test]
fn shader_validates_under_naga() {
    let module =
        naga::front::wgsl::parse_str(WGSL).unwrap_or_else(|e| panic!("parse WGSL falhou: {e:?}"));
    let mut validator = naga::valid::Validator::new(ValidationFlags::all(), Capabilities::all());
    validator
        .validate(&module)
        .unwrap_or_else(|e| panic!("validação naga falhou: {e:?}"));

    let entry = module
        .entry_points
        .iter()
        .find(|e| e.name == "fragment")
        .expect("entry point `fragment` ausente");
    assert_eq!(entry.stage, naga::ShaderStage::Fragment);
}

#[test]
fn uniform_struct_size_matches_cpu_packing() {
    let module = naga::front::wgsl::parse_str(WGSL).expect("parse WGSL falhou no teste de layout");

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

    assert_eq!(
        size_of("AerialUniform"),
        Some(viber::postfx::AERIAL_UNIFORM_BYTES as u32),
        "AerialUniform tem de medir {} B (o packing CPU escreve isto)",
        viber::postfx::AERIAL_UNIFORM_BYTES
    );
}

/// A matemática do GRADE no WGSL tem de espelhar a fn pura CPU
/// `aerial_grade` (mesmo smoothstep, mesmo mix, mesmo escurecer): se o
/// shader e o teste de pixel divergirem, o teste aprova um passe que não
/// existe. Verificações estruturais dos termos essenciais.
#[test]
fn shader_mirrors_the_cpu_grade() {
    assert!(WGSL.contains("dot(color, LUMA)"), "haze usa a luminância");
    assert!(
        WGSL.contains("luma * aer.tint.rgb"),
        "haze monocromo ao tint"
    );
    assert!(WGSL.contains("t * t * (3.0 - 2.0 * t)"), "rampa smoothstep");
    assert!(
        WGSL.contains("1.0 - aer.tint.w * f"),
        "escurecer de contraste"
    );
    // O céu (domo) NÃO é tocado: o skip por distância existe.
    assert!(WGSL.contains("dist >= aer.ramp.z"));
    assert!(
        WGSL.contains("d <= 0.0"),
        "clear do reverse-Z salta o passe"
    );
}
