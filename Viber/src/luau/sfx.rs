//! Registry nome→clip para `viber.sound` (case-insensitive, tabela única —
//! um clip novo no enum SEM linha aqui falha o teste
//! `test_lua_registry_covers_all_clips`) e o fuzzy match de gestos
//! (`viber.gesture`) contra os clips do rig.

/// Registry nome-clip Lua (`viber.sound`) — case-insensitive, tabela única.
/// Os gatilhos nativos usam as variantes directamente; os scripts passam por
/// aqui. Aliases aceitáveis além do nome canónico (ex. `level_up`) ficam em
/// linhas próprias apontando à mesma variante.
pub const SFX_NAME_REGISTRY: &[(&str, crate::ambient::SfxClip)] = &[
    ("hit", crate::ambient::SfxClip::Hit),
    ("whoosh", crate::ambient::SfxClip::Whoosh),
    ("harvest", crate::ambient::SfxClip::Harvest),
    ("ui", crate::ambient::SfxClip::Ui),
    ("chop_hit", crate::ambient::SfxClip::ChopHit),
    ("chop_break", crate::ambient::SfxClip::ChopBreak),
    ("mine_hit", crate::ambient::SfxClip::MineHit),
    ("mine_break", crate::ambient::SfxClip::MineBreak),
    ("levelup", crate::ambient::SfxClip::LevelUp),
    ("level_up", crate::ambient::SfxClip::LevelUp),
    ("quest_complete", crate::ambient::SfxClip::QuestDone),
    ("quest_done", crate::ambient::SfxClip::QuestDone),
    ("travel", crate::ambient::SfxClip::Travel),
    ("loot", crate::ambient::SfxClip::Loot),
    ("chest_open", crate::ambient::SfxClip::Loot),
    ("footstep", crate::ambient::SfxClip::Footstep),
    ("footstep_water", crate::ambient::SfxClip::FootstepWater),
    ("hurt", crate::ambient::SfxClip::Hurt),
    ("heal", crate::ambient::SfxClip::Heal),
    ("game_over", crate::ambient::SfxClip::GameOver),
    ("quest_accept", crate::ambient::SfxClip::QuestAccept),
    ("notification", crate::ambient::SfxClip::Notification),
    ("coin", crate::ambient::SfxClip::Coin),
    ("buy", crate::ambient::SfxClip::Buy),
    ("error", crate::ambient::SfxClip::Error),
    ("save", crate::ambient::SfxClip::Save),
    ("load", crate::ambient::SfxClip::Load),
    ("shop_open", crate::ambient::SfxClip::ShopOpen),
    ("enemy_hurt", crate::ambient::SfxClip::EnemyHurt),
    ("enemy_death", crate::ambient::SfxClip::EnemyDeath),
    ("wolf_growl", crate::ambient::SfxClip::WolfGrowl),
    ("growl", crate::ambient::SfxClip::WolfGrowl),
    ("slime_squish", crate::ambient::SfxClip::SlimeSquish),
    ("boss_roar", crate::ambient::SfxClip::BossRoar),
    ("roar", crate::ambient::SfxClip::BossRoar),
    ("shield_block", crate::ambient::SfxClip::ShieldBlock),
    ("block", crate::ambient::SfxClip::ShieldBlock),
    ("door_open", crate::ambient::SfxClip::DoorOpen),
    ("door_close", crate::ambient::SfxClip::DoorClose),
    ("bomb_drop", crate::ambient::SfxClip::BombDrop),
    ("jump", crate::ambient::SfxClip::Jump),
    ("dash", crate::ambient::SfxClip::Dash),
];

/// Nome de clip SFX Lua (`"hit"`, `"UI"`) → [`crate::ambient::SfxClip`]
/// (case-insensitive via [`SFX_NAME_REGISTRY`]; desconhecido = `None`).
pub fn sfx_clip_from_str(name: &str) -> Option<crate::ambient::SfxClip> {
    let lower = name.to_ascii_lowercase();
    SFX_NAME_REGISTRY
        .iter()
        .find(|(alias, _)| *alias == lower)
        .map(|(_, clip)| *clip)
}

/// Índice do clip de gesto para um pedido `viber.gesture(name)` (pure fn).
///
/// Tenta cada alternativa (separadas por `,` ou `|`) por ordem; dentro de
/// cada uma, o match EXACTO normalizado ganha à substring — ambos os lados
/// passam por [`crate::animation::normalize_clip_name`] (caixa, `_`/`-` e
/// prefixos de ferramenta caem fora), por isso `"foldarms"` encontra
/// `Animator3D_FoldArms`. Sem correspondência = `None` (o chamador avisa 1×
/// e ignora, sem crash).
pub fn match_gesture_clip(
    animator: &crate::animation::CharacterAnimator,
    request: &str,
) -> Option<usize> {
    let names: Vec<String> = animator
        .clip_names
        .iter()
        .map(|n| crate::animation::normalize_clip_name(n))
        .collect();
    for want in request.split([',', '|']) {
        let want = crate::animation::normalize_clip_name(want);
        if want.is_empty() {
            continue;
        }
        if let Some(i) = names.iter().position(|n| *n == want) {
            return Some(i);
        }
        if let Some(i) = names.iter().position(|n| n.contains(&want)) {
            return Some(i);
        }
    }
    None
}
