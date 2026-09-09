//! `config.yaml` — o contrato de PATHS entre o jogo e a engine.
//!
//! Todo jogo (a pasta que contém o `world.xml`) TEM de ter um `config.yaml`
//! ao lado; é ele que declara onde vivem os assets e os diretórios do jogo —
//! a engine não tem layouts de filesystem hardcoded (o antigo walk de
//! ancestros à procura de `examples/shared-assets/public` morreu aqui).
//! `viber create` gera um exemplo completo; o schema está documentado em
//! `docs/ASSETS.md`.
//!
//! # Bases de resolução (distintas — não misturar)
//!
//! * `assets.*` resolve contra as **asset roots por ordem** (a pasta do jogo
//!   é SEMPRE a 1.ª; as `roots` extra vêm a seguir): são paths de
//!   AssetServer, o `MultiRootFileReader` tenta cada root por ordem.
//! * `game.*` resolve contra a **pasta do jogo** (filesystem direto).
//! * `save.dir` é **absoluto** (`~` expandido no load).
//!
//! O que o config remapeia são DIRETÓRIOS; os nomes de ficheiro dentro deles
//! (`combat/swing.ogg`, `{layer}.ogg`, `{alias}/albedo.ktx2`) são o contrato
//! de CONTEÚDO do pool (docs/ASSETS.md) — remapear ficheiro-a-ficheiro seria
//! uma tabela enorme sem ganho de desacoplamento.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use bevy::prelude::Resource;
use serde::Deserialize;

/// Nome do ficheiro, ao lado do `world.xml`.
pub const CONFIG_FILE: &str = "config.yaml";

#[derive(Debug, Clone, Resource, Deserialize)]
pub struct GameConfig {
    /// Título da janela (opcional; default = nome do ficheiro do mundo).
    #[serde(default)]
    pub title: Option<String>,
    pub assets: AssetsConfig,
    pub game: GameDirs,
    pub save: SaveConfig,
}

/// Diretórios resolvidos contra as asset roots (AssetServer multi-root).
#[derive(Debug, Clone, Deserialize)]
pub struct AssetsConfig {
    /// Roots extra por ordem, DEPOIS da pasta do jogo (vazio = standalone).
    /// Relativas à pasta do jogo; absolutas ficam tal-qual.
    #[serde(default)]
    pub roots: Vec<PathBuf>,
    /// `<MusicLayer layer="x">` → `{bgm_dir}/x.ogg`.
    pub bgm_dir: PathBuf,
    /// Clips da engine (registry SFX, loops de ambiente, chuva) →
    /// `{sfx_dir}/{clip}`.
    pub sfx_dir: PathBuf,
    /// Layers de terreno → `{dir}/{alias}/{albedo,normal}.ktx2`.
    pub terrain_textures_dir: PathBuf,
    // NOTA: NÃO há `shaders_dir` — o `Material::fragment_shader()` do Bevy é
    // um fn ESTÁTICO que resolve `shaders/{sky,water,terrain_chunk}.wgsl`
    // contra as roots; um campo que remapeasse só a escrita partia o render.
    // O diretório `shaders/` da 1.ª root é contrato de conteúdo
    // (docs/ASSETS.md), como os nomes dos clips de áudio.
}

/// Diretórios resolvidos contra a pasta do jogo (filesystem direto).
#[derive(Debug, Clone, Deserialize)]
pub struct GameDirs {
    /// `script="x.lua"` + hot-reload.
    pub scripts_dir: PathBuf,
    // NOTA: NÃO há `ui_dir` — o `src="@ui/hud.css"` do UiStyle é relativo à
    // pasta do jogo e o prefixo `ui/` é parte do caminho autor (contrato do
    // XML); juntar um dir configurável duplicava o prefixo e despia o HUD de
    // estilo (caso real 2026-09-08).
}

#[derive(Debug, Clone, Deserialize)]
pub struct SaveConfig {
    /// Absoluto (com `~` expandido no load); `{dir}/{mundo}.save.json`.
    pub dir: PathBuf,
}

impl GameConfig {
    /// Asset roots em ordem de precedência: a pasta do jogo SEMPRE primeiro
    /// (overrides por-mundo + shaders especializados), depois as extras do
    /// config. `VIBER_ASSET_POOL` (debug/CI) substitui as extras: caminho
    /// pinado, ou `"0"` = nenhuma.
    pub fn asset_roots(&self, world_dir: &Path) -> Vec<PathBuf> {
        let mut roots = vec![world_dir.to_path_buf()];
        let extras: Vec<PathBuf> = match std::env::var("VIBER_ASSET_POOL") {
            Ok(pinned) if pinned == "0" => Vec::new(),
            Ok(pinned) => vec![PathBuf::from(pinned)],
            _ => self
                .assets
                .roots
                .iter()
                .map(|r| {
                    if r.is_absolute() {
                        r.clone()
                    } else {
                        world_dir.join(r)
                    }
                })
                .collect(),
        };
        for extra in extras {
            if !roots.contains(&extra) {
                roots.push(extra);
            }
        }
        roots
    }

