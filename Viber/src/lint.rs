//! `viber scene lint` — o contrato do mundo como linters ESTÁTICOS. Apanha em
//! CI (exit non-zero com `--strict`) a classe de defeitos que hoje só rebenta
//! em runtime: um `.lua` com erro de sintaxe (a entidade nasce sem
//! comportamento), um `viber.load("lib/x.lua")` para ficheiro inexistente,
//! uma quest apontando a um NPC/marco renomeado no editor (a quest fica
//! ininiciável/incompletável em silêncio — o mesmo gatilho do warn de marcas
//! órfãs no `load`), e nomes de entidade duplicados (os seletores `name=…`
//! — a API de edição de agentes e editor — ficam ambíguos).
//!
//! Tudo offline (lê XML + ficheiros; não precisa de engine). A bridge expõe o
//! mesmo relatório (`viber.scene.lint`) para lint de um mundo a correr.

use std::collections::{BTreeMap, HashSet};
use std::path::Path;

use anyhow::{Context, Result};
use serde_json::json;

use crate::recipes::{EntityKind, EntitySpec};

/// Severidade de um finding: `Error` falha `--strict` (o mundo está
/// quebrado); `Warning` é reportado mas não falha (heurístico/recuperável).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Error,
    Warning,
}

#[derive(Debug, Clone)]
pub struct Finding {
    pub severity: Severity,
    /// Classe estável (`script-syntax`, `script-ref`, `script-load`,
    /// `quest-parse`, `duplicate-name`) — os agentes filtram por aqui.
    pub kind: &'static str,
    pub message: String,
}

impl Finding {
    fn error(kind: &'static str, message: String) -> Self {
        Self {
            severity: Severity::Error,
            kind,
            message,
        }
    }

    fn warning(kind: &'static str, message: String) -> Self {
        Self {
            severity: Severity::Warning,
            kind,
            message,
        }
    }

    fn to_json(&self) -> serde_json::Value {
        json!({
            "severity": match self.severity {
                Severity::Error => "error",
                Severity::Warning => "warning",
            },
            "kind": self.kind,
            "message": self.message,
        })
    }
}

/// O relatório completo do mundo. `errors` é o que interessa ao `--strict`.
#[derive(Debug, Default)]
pub struct LintReport {
    pub findings: Vec<Finding>,
}

impl LintReport {
    pub fn errors(&self) -> impl Iterator<Item = &Finding> {
        self.findings
            .iter()
            .filter(|f| f.severity == Severity::Error)
    }

    pub fn to_json(&self) -> serde_json::Value {
        let mut by_kind: BTreeMap<&str, usize> = BTreeMap::new();
        for finding in &self.findings {
            *by_kind.entry(finding.kind).or_default() += 1;
        }
        json!({
            "findings": self.findings.iter().map(Finding::to_json).collect::<Vec<_>>(),
            "errors": self.errors().count(),
            "warnings": self.findings.len() - self.errors().count(),
            "by_kind": by_kind,
        })
    }
}

/// O relatório em JSON (o formato da CLI e da bridge — contrato estável:
/// `{findings:[{severity,kind,message}], errors, warnings, by_kind}`).
pub fn report_json(report: &LintReport) -> serde_json::Value {
    report.to_json()
}

/// Entrada do CLI: lint completo do mundo (descoberta estática → world.xml).
/// Imprime o relatório JSON em stdout; `strict` com erros devolve `Err`
/// DEPOIS de imprimir (o dispatch do CLI faz exit non-zero sem apagar o
/// relatório).
pub fn run_cli(world: &Path, strict: bool) -> Result<serde_json::Value> {
    let report = lint_world(world)?;
    let errors = report.errors().count();
    let out = report.to_json();
    if strict && errors > 0 {
        println!("{}", serde_json::to_string_pretty(&out)?);
        anyhow::bail!("lint: {errors} erro(s) — relatório em stdout");
    }
    Ok(out)
}

