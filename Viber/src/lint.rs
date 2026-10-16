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

use std::collections::{BTreeMap, HashMap, HashSet};
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
    let world_dir = crate::project::root_of(&world_xml);
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
    findings.extend(check_script_props(
        &config.scripts_dir_on(&world_dir),
        &world.entities,
    ));
    findings.extend(check_signals(
        &config.scripts_dir_on(&world_dir),
        &world.entities,
    ));
    // Num projeto com cenas a biblioteca de cinemáticas é a UNIÃO (principal, autoloads,
    // cenas): um script de uma cena pode tocar uma cinemática declarada noutra.
    let mut cinematic_nodes = loaded.nodes.clone();
    cinematic_nodes.extend(project_cinematics(&world_xml));
    findings.extend(check_cinematics(
        &cinematic_nodes,
        &config.scripts_dir_on(&world_dir),
        &world.entities,
    ));
    findings.extend(check_quests(&world_dir, &config));
    findings.extend(check_duplicate_names(duplicates));
    findings.extend(check_project(
        &world_xml,
        &config.scripts_dir_on(&world_dir),
    ));
    Ok(LintReport { findings })
}

/// Os `<Cinematic>` das OUTRAS cenas e dos autoloads do projeto de `world_xml` (vazio fora
/// de um projeto declarado).
fn project_cinematics(world_xml: &Path) -> Vec<crate::xml::XmlNode> {
    let project = crate::project::Project::discover(world_xml);
    if !project.declared {
        return Vec::new();
    }
    let this = world_xml
        .canonicalize()
        .unwrap_or_else(|_| world_xml.to_path_buf());
    fn rec(nodes: Vec<crate::xml::XmlNode>, out: &mut Vec<crate::xml::XmlNode>) {
        for node in nodes {
            if node.tag.eq_ignore_ascii_case("cinematic") {
                out.push(node);
            } else {
                rec(node.children, out);
            }
        }
    }
    let mut out = Vec::new();
    for file in project
        .scene_files()
        .into_iter()
        .chain(project.autoload_paths())
    {
        if file.canonicalize().unwrap_or_else(|_| file.clone()) == this {
            continue;
        }
        if let Ok(loaded) = crate::xml::include::load_world_in(&file, &project.root) {
            rec(loaded.nodes, &mut out);
        }
    }
    out
}