    /// Path de asset (relativo a uma root) como string para o AssetServer.
    fn asset_string(dir: &Path, rel: &str) -> String {
        let dir = dir.to_string_lossy();
        format!("{}/{}", dir.trim_end_matches('/'), rel)
    }

    /// `<MusicLayer layer>` → path AssetServer (contra as roots).
    pub fn bgm_path(&self, layer: &str) -> String {
        Self::asset_string(&self.assets.bgm_dir, &format!("{layer}.ogg"))
    }

    /// Clip SFX (`combat/swing.ogg`, relativo ao `sfx_dir`) → path
    /// AssetServer.
    pub fn sfx_path(&self, clip: &str) -> String {
        Self::asset_string(&self.assets.sfx_dir, clip)
    }

    /// Layer de terreno (`grass`) → path de albedo contra as roots; None se
    /// o valor não é um alias mas um caminho cru de textura.
    pub fn terrain_albedo(&self, layer: &str) -> Option<String> {
        crate::terrain::splat::pool_albedo(layer).map(|rel| {
            Self::asset_string(&self.assets.terrain_textures_dir, &rel)
        })
    }

    /// O mesmo para a normal map.
    pub fn terrain_normal(&self, layer: &str) -> Option<String> {
        crate::terrain::splat::pool_normal(layer).map(|rel| {
            Self::asset_string(&self.assets.terrain_textures_dir, &rel)
        })
    }

    /// O mesmo para o height map (escalar, height-blend do chunk).
    pub fn terrain_height(&self, layer: &str) -> Option<String> {
        crate::terrain::splat::pool_height(layer).map(|rel| {
            Self::asset_string(&self.assets.terrain_textures_dir, &rel)
        })
    }

    /// O mesmo para o AO map (escalar, oclusão por texel do chunk).
    pub fn terrain_ao(&self, layer: &str) -> Option<String> {
        crate::terrain::splat::pool_ao(layer).map(|rel| {
            Self::asset_string(&self.assets.terrain_textures_dir, &rel)
        })
    }

    /// Pasta de scripts (filesystem, contra a pasta do jogo).
    pub fn scripts_dir_on(&self, world_dir: &Path) -> PathBuf {
        world_dir.join(&self.game.scripts_dir)
    }

    /// Onde gravar os saves (absoluto; `~` já expandido no load).
    pub fn save_dir(&self) -> &Path {
        &self.save.dir
    }
}

/// Carrega o `config.yaml` da pasta do jogo. **Obrigatório**: sem ele o
/// `run`/`analyze` param aqui com o remédio na mensagem.
pub fn load(world_dir: &Path) -> Result<GameConfig> {
    let path = world_dir.join(CONFIG_FILE);
    let text = std::fs::read_to_string(&path).map_err(|_| {
        anyhow::anyhow!(
            "{}: todo jogo tem de ter um {} ao lado do world.xml — é ele que \
             declara os paths (docs/ASSETS.md); o `viber create` gera um exemplo",
            path.display(),
            CONFIG_FILE
        )
    })?;
    let value: serde_yaml::Value =
        serde_yaml::from_str(&text).with_context(|| format!("{}: YAML inválido", path.display()))?;
    warn_unknown_keys(&path, &value);
    let mut config: GameConfig = serde_yaml::from_value(value).map_err(|error| {
        anyhow::anyhow!(
            "{}: campo em falta ou com tipo errado ({error}) — o `viber create` \
             gera um exemplo completo (schema em docs/ASSETS.md)",
            path.display()
        )
    })?;
    config.save.dir = expand_home(&config.save.dir);
    for root in &config.assets.roots {
        let resolved = if root.is_absolute() {
            root.clone()
        } else {
            world_dir.join(root)
        };
        if !resolved.is_dir() {
            eprintln!(
                "warning: config.yaml: assets.roots: {} não existe — os assets \
                 dela não resolvem",
                resolved.display()
            );
        }
    }
    Ok(config)
}

/// Aviso (não erro) em chaves desconhecidas — o config cresce, e um jogo com
/// um campo futuro não deve rebentar numa engine antiga (igual aos attrs XML).
fn warn_unknown_keys(path: &Path, value: &serde_yaml::Value) {
    const TOP: &[&str] = &["title", "assets", "game", "save"];
    const ASSETS: &[&str] = &[
        "roots",
        "bgm_dir",
        "sfx_dir",
        "terrain_textures_dir",
        
    ];
    const GAME: &[&str] = &["scripts_dir"];
    const SAVE: &[&str] = &["dir"];
    let Some(map) = value.as_mapping() else {
        return;
    };
    for (key, group) in map {
        let Some(key) = key.as_str() else {
            continue;
        };
        if !TOP.contains(&key) {
            eprintln!("warning: {}: chave desconhecida `{key}` — ignorada", path.display());
            continue;
        }
        let allowed: &[&str] = match key {
            "assets" => ASSETS,
            "game" => GAME,
            "save" => SAVE,
            _ => continue,
        };
        if let Some(inner) = group.as_mapping() {
            for inner_key in inner.keys() {
                if let Some(inner_key) = inner_key.as_str() {
                    if !allowed.contains(&inner_key) {
                        eprintln!(
                            "warning: {}: chave desconhecida `{key}.{inner_key}` — ignorada",
                            path.display()
                        );
                    }
                }
            }
        }
    }
}