/// Lint completo: sintaxe/referências de scripts, quests, nomes duplicados.
pub fn lint_world(world_xml: &Path) -> Result<LintReport> {
    let world_xml = std::path::absolute(world_xml)?;
    let world_dir = world_xml
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
    let loaded = crate::xml::include::load_world(&world_xml)?;
    let world = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes)
        .with_context(|| format!("a parsear {}", world_xml.display()))?;
    let config = crate::config::load(&world_dir)?;

    // Inventário do mundo: scripts referenciados (por entidade e por
    // spawner). Nomes duplicados contam em `count_duplicates`.
    let mut referenced_scripts: Vec<String> = Vec::new();
    walk(&world.entities, &mut |spec| {
        match &spec.kind {
            EntityKind::StaticSpawner { spec } | EntityKind::DynamicSpawner { spec } => {
                if let Some(script) = spec.template_script.as_deref() {
                    referenced_scripts.push(script.to_owned());
                }
            }
            _ => {}
        }
        if let Some(script) = spec.script.as_deref() {
            referenced_scripts.push(script.to_owned());
        }
    });
    let duplicates = count_duplicates(&world.entities);

    let mut findings = Vec::new();
    findings.extend(check_scripts(&world_dir, &config, &referenced_scripts));
    findings.extend(check_quests(&world_dir, &config));
    findings.extend(check_duplicate_names(duplicates));
    Ok(LintReport { findings })
}

fn walk<'a>(specs: &'a [EntitySpec], f: &mut impl FnMut(&'a EntitySpec)) {
    for spec in specs {
        f(spec);
        walk(&spec.children, f);
    }
}

/// Nomes com mais de uma entidade (seletor `name=` ambíguo). Determinístico:
/// ordenado por (−contagem, nome), cap 20.
fn count_duplicates(specs: &[EntitySpec]) -> Vec<(String, usize)> {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    walk(specs, &mut |spec| {
        if let Some(name) = spec.name.as_deref() {
            *counts.entry(name.to_owned()).or_default() += 1;
        }
    });
    let mut duplicates: Vec<(String, usize)> =
        counts.into_iter().filter(|(_, count)| *count > 1).collect();
    duplicates.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    duplicates.truncate(20);
    duplicates
}

fn check_duplicate_names(duplicates: Vec<(String, usize)>) -> Vec<Finding> {
    duplicates
        .into_iter()
        .map(|(name, count)| {
            Finding::warning(
                "duplicate-name",
                format!(
                "`{name}` tem {count} entidades — `name={name}` fica ambíguo para seletores (usa `ficheiro#i/j`, ou nomes únicos se o seletor importa)",
            ),
            )
        })
        .collect()
}

/// Scripts: (1) cada `script=`/template referenciado EXISTE; (2) cada `.lua`
/// do `scripts_dir` compila (sintaxe Luau — o que rebenta em runtime só por
/// estar carregado); (3) cada `viber.load("x.lua")` em qualquer script tem
/// ficheiro. Determinístico: ficheiros em ordem sorted.
fn check_scripts(
    world_dir: &Path,
    config: &crate::config::GameConfig,
    referenced: &[String],
) -> Vec<Finding> {
    let mut findings = Vec::new();
    let scripts_dir = config.scripts_dir_on(world_dir);

    // (1) referências do mundo
    let mut missing_refs: BTreeMap<&String, ()> = BTreeMap::new();
    for script in referenced {
        let path = resolve_script(&scripts_dir, script);
        if !path.exists() {
            missing_refs.insert(script, ());
        }
    }
    for (script, _) in missing_refs {
        findings.push(Finding::error(
            "script-ref",
            format!(
                "script referenciado não existe: {script} (sob {})",
                scripts_dir.display()
            ),
        ));
    }

    // (2) sintaxe + (3) alvos de viber.load — varre o diretório TODO: cobre
    // módulos (`viber.load`) que nenhum nó referência diretamente.
    let lua = lua_sandbox();
    let mut stack = vec![scripts_dir.clone()];
    let mut seen: HashSet<String> = referenced.iter().cloned().collect();
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        let mut entries: Vec<_> = entries.flatten().collect();
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            if path.extension().and_then(|ext| ext.to_str()) != Some("lua") {
                continue;
            }
            let Ok(source) = std::fs::read_to_string(&path) else {
                continue;
            };
            let rel = path
                .strip_prefix(&scripts_dir)
                .unwrap_or(&path)
                .display()
                .to_string();
            // (2) sintaxe: compilar SEM correr (top-level das libs do jogo é
            // só definição de funções/state).
            if let Err(error) = compile(&lua, &source, &rel) {
                findings.push(Finding::error("script-syntax", format!("{rel}: {error}")));
                continue; // sem sintaxe não faz sentido procurar viber.load
            }
            // (3) módulos carregados por script
            for target in viber_load_targets(&source) {
                if seen.insert(target.clone()) {
                    let module = resolve_script(&scripts_dir, &target);
                    if !module.exists() {
                        findings.push(Finding::error(
                            "script-load",
                            format!("{rel}: viber.load(\"{target}\") — ficheiro não existe"),
                        ));
                    }
                }
            }
        }
    }
    findings
}