/// Projeto com cenas (`project:` no config): portais com alvo e entrada que existem, cenas
/// com entrada, autoloads sem recursos de mundo, e os `viber.change_scene("…")` literais dos
/// scripts a apontar para cenas que existem. Corre sobre o PROJETO inteiro, seja qual for a
/// cena que se pediu.
fn check_project(world_xml: &Path, scripts_dir: &Path) -> Vec<Finding> {
    let project = crate::project::Project::discover(world_xml);
    if !project.declared {
        return Vec::new();
    }
    let mut findings = Vec::new();
    let scenes = project.list_scenes();
    let by_id: HashMap<&str, &crate::project::SceneInfo> =
        scenes.iter().map(|s| (s.id.as_str(), s)).collect();
    for scene in &scenes {
        if let Some(error) = &scene.error {
            findings.push(Finding::error(
                "scene-parse",
                format!("cena `{}` ({}) não carrega: {error}", scene.id, scene.path),
            ));
            continue;
        }
        if !scene.main && scene.entries.is_empty() {
            findings.push(Finding::warning(
                "scene-no-entry",
                format!(
                    "cena `{}` sem `<SceneEntry>` nem `<ScenePortal>` — quem lá chegar fica onde estava",
                    scene.id
                ),
            ));
        }
        for portal in &scene.portals {
            let label = if portal.name.is_empty() {
                "<ScenePortal> sem nome".to_string()
            } else {
                format!("`{}`", portal.name)
            };
            let Some(target) = by_id.get(portal.target.as_str()) else {
                findings.push(Finding::error(
                    "scene-portal-target",
                    format!(
                        "cena `{}`: portal {label} aponta para a cena `{}`, que não existe (cenas: {})",
                        scene.id,
                        portal.target,
                        scenes.iter().map(|s| s.id.as_str()).collect::<Vec<_>>().join(", ")
                    ),
                ));
                continue;
            };
            if let Some(entry) = portal.entry.as_deref().filter(|e| *e != "@back")
                && !target.entries.iter().any(|known| known.name == entry)
            {
                findings.push(Finding::error(
                    "scene-portal-entry",
                    format!(
                        "cena `{}`: portal {label} chega à entrada `{entry}` da cena `{}`, que não existe (entradas: {})",
                        scene.id,
                        target.id,
                        target.entries.iter().map(|e| e.name.as_str()).collect::<Vec<_>>().join(", ")
                    ),
                ));
            }
        }
    }
    const WORLD_TAGS: &[&str] = &[
        "terrain",
        "sky",
        "navmesh",
        "daycycle",
        "weather",
        "worldborder",
        "interiorscene",
        "biomeregion",
        "spawngate",
    ];
    for file in project.autoload_paths() {
        match crate::xml::include::load_world_in(&file, &project.root) {
            Ok(loaded) => {
                let mut tags = Vec::new();
                fn scan(nodes: &[crate::xml::XmlNode], out: &mut Vec<String>) {
                    for node in nodes {
                        let lower = node.tag.to_ascii_lowercase();
                        if WORLD_TAGS.contains(&lower.as_str()) {
                            out.push(node.tag.clone());
                        }
                        scan(&node.children, out);
                    }
                }
                scan(&loaded.nodes, &mut tags);
                for tag in tags {
                    findings.push(Finding::warning(
                        "autoload-world-tag",
                        format!(
                            "autoload {}: `<{tag}>` é de uma cena (a cena carregada substitui-o) — move-o para a cena",
                            file.display()
                        ),
                    ));
                }
            }
            Err(error) => findings.push(Finding::error(
                "autoload-parse",
                format!("autoload {} não carrega: {error:#}", file.display()),
            )),
        }
    }
    // `viber.change_scene("id"…)` literais.
    let mut stack = vec![scripts_dir.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(read) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in read.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            if path.extension().and_then(|e| e.to_str()) != Some("lua") {
                continue;
            }
            let Ok(source) = std::fs::read_to_string(&path) else {
                continue;
            };
            for (line_no, line) in source.lines().enumerate() {
                let mut rest = line;
                while let Some(at) = rest.find("change_scene(") {
                    rest = &rest[at + "change_scene(".len()..];
                    let trimmed = rest.trim_start();
                    let Some(quote) = trimmed.chars().next().filter(|c| *c == '"' || *c == '\'')
                    else {
                        continue;
                    };
                    let body = &trimmed[1..];
                    let Some(end) = body.find(quote) else {
                        continue;
                    };
                    let id = &body[..end];
                    if !by_id.contains_key(id) {
                        findings.push(Finding::error(
                            "scene-change-target",
                            format!(
                                "{}:{}: viber.change_scene(\"{id}\") — cena desconhecida",
                                path.display(),
                                line_no + 1
                            ),
                        ));
                    }
                }
            }
        }
    }
    findings
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
            for error in crate::luau::exports::parse_exports(&source).errors {
                findings.push(Finding::error(
                    "script-export",
                    format!("{rel}: --@export {error}"),
                ));
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

/// Props exportados (`prop-*` ↔ `--@export`): cada valor de instância tem de
/// ser exportado pelo script (`script-prop-unknown` — typos), respeitar o
/// tipo/intervalo/enum (`script-prop-type`) e, num `entity`, nomear uma
/// entidade do mundo (`script-prop-entity`). Props num nó sem script são
/// órfãos (`script-prop-orphan`).
fn check_script_props(scripts_dir: &Path, entities: &[EntitySpec]) -> Vec<Finding> {
    let mut names: HashSet<String> = HashSet::new();
    // (rótulo, script, props) de cada uso.
    type PropUse = (String, Option<String>, Vec<(String, String)>);
    let mut uses: Vec<PropUse> = Vec::new();
    walk(entities, &mut |spec| {
        if let Some(name) = &spec.name {
            names.insert(name.clone());
        }
        let label = spec_label(spec);
        if let EntityKind::StaticSpawner { spec: spawner }
        | EntityKind::DynamicSpawner { spec: spawner } = &spec.kind
            && !spawner.template_props.is_empty()
        {
            uses.push((
                format!("{label} (template)"),
                spawner.template_script.clone(),
                spawner.template_props.clone(),
            ));
        }
        if !spec.props.is_empty() {
            uses.push((label, spec.script.clone(), spec.props.clone()));
        }
    });
    let mut cache: HashMap<String, Option<crate::luau::exports::ScriptExports>> = HashMap::new();
    let mut findings = Vec::new();
    for (label, script, props) in uses {
        let Some(script) = script else {
            findings.push(Finding::warning(
                "script-prop-orphan",
                format!("{label}: prop-* sem script= — ninguém os lê"),
            ));
            continue;
        };
        let exports = cache.entry(script.clone()).or_insert_with(|| {
            std::fs::read_to_string(resolve_script(scripts_dir, &script))
                .ok()
                .map(|source| crate::luau::exports::parse_exports(&source))
        });
        let Some(exports) = exports else {
            continue; // script em falta: já é `script-ref`
        };
        for name in exports.unknown(&props) {
            findings.push(Finding::error(
                "script-prop-unknown",
                format!("{label}: prop-{name} não é exportado por {script}"),
            ));
        }
        for prop in exports.resolve(&props) {
            if let Some(error) = &prop.error {
                findings.push(Finding::error(
                    "script-prop-type",
                    format!("{label}: prop-{} ({}): {error}", prop.def.name, prop.def.ty),
                ));
            } else if prop.def.ty == crate::luau::exports::PropType::Entity
                && let Some(crate::luau::exports::PropValue::Text(target)) = &prop.value
                && !names.contains(target)
            {
                findings.push(Finding::warning(
                    "script-prop-entity",
                    format!(
                        "{label}: prop-{} = `{target}` não nomeia nenhuma entidade do mundo",
                        prop.def.name
                    ),
                ));
            }
        }
    }
    findings
}

/// `name=` quando existe; senão o nó de origem (`ficheiro#caminho`).
fn spec_label(spec: &EntitySpec) -> String {
    spec.name.clone().unwrap_or_else(|| match &spec.source {
        Some(origin) => format!(
            "{}#{}",
            origin.file.file_name().unwrap_or_default().display(),
            origin
                .path
                .iter()
                .map(u32::to_string)
                .collect::<Vec<_>>()
                .join("/")
        ),
        None => "<sem nome>".to_owned(),
    })
}

/// Sinais e grupos (`--@signal` ↔ `viber.emit` ↔ `connect-<sinal>` ↔ `groups=`):
/// * `connect-syntax` — valor que não é `alvo:método; @grupo:método`;
/// * `connect-orphan` — `connect-*` num nó sem script (nada emite);
/// * `connect-signal` — o script do emissor não declara o sinal;
/// * `connect-target` — nome que não existe (erro) / grupo sem membros (aviso);
/// * `connect-method` — o alvo não tem script, ou o script não define o método;
/// * `script-emit` — `viber.emit("x")` literal de um sinal não declarado.
fn check_signals(scripts_dir: &Path, entities: &[EntitySpec]) -> Vec<Finding> {
    use crate::luau::exports::{ConnTarget, ScriptExports, parse_connections, parse_exports};
    // nome → scripts das entidades com esse nome; grupo → scripts dos membros
    // (`None` = membro sem script).
    let mut names: HashMap<String, Vec<Option<String>>> = HashMap::new();
    let mut groups: HashMap<String, Vec<Option<String>>> = HashMap::new();
    // (rótulo, script do emissor, ligações)
    type Emitter = (String, Option<String>, Vec<(String, String)>);
    let mut emitters: Vec<Emitter> = Vec::new();
    let mut scripts: HashSet<String> = HashSet::new();
    walk(entities, &mut |spec| {
        let label = spec_label(spec);
        if let Some(name) = &spec.name {
            names
                .entry(name.clone())
                .or_default()
                .push(spec.script.clone());
        }
        for group in &spec.links.groups {
            groups
                .entry(group.clone())
                .or_default()
                .push(spec.script.clone());
        }
        if let Some(script) = &spec.script {
            scripts.insert(script.clone());
        }
        if !spec.links.connections.is_empty() {
            emitters.push((
                label.clone(),
                spec.script.clone(),
                spec.links.connections.clone(),
            ));
        }
        if let EntityKind::StaticSpawner { spec: spawner }
        | EntityKind::DynamicSpawner { spec: spawner } = &spec.kind
        {
            for group in &spawner.template_links.groups {
                groups
                    .entry(group.clone())
                    .or_default()
                    .push(spawner.template_script.clone());
            }
            if !spawner.template_links.connections.is_empty() {
                emitters.push((
                    format!("{label} (template)"),
                    spawner.template_script.clone(),
                    spawner.template_links.connections.clone(),
                ));
            }
        }
    });
    let mut cache: HashMap<String, Option<(ScriptExports, String)>> = HashMap::new();
    let mut load = |script: &str| -> Option<(ScriptExports, String)> {
        cache
            .entry(script.to_owned())
            .or_insert_with(|| {
                std::fs::read_to_string(resolve_script(scripts_dir, script))
                    .ok()
                    .map(|source| (parse_exports(&source), source))
            })
            .clone()
    };
    let mut findings = Vec::new();
    // `viber.emit("x")` literal sem `--@signal x`.
    let mut sorted: Vec<&String> = scripts.iter().collect();
    sorted.sort();
    for script in sorted {
        let Some((exports, source)) = load(script) else {
            continue;
        };
        for signal in emit_literals(&source) {
            if exports.signal(&signal).is_none() {
                findings.push(Finding::error(
                    "script-emit",
                    format!("{script}: viber.emit(\"{signal}\") sem `--@signal {signal}`"),
                ));
            }
        }
    }
    for (label, script, connections) in emitters {
        let exports = script.as_deref().and_then(&mut load).map(|(e, _)| e);
        for (signal, raw) in connections {
            let parsed = match parse_connections(&raw) {
                Ok(parsed) => parsed,
                Err(error) => {
                    findings.push(Finding::error(
                        "connect-syntax",
                        format!("{label}: connect-{signal}: {error}"),
                    ));
                    continue;
                }
            };
            match (&script, &exports) {
                (None, _) => findings.push(Finding::error(
                    "connect-orphan",
                    format!("{label}: connect-{signal} sem script= — nada emite o sinal"),
                )),
                (Some(script), Some(exports)) if exports.signal(&signal).is_none() => findings
                    .push(Finding::error(
                        "connect-signal",
                        format!(
                            "{label}: connect-{signal}: {script} não declara `--@signal {signal}`"
                        ),
                    )),
                _ => {}
            }
            for conn in parsed {
                let members: Vec<Option<String>> = match &conn.target {
                    ConnTarget::Name(name) if name == "self" => vec![script.clone()],
                    ConnTarget::Name(name) => match names.get(name) {
                        Some(found) => found.clone(),
                        None => {
                            findings.push(Finding::error(
                                "connect-target",
                                format!("{label}: connect-{signal} → `{name}` não nomeia nenhuma entidade"),
                            ));
                            continue;
                        }
                    },
                    ConnTarget::Group(group) => match groups.get(group) {
                        Some(found) => found.clone(),
                        None => {
                            findings.push(Finding::warning(
                                "connect-target",
                                format!("{label}: connect-{signal} → grupo `@{group}` sem membros"),
                            ));
                            continue;
                        }
                    },
                };
                let mut reported = HashSet::new();
                for member in members {
                    let problem = match member.as_deref() {
                        None => Some(format!(
                            "{label}: connect-{signal} → `{}` sem script",
                            conn.target
                        )),
                        Some(target_script) => match load(target_script) {
                            Some((target_exports, _))
                                if !target_exports.has_method(&conn.method) =>
                            {
                                Some(format!(
                                    "{label}: connect-{signal} → {target_script} não define `function {}(…)`",
                                    conn.method
                                ))
                            }
                            _ => None,
                        },
                    };
                    if let Some(problem) = problem
                        && reported.insert(problem.clone())
                    {
                        findings.push(Finding::error("connect-method", problem));
                    }
                }
            }
        }
    }
    findings
}

/// Cinemáticas (`<Cinematic>`):
/// * `cinematic-parse` — o XML não forma uma cinemática (sem id, sem planos, …);
/// * `cinematic-duplicate` — dois `<Cinematic>` com o mesmo id;
/// * `cinematic-sound` — `<Sound clip>` que a engine não conhece;
/// * `cinematic-anchor` — `anchor="nome"` sem entidade com esse `name`;
/// * `cinematic-ref` — `viber.cinematic("x")`, `intro = "x"` (lib/fsm) ou
///   `prop-cinematic="x"` para um id que não existe.
fn check_cinematics(
    nodes: &[crate::xml::XmlNode],
    scripts_dir: &Path,
    entities: &[EntitySpec],
) -> Vec<Finding> {
    let mut findings = Vec::new();
    let mut ids: HashMap<String, usize> = HashMap::new();
    let mut specs = Vec::new();
    fn rec<'a>(nodes: &'a [crate::xml::XmlNode], out: &mut Vec<&'a crate::xml::XmlNode>) {
        for node in nodes {
            if node.tag.eq_ignore_ascii_case("cinematic") {
                out.push(node);
            } else {
                rec(&node.children, out);
            }
        }
    }
    let mut raw = Vec::new();
    rec(nodes, &mut raw);
    for node in raw {
        match crate::cinematic::parse(node) {
            Ok(spec) => {
                *ids.entry(spec.id.clone()).or_default() += 1;
                specs.push(spec);
            }
            Err(error) => findings.push(Finding::error("cinematic-parse", error)),
        }
    }
    for (id, count) in &ids {
        if *count > 1 {
            findings.push(Finding::error(
                "cinematic-duplicate",
                format!("<Cinematic id=\"{id}\"> declarada {count}×"),
            ));
        }
    }
    let mut names: HashSet<String> = HashSet::new();
    walk(entities, &mut |spec| {
        if let Some(name) = &spec.name {
            names.insert(name.clone());
        }
    });
    for spec in &specs {
        for cue in &spec.sounds {
            if crate::luau::sfx_clip_from_str(&cue.clip).is_none() {
                findings.push(Finding::error(
                    "cinematic-sound",
                    format!("{}: <Sound clip=\"{}\"> desconhecido", spec.id, cue.clip),
                ));
            }
        }
        for shot in &spec.shots {
            if let crate::cinematic::Anchor::Named(name) = &shot.anchor
                && !names.contains(name)
            {
                findings.push(Finding::error(
                    "cinematic-anchor",
                    format!("{}: anchor=\"{name}\" não nomeia nenhuma entidade", spec.id),
                ));
            }
        }
    }
    // Referências: literais nos scripts e `prop-cinematic` nos nós.
    let mut refs: Vec<(String, String)> = Vec::new();
    if let Ok(entries) = walk_lua(scripts_dir) {
        for path in entries {
            let Ok(source) = std::fs::read_to_string(&path) else {
                continue;
            };
            let label = path
                .strip_prefix(scripts_dir)
                .unwrap_or(&path)
                .display()
                .to_string();
            for needle in ["viber.cinematic(\"", "intro = \""] {
                let mut rest = source.as_str();
                while let Some(start) = rest.find(needle) {
                    let after = &rest[start + needle.len()..];
                    let Some(end) = after.find('"') else { break };
                    refs.push((label.clone(), after[..end].to_owned()));
                    rest = &after[end..];
                }
            }
        }
    }
    walk(entities, &mut |spec| {
        if let Some((_, id)) = spec.props.iter().find(|(k, _)| k == "cinematic")
            && !id.is_empty()
        {
            refs.push((spec_label(spec), id.clone()));
        }
    });
    for (owner, id) in refs {
        if !ids.contains_key(&id) {
            findings.push(Finding::error(
                "cinematic-ref",
                format!("{owner}: cinemática `{id}` não existe (viber cinematic list)"),
            ));
        }
    }
    findings
}