/// Expande `~`/`~/…` no início do path (só save.dir usa).
fn expand_home(path: &Path) -> PathBuf {
    let Some(text) = path.to_str() else {
        return path.to_path_buf();
    };
    let Some(home) = dirs::home_dir() else {
        return path.to_path_buf();
    };
    if text == "~" {
        return home;
    }
    if let Some(rest) = text.strip_prefix("~/") {
        return home.join(rest);
    }
    path.to_path_buf()
}

/// Config canónico para os testes da lib (audit/ambient/splat) — os dirs
/// materializados como o `viber create` os gera, sem roots extra.
#[cfg(test)]
pub(crate) fn fixture() -> GameConfig {
    GameConfig {
        title: None,
        assets: AssetsConfig {
            roots: Vec::new(),
            bgm_dir: PathBuf::from("assets/audio/bgm"),
            sfx_dir: PathBuf::from("assets/audio/sfx"),
            terrain_textures_dir: PathBuf::from("assets/textures"),
        },
        game: GameDirs {
            scripts_dir: PathBuf::from("scripts"),
        },
        save: SaveConfig {
            dir: PathBuf::from("/tmp"),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_config(dir: &Path, body: &str) {
        std::fs::write(dir.join(CONFIG_FILE), body).unwrap();
    }

    /// O config canónico materializado (o que o `viber create` gera, com uma
    /// root extra a apontar ao pool de um tempdir qualquer).
    const FULL: &str = "title: Jogo Teste\n\
        assets:\n\
        \x20 roots:\n\
        \x20   - pool/public\n\
        \x20 bgm_dir: assets/audio/bgm\n\
        \x20 sfx_dir: assets/audio/sfx\n\
        \x20 terrain_textures_dir: assets/textures\n\
        game:\n\
        \x20 scripts_dir: scripts\n\
        save:\n\
        \x20 dir: /tmp/viber-test-saves\n";

    #[test]
    fn full_config_parses_and_resolves() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("pool/public/assets/meshes")).unwrap();
        write_config(dir.path(), FULL);
        let config = load(dir.path()).unwrap();
        assert_eq!(config.title.as_deref(), Some("Jogo Teste"));
        assert_eq!(config.bgm_path("explore"), "assets/audio/bgm/explore.ogg");
        assert_eq!(config.sfx_path("combat/swing.ogg"), "assets/audio/sfx/combat/swing.ogg");
        assert_eq!(
            config.terrain_albedo("grass").as_deref(),
            Some("assets/textures/grass/albedo.ktx2")
        );

        let world = dir.path().join("world.xml");
        let world_dir = world.parent().unwrap();
        assert_eq!(
            config.asset_roots(world_dir),
            vec![world_dir.to_path_buf(), dir.path().join("pool/public")]
        );
        assert_eq!(config.scripts_dir_on(world_dir), world_dir.join("scripts"));
        assert_eq!(config.save_dir(), Path::new("/tmp/viber-test-saves"));
    }

    #[test]
    fn missing_file_is_an_error_with_the_fix() {
        let dir = tempfile::tempdir().unwrap();
        let err = load(dir.path()).unwrap_err().to_string();
        assert!(err.contains(CONFIG_FILE), "{err}");
        assert!(err.contains("viber create"), "{err}");
    }

    #[test]
    fn missing_field_is_an_error_naming_it() {
        let dir = tempfile::tempdir().unwrap();
        // Sem o bloco `game` — o erro tem de apontar o campo.
        write_config(
            dir.path(),
            "assets:\n  sfx_dir: sfx\n  bgm_dir: bgm\n  terrain_textures_dir: t\nsave:\n  dir: /tmp/x\n",
        );
        let err = load(dir.path()).unwrap_err().to_string();
        assert!(err.contains("game"), "{err}");
    }

    #[test]
    fn unknown_keys_warn_but_parse() {
        let dir = tempfile::tempdir().unwrap();
        write_config(
            dir.path(),
            "futuro: sim\n\
             assets:\n  roots: []\n  bgm_dir: bgm\n  sfx_dir: sfx\n  terrain_textures_dir: t\n  novidade: 1\n\
             game:\n  scripts_dir: scripts\n\
             save:\n  dir: /tmp/x\n",
        );
        // Chaves desconhecidas avisam (stderr) e NÃO partem o parse — um
        // config com campo futuro corre numa engine antiga.
        assert!(load(dir.path()).is_ok());
    }

    #[test]
    fn standalone_game_has_no_extra_roots() {
        let dir = tempfile::tempdir().unwrap();
        write_config(
            dir.path(),
            "assets:\n  roots: []\n  bgm_dir: bgm\n  sfx_dir: sfx\n  terrain_textures_dir: t\n\
             game:\n  scripts_dir: scripts\nsave:\n  dir: /tmp/x\n",
        );
        let config = load(dir.path()).unwrap();
        let world_dir = dir.path();
        assert_eq!(config.asset_roots(world_dir), vec![world_dir.to_path_buf()]);
    }
}