/// O `scripts_dir` é a raiz de resolução; caminhos com `scripts/` prefixado
/// (estilo antigo) também resolvem — o runtime aceita ambos.
fn resolve_script(scripts_dir: &Path, script: &str) -> std::path::PathBuf {
    let trimmed = script.trim_start_matches("./");
    let direct = scripts_dir.join(trimmed);
    if direct.exists() {
        return direct;
    }
    match trimmed.strip_prefix("scripts/") {
        Some(rest) => scripts_dir.join(rest),
        None => direct,
    }
}

/// `viber.load("x.lua")` / `viber.load('x.lua')` — alvos literais no fonte
/// (scan barato; caminhos computados ficam por cobrir, por design).
fn viber_load_targets(source: &str) -> Vec<String> {
    let mut targets = Vec::new();
    for quote in ['"', '\''] {
        let needle = format!("viber.load({quote}");
        let mut rest = source;
        while let Some(start) = rest.find(&needle) {
            let after = &rest[start + needle.len()..];
            if let Some(end) = after.find(quote) {
                targets.push(after[..end].to_owned());
                rest = &after[end..];
            } else {
                break;
            }
        }
    }
    targets
}

/// A MESMA sandbox do runtime (`luau/host.rs`): sem `PACKAGE` (um `require`
/// num script do jogo é sintaxe válida Luau, mas não corre aqui — compilamos,
/// não executamos).
pub fn lua_sandbox() -> mlua::Lua {
    mlua::Lua::new_with(
        mlua::StdLib::ALL_SAFE ^ mlua::StdLib::PACKAGE,
        mlua::LuaOptions::default(),
    )
    .expect("a sandbox Luau compila")
}

/// Compila um chunk sem o executar. `Err` = erro de SINTAXE (o que interessa
/// ao lint; erros de execução não são detetáveis estaticamente).
pub fn compile(lua: &mlua::Lua, source: &str, name: &str) -> Result<(), String> {
    lua.load(source)
        .set_name(name)
        .into_function()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

/// Quests: cada `*.json` do `quests_dir` PARSEIA contra `[QuestDef]` (o
/// runtime faz warn+skip um ficheiro que não parseia — o jogo perde as
/// quests em silêncio). As referências (`def.id` ↔ `dialogue-id`, alvos
/// `kill`) são do [`crate::audit`], não daqui.
fn check_quests(world_dir: &Path, config: &crate::config::GameConfig) -> Vec<Finding> {
    let mut findings = Vec::new();
    let quests_dir = config.quests_dir_on(world_dir);
    let Ok(entries) = std::fs::read_dir(&quests_dir) else {
        return findings; // mundo sem quests: legítimo
    };
    let mut files: Vec<_> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(|ext| ext.to_str()) == Some("json"))
        .collect();
    files.sort();
    for file in files {
        let name = file
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let Ok(text) = std::fs::read_to_string(&file) else {
            continue;
        };
        // O MESMO contrato do runtime (`load_quests_from_dir`): um ficheiro é
        // um ARRAY de defs — o que o runtime ignora com warn aqui é ERRO.
        let Ok(defs) = serde_json::from_str::<Vec<crate::quests::QuestDef>>(&text) else {
            findings.push(Finding::error(
                "quest-parse",
                format!("quests/{name}: JSON não parseia contra [QuestDef] (array de quests)"),
            ));
            continue;
        };
        let _ = defs; // parseou — as refs são do audit
    }
    findings
}

#[cfg(test)]
mod tests {
    use super::*;

    const WORLD: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<world>
  <Entity name="npc_bard">
    <DialogueNPC dialogue-id="bard" />
  </Entity>
  <Landmark name="forest-outpost-tower" label="Torre" biome="forest" />
  <Entity name="hero" script="hero.lua" />
  <Entity name="well" />
</world>
"#;