/// Todos os `.lua` debaixo de `dir` (recursivo).
fn walk_lua(dir: &Path) -> std::io::Result<Vec<std::path::PathBuf>> {
    let mut out = Vec::new();
    for entry in std::fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            out.extend(walk_lua(&path)?);
        } else if path.extension().is_some_and(|e| e == "lua") {
            out.push(path);
        }
    }
    out.sort();
    Ok(out)
}

/// `viber.emit("x"` / `viber.emit('x'` — sinais emitidos por literal.
fn emit_literals(source: &str) -> Vec<String> {
    let mut out = Vec::new();
    for quote in ['"', '\''] {
        let needle = format!("viber.emit({quote}");
        let mut rest = source;
        while let Some(start) = rest.find(&needle) {
            let after = &rest[start + needle.len()..];
            let Some(end) = after.find(quote) else {
                break;
            };
            if !out.iter().any(|s| s == &after[..end]) {
                out.push(after[..end].to_owned());
            }
            rest = &after[end..];
        }
    }
    out
}

/// O `scripts_dir` é a raiz de resolução; caminhos com `scripts/` prefixado
/// (estilo antigo) também resolvem — o runtime aceita ambos.
pub fn resolve_script(scripts_dir: &Path, script: &str) -> std::path::PathBuf {
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
    fn script_props_are_checked_against_exports() {
        let dir = fixture(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<world>
  <Entity name="ok" script="npc.lua" prop-speed="2" prop-home="well" />
  <Entity name="typo" script="npc.lua" prop-sped="2" />
  <Entity name="bad" script="npc.lua" prop-speed="9" prop-home="ninguem" />
  <Entity name="orphan" prop-speed="1" />
  <Entity name="well" />
</world>
"#,
            &[(
                "npc.lua",
                "--@export speed: number(0..5) = 1\n--@export home: entity\n--@export x: float\nfunction on_update(dt) end\n",
            )],
            &[],
        );
        let report = lint_world(&dir.path().join("world.xml")).unwrap();
        let found = |kind: &str, needle: &str| {
            report
                .findings
                .iter()
                .any(|f| f.kind == kind && f.message.contains(needle))
        };
        assert!(found("script-export", "npc.lua"), "{:?}", report.findings);
        assert!(found("script-prop-unknown", "typo: prop-sped"));
        assert!(found("script-prop-type", "bad: prop-speed"));
        assert!(found("script-prop-entity", "ninguem"));
        assert!(found("script-prop-orphan", "orphan"));
        assert!(
            !report.findings.iter().any(|f| f.message.starts_with("ok:")),
            "{:?}",
            report.findings
        );
    }

    #[test]
    fn signal_connections_are_checked() {
        let dir = fixture(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<world>
  <Entity name="chest" script="chest.lua" connect-opened="door:open; @guards:alarm" />
  <Entity name="door" script="door.lua" groups="guards" />
  <Entity name="mute" script="door.lua" groups="guards" connect-opened="door:open" />
  <Entity name="bad" script="chest.lua" connect-opened="ninguem:open; door:fly; @vazio:x" connect-shut="door:open" connect-x="semmetodo" />
  <Entity name="plain" groups="guards" connect-opened="door:open" />
</world>
"#,
            &[
                (
                    "chest.lua",
                    "--@signal opened(gold)\nfunction on_update(dt) viber.emit(\"opened\", 1) viber.emit('typo') end\n",
                ),
                ("door.lua", "function open() end\nfunction alarm() end\n"),
            ],
            &[],
        );
        let report = lint_world(&dir.path().join("world.xml")).unwrap();
        let found = |kind: &str, needle: &str| {
            report
                .findings
                .iter()
                .any(|f| f.kind == kind && f.message.contains(needle))
        };
        assert!(found("script-emit", "typo"), "{:?}", report.findings);
        assert!(found("connect-signal", "mute: connect-opened"));
        assert!(found("connect-target", "ninguem"));
        assert!(found("connect-target", "@vazio"));
        assert!(found("connect-method", "function fly"));
        assert!(found("connect-signal", "bad: connect-shut"));
        assert!(found("connect-syntax", "bad: connect-x"));
        assert!(found("connect-orphan", "plain"));
        // o grupo `guards` inclui `plain` (sem script): alarm não chega lá
        assert!(found("connect-method", "`@guards` sem script"));
        assert!(
            !report
                .findings
                .iter()
                .any(|f| f.message.starts_with("door:")),
            "{:?}",
            report.findings
        );
    }

    #[test]
    fn cinematics_are_checked() {
        let dir = fixture(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<world>
  <Entity name="altar" />
  <Entity name="trig" script="trigger.lua" prop-cinematic="sumida" />
  <Cinematic id="ok"><Shot from="0 1 2" look="0 0 0" anchor="altar" /><Sound clip="roar" /></Cinematic>
  <Cinematic id="ok"><Shot from="0 1 2" look="0 0 0" /></Cinematic>
  <Cinematic id="mau"><Shot from="0 1 2" look="0 0 0" anchor="ninguem" /><Sound clip="kazoo" /></Cinematic>
  <Cinematic id="vazia" />
</world>
"#,
            &[
                (
                    "trigger.lua",
                    "--@export cinematic: string = \"\"\nfunction on_update(dt) end\n",
                ),
                (
                    "boss.lua",
                    "local f = { intro = \"fantasma\" }\nfunction on_update(dt) viber.cinematic(\"ok\") end\n",
                ),
            ],
            &[],
        );
        let report = lint_world(&dir.path().join("world.xml")).unwrap();
        let found = |kind: &str, needle: &str| {
            report
                .findings
                .iter()
                .any(|f| f.kind == kind && f.message.contains(needle))
        };
        assert!(found("cinematic-duplicate", "ok"), "{:?}", report.findings);
        assert!(found("cinematic-parse", "vazia"));
        assert!(found("cinematic-sound", "kazoo"));
        assert!(found("cinematic-anchor", "ninguem"));
        assert!(found("cinematic-ref", "fantasma"));
        assert!(found("cinematic-ref", "sumida"));
        assert!(!found("cinematic-ref", "`ok`"));
        assert!(!found("cinematic-anchor", "altar"));
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

#[cfg(test)]
mod project_tests {
    use super::*;

    #[test]
    fn project_lint_catches_broken_portals_and_scene_literals() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(
            root.join("config.yaml"),
            "assets:\n  roots: []\n  bgm_dir: b\n  sfx_dir: s\n  terrain_textures_dir: t\n\
             game:\n  scripts_dir: scripts\nsave:\n  dir: /tmp\nproject:\n  scenes: scenes\n",
        )
        .unwrap();
        std::fs::create_dir_all(root.join("scenes")).unwrap();
        std::fs::create_dir_all(root.join("scripts")).unwrap();
        std::fs::write(
            root.join("world.xml"),
            r#"<world static="true">
  <ScenePortal name="a" target="house" entry="from-town"/>
  <ScenePortal name="b" target="house" entry="nope"/>
  <ScenePortal name="c" target="ghost"/>
</world>"#,
        )
        .unwrap();
        std::fs::write(
            root.join("scenes/house.xml"),
            r#"<world static="true"><SceneEntry name="from-town"/><ScenePortal name="exit" target="main" entry="@back"/></world>"#,
        )
        .unwrap();
        std::fs::write(root.join("scenes/empty.xml"), r#"<world static="true"/>"#).unwrap();
        std::fs::write(
            root.join("scripts/door.lua"),
            "viber.change_scene(\"house\")\nviber.change_scene('cellar', 'x')\n",
        )
        .unwrap();
        let findings = check_project(&root.join("world.xml"), &root.join("scripts"));
        let kinds: Vec<(&str, &str)> = findings
            .iter()
            .map(|f| (f.kind, f.message.as_str()))
            .collect();
        let has =
            |kind: &str, needle: &str| kinds.iter().any(|(k, m)| *k == kind && m.contains(needle));
        assert!(has("scene-portal-entry", "`nope`"), "{kinds:?}");
        assert!(has("scene-portal-target", "`ghost`"), "{kinds:?}");
        assert!(has("scene-no-entry", "`empty`"), "{kinds:?}");
        assert!(has("scene-change-target", "cellar"), "{kinds:?}");
        assert!(!has("scene-change-target", "\"house\""), "{kinds:?}");
        assert!(!has("scene-portal-entry", "`from-town`"), "{kinds:?}");
        assert_eq!(findings.len(), 4, "{kinds:?}");
    }
}
