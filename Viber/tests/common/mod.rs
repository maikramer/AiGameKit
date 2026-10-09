//! WESL harness partilhado pelos testes de shader (chunk/sky/water).
//!
//! Replica o pipeline do bevy 0.20 (`bevy_shader::shader_cache`): compila com
//! `imports: true, condcomp: true`, defs booleanas como *feature flags* e um
//! módulo especial `constants` com as defs numéricas
//! (`constants::MATERIAL_BIND_GROUP`). Os módulos `bevy_pbr`/`bevy_render`
//! são servidos por stubs mínimos declarados em cada harness.
//!
//! IMPORTANT: os stubs NÃO são o layout real da view/mesh do bevy — provam a
//! consistência interna do shader, não a compatibilidade de pipeline layout
//! (o mesmo contrato do antigo resolvedor textual de `#import`).

use std::borrow::Cow;
use wesl::syntax::{ModulePath, PathOrigin};
use wesl::{CompileOptions, Feature, ResolveError, Resolver};

const ROOT_PACKAGE: &str = "viber_shader";

pub struct HarnessResolver<'a> {
    pub source: &'a str,
    pub stubs: &'a [(&'static str, &'static str)],
    pub constants: &'a str,
}

impl Resolver for HarnessResolver<'_> {
    fn resolve_source(&self, path: &ModulePath) -> Result<Cow<'_, str>, ResolveError> {
        let path = &canonical_path(path);
        // O módulo especial `constants` (mesma convenção do shader_cache).
        if path.origin == PathOrigin::Package("constants".to_string()) && path.components.is_empty()
        {
            return Ok(Cow::Borrowed(self.constants));
        }
        if path.origin == PathOrigin::Package(ROOT_PACKAGE.to_string())
            && path.components.is_empty()
        {
            return Ok(Cow::Borrowed(self.source));
        }
        let key = path.to_string();
        for (module, stub) in self.stubs {
            if *module == key {
                return Ok(Cow::Borrowed(stub));
            }
        }
        Err(ResolveError::ModuleNotFound(
            path.clone(),
            "sem stub no harness — estende o contrato explícito".to_string(),
        ))
    }
}

/// Mesma canonicalização do `bevy_shader::shader_cache`: um import de pacote
/// externo feito de dentro do nosso (`import bevy_pbr::…` em `viber_shader`)
/// chega como `Package("viber_shader/bevy_pbr")` e resolve para `bevy_pbr`.
fn canonical_path(path: &ModulePath) -> ModulePath {
    match &path.origin {
        PathOrigin::Package(pkg) if pkg.contains('/') => ModulePath {
            origin: PathOrigin::Package(pkg.rsplit('/').next().unwrap().to_string()),
            components: path.components.clone(),
        },
        _ => path.clone(),
    }
}

/// Compila o `source` WESL (imports stubbed + defs booleanas) e devolve WGSL
/// pronto para o naga — o mesmo papel do antigo `standalone()` textual.
pub fn compile_wesl(
    source: &str,
    stubs: &[(&'static str, &'static str)],
    defines: &[&str],
) -> String {
    let mut options = CompileOptions {
        imports: true,
        condcomp: true,
        ..Default::default()
    };
    for def in defines {
        options
            .features
            .flags
            .insert(def.to_string(), Feature::Enable);
    }
    // O valor REAL que o bevy substitui no runtime (o grupo do material) —
    // manter a sincronizar com `bevy::pbr::MATERIAL_BIND_GROUP_INDEX`.
    let constants = format!(
        "const MATERIAL_BIND_GROUP = {}u;\n",
        bevy::pbr::MATERIAL_BIND_GROUP_INDEX
    );
    let resolver = HarnessResolver {
        source,
        stubs,
        constants: &constants,
    };
    let root = ModulePath {
        origin: PathOrigin::Package(ROOT_PACKAGE.to_string()),
        components: Vec::new(),
    };
    let compiled = wesl::compile(&root, &resolver, &wesl::EscapeMangler, &options)
        .unwrap_or_else(|error| panic!("WESL compile falhou:\n{error}"));
    compiled.syntax.to_string()
}

pub fn validate(wgsl: &str) -> naga::Module {
    let module = naga::front::wgsl::parse_str(wgsl)
        .unwrap_or_else(|error| panic!("{}", error.emit_to_string(wgsl)));
    naga::valid::Validator::new(
        naga::valid::ValidationFlags::all(),
        naga::valid::Capabilities::all(),
    )
    .validate(&module)
    .unwrap_or_else(|error| panic!("{}", error.emit_to_string(wgsl)));
    module
}