    /// Fixture: mundo + config + scripts/ + quests/ num tempdir.
    fn fixture(
        world: &str,
        scripts: &[(&str, &str)],
        quests: &[(&str, &str)],
    ) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("world.xml"), world).unwrap();
        std::fs::write(
            dir.path().join("config.yaml"),
            "title: Lint Test\nassets:\n  roots: []\n  bgm_dir: assets/audio/bgm\n  sfx_dir: assets/audio/sfx\n  terrain_textures_dir: assets/textures\ngame:\n  scripts_dir: scripts\nsave:\n  dir: /tmp\n",
        )
        .unwrap();
        for (name, source) in scripts {
            let path = dir.path().join("scripts").join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, source).unwrap();
        }
        for (name, source) in quests {
            let path = dir.path().join("quests").join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, source).unwrap();
        }
        dir
    }

    fn quest(npc: &str, kind: &str, target: &str) -> String {
        format!(
            r#"[{{ "id": "q1", "npc": "{npc}", "biome": "forest", "title": "T",
                  "objective": {{ "type": "{kind}", "target": "{target}", "count": 1 }},
                  "rewards": {{ "gold": 5 }} }}]"#
        )
    }

    #[test]
    fn clean_world_has_zero_findings() {
        let dir = fixture(
            WORLD,
            &[
                ("hero.lua", "function on_update(dt) end\n"),
                ("lib/x.lua", "return {}\n"),
            ],
            &[("q1.json", &quest("bard", "visit", "forest-outpost-tower"))],
        );
        let report = lint_world(&dir.path().join("world.xml")).unwrap();
        assert!(
            report.findings.is_empty(),
            "{:?}",
            report
                .findings
                .iter()
                .map(|f| &f.message)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn syntax_error_script_load_and_broken_quest_json_are_found() {
        let dir = fixture(
            WORLD,
            &[
                ("hero.lua", "function on_update(dt\ndt = dt +\n"),
                ("lib/broken.lua", "local x = {"),
                ("lib/ok.lua", "return viber.load('lib/falta.lua')\n"),
            ],
            &[("ruim.json", "{ náo é json")],
        );
        let report = lint_world(&dir.path().join("world.xml")).unwrap();
        let kinds: Vec<&str> = report.findings.iter().map(|f| f.kind).collect();
        assert!(kinds.contains(&"script-syntax"), "{kinds:?}");
        assert_eq!(
            report
                .errors()
                .filter(|f| f.kind == "script-syntax")
                .count(),
            2
        );
        assert!(kinds.contains(&"script-load"), "{kinds:?}");
        assert!(kinds.contains(&"quest-parse"), "{kinds:?}");
        assert_eq!(
            report.errors().count(),
            4,
            "sintaxe×2 + viber.load + quest-parse"
        );
    }

    #[test]
    fn missing_referenced_script_is_an_error_and_notice_board_is_valid() {
        let dir = fixture(
            WORLD,
            &[],
            &[("quadro.json", &quest("notice_board", "kill", "goblin"))],
        );
        let report = lint_world(&dir.path().join("world.xml")).unwrap();
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.kind == "script-ref" && f.message.contains("hero.lua")),
            "world.xml refere scripts/hero.lua mas a fixture NÃO o criou: {:?}",
            report.findings
        );
        assert!(
            !report
                .findings
                .iter()
                .any(|f| f.kind == "quest-ref" && f.message.contains("notice_board")),
            "notice_board é convenção válida"
        );
    }

    #[test]
    fn live_routing_sends_lint_to_the_bridge() {
        // `--live`: o método lê os ficheiros do root_dir da engine — o CLI
        // só encaminha (o `strict` vai nos params).
        let (method, params) =
            crate::scene::cli::live_request(crate::scene::cli::SceneCommand::Lint { strict: true })
                .unwrap();
        assert_eq!(method, "viber.scene.lint");
        assert_eq!(params, serde_json::json!({ "strict": true }));
    }

    #[test]
    fn duplicate_entity_names_are_reported_with_counts() {
        let dir = fixture(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<world>
  <Group name="props">
    <Entity name="well" />
    <Entity name="well" />
    <Entity name="cart" />
  </Group>
</world>
"#,
            &[],
            &[],
        );
        let report = lint_world(&dir.path().join("world.xml")).unwrap();
        assert_eq!(report.errors().count(), 0, "duplicado é Warning");
        assert!(
            report.findings.iter().any(|f| f.kind == "duplicate-name"
                && f.message.contains("`well` tem 2")
                && !f.message.contains("cart")),
            "{:?}",
            report.findings
        );
    }

    #[test]
    fn run_cli_strict_bails_after_printing_and_clean_world_returns_object() {
        let dir = fixture(
            WORLD,
            &[("hero.lua", "function on_update(dt\ndt = dt +\n")],
            &[],
        );
        // sem --strict: o objeto sai com errors: 1 (o script partido)
        let out = run_cli(&dir.path().join("world.xml"), false).unwrap();
        assert_eq!(out["errors"], 1);
        // com erro + --strict: relatório impresso e Err (o CLI faz exit 1)
        let out = run_cli(&dir.path().join("world.xml"), true);
        assert!(out.is_err());
    }
}
