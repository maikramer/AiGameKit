//! Comandos que scripts enfileiram e a engine aplica pós-frame
//! (`runtime::luau_update` drena [`ScriptCommand`]s de todos os `on_update`).
//! A engine provê os BLOCOS PRIMITIVOS (percepção, movimento com snap no
//! terreno, combate, UI) — a composição de comportamento vive no Luau.

use bevy::prelude::*;
use mlua::Function;

/// Comandos que scripts enfileiram e a engine aplica pós-frame. A engine
/// provê os BLOCOS PRIMITIVOS (percepção, movimento com snap no terreno,
/// combate, UI) — a composição de comportamento vive no Luau.
#[derive(Debug, Clone)]
pub enum ScriptCommand {
    /// Velocidade planar desejada (m/s) para este frame. NÃO é um passo já
    /// multiplicado por `dt`: o consumidor ([`crate::ai::apply_ai_locomotion`])
    /// faz a rampa de aceleração, o passo, o snap de Y e a viragem.
    MoveBy(Entity, Vec2),
    /// Marcha para um PONTO (`viber.move_towards`).
    ///
    /// Separado do [`Self::MoveBy`] porque o destino é informação que o
    /// pathfinding precisa e a velocidade não carrega: projectar a velocidade
    /// para a frente só nomeia pontos sobre a linha recta — que é exactamente
    /// a linha onde está a parede.
    MoveTowards {
        entity: Entity,
        goal: Vec2,
        speed: f32,
    },
    /// Vira a entidade para olhar um ponto (yaw only).
    FaceTowards(Entity, Vec3),
    /// Fixa os nominais de locomoção do rig (m/s) e, opcionalmente, a taxa de
    /// viragem (rad/s). Desliga a auto-calibração.
    SetLocomotion {
        entity: Entity,
        walk: f32,
        run: f32,
        turn_rate: Option<f32>,
    },
    TeleportPlayer(Vec3),
    AddXp(u32),
    DamagePlayer {
        amount: f32,
        from: Option<Vec3>,
    },
    HealPlayer(f32),
    /// Dispara um projétil de um `<ProjectileTemplate>` (`viber.fire_projectile`).
    FireProjectile {
        template: String,
        origin: Vec3,
        target: Vec3,
    },
    /// Aplica um status effect ao herói (hoje: `"venom"`) — tratado pelo
    /// feedback (tick 1/s, path único de dano).
    ApplyStatus {
        kind: String,
        secs: f32,
    },
    /// Quests: aceitar / entregar / reportar progresso (hooks para scripts,
    /// aplicados pelo [`crate::quests::QuestLog`]).
    QuestAccept(String),
    QuestTurnIn(String),
    /// kill/collect: alvo + quantidade.
    QuestReport {
        target: String,
        amount: u32,
    },
    /// Visita a um marco nomeado.
    QuestVisit(String),
    /// Destrutível com queda (`break-style: fall`) — tomba na direção
    /// herói→entidade e despawna no fim.
    Topple {
        entity: Entity,
    },
    /// Consome do vault (`viber.vault_take`): recurso OU item, `false`-sem-
    /// stock vira no-op com warn 1× (o script guarda-se com `vault_get`).
    VaultTake { kind: String, amount: u32 },
    /// Reclama uma ação da UI (`viber.own_action("buy")`) — o handler nativo
    /// correspondente cala e a ação chega a Lua via `viber.events()`.
    OwnUiAction(String),
    /// Deposita recurso no vault (`gold`/`wood`/`stone`) — economia loop 4.
    /// `from_collect` marca `viber.report_collect`, que aceita também itens de
    /// objetivo (fallback `item_add`); a chamada explícita `viber.vault_add`
    /// não — recurso desconhecido é erro diagnosticável, não item silencioso.
    VaultAdd {
        kind: String,
        amount: u32,
        from_collect: bool,
    },
    /// Adiciona item ao inventário (`potion`, `antidote`, `bomb`…).
    ItemAdd {
        id: String,
        amount: u32,
    },
    /// Mensagem de UI (balão/toast) — consumida via [`ScriptToast`].
    Toast(String),
    /// Registra alvo de interação ("[J] Minerar") na entidade.
    SetInteraction {
        entity: Entity,
        label: String,
        key: String,
        range: f32,
    },
    Despawn(Entity),
    /// Clip de ação no rig da entidade (`viber.gesture` / `viber.play_clip`):
    /// fuzzy match do nome, blend ~250 ms — o driver de locomoção recupera o
    /// rig no fim do clip. `speed` escala a reprodução (1.0 = nominal; a
    /// colheita nativa usa 1.4).
    Gesture {
        entity: Entity,
        name: String,
        speed: f32,
    },
    /// SFX curto (`viber.sound`) tocado na posição da entidade — consumido
    /// pelo `sfx_player_system` do [`crate::ambient`].
    PlaySfx {
        clip: crate::ambient::SfxClip,
        position: Option<Vec3>,
    },
    /// Reclama um sistema nativo (`viber.own_system("dialogue")`) — o handler
    /// nativo correspondente cala e a lógica passa a ser do script.
    OwnSystem(String),
    /// Dano em ÁREA (`viber.radial_damage`): falloff linear (skills.rs) +
    /// knockback radial opcional; mortes seguem a paridade do
    /// `kill_creature` (corpo, XP, quests, evento `Kill`).
    RadialDamage {
        x: f32,
        z: f32,
        radius: f32,
        damage: f32,
        knockback: f32,
    },
    /// Burst de partículas (`viber.burst`) — preset validado contra
    /// [`crate::particles::PRESET_NAMES`].
    Burst {
        preset: String,
        pos: Vec3,
        count: usize,
    },
    /// Anel de choque (`viber.ring`).
    Ring {
        x: f32,
        z: f32,
        radius: f32,
        color: Option<[f32; 3]>,
    },
    /// Câmara: shake (`viber.shake`), solavanco direcional
    /// (`viber.kick`) e kick de FOV (`viber.fov_kick`).
    Shake(f32),
    CameraKick(Vec3),
    FovKick(f32),
    /// Pulso de pós-processo (`viber.punch`) e hit-stop (`viber.hit_stop`).
    Punch {
        stops: f32,
        bloom: f32,
    },
    HitStop(f32),
    /// Número de dano flutuante (`viber.damage_number`).
    DamageNumber {
        text: String,
        pos: Vec3,
        color: Option<[f32; 3]>,
    },
    /// Limpa um status effect do herói (`viber.status_clear`).
    StatusClear(String),
    /// Balão de diálogo nativo (`viber.say(texto, segundos?)`).
    Say {
        text: String,
        /// 0 = duração por omissão ([`crate::hud::BALLOON_DURATION`]).
        secs: f32,
    },
    /// Pede uma gravação do save (`viber.save()`) — o `options_system` do
    /// save drena o pedido no mesmo frame (mundo `gameplay: none` incluído).
    SaveNow,
    /// Pede um carregamento do save (`viber.load()`).
    LoadNow,
    /// Vitais GENÉRICAS de entidade (`viber.entity_set_max_hp`): cria o
    /// `Health` se faltar (current = max); numa entidade existente
    /// redimensiona (current clampe ao novo max).
    EntitySetMaxHp {
        entity: Entity,
        max: f32,
    },
    /// Dano direto em entidade (`viber.entity_damage`) — sem i-frames (esses
    /// são do path do player). Sem `Health` = no-op (warn 1×). HP ≤ 0 emite
    /// [`ScriptGameEvent::Kill`] e NÃO corre o caminho nativo de morte
    /// (cadáver/XP/quests são do melee) — o script decide o que segue.
    EntityDamage {
        entity: Entity,
        amount: f32,
    },
    /// Cura direta em entidade (`viber.entity_heal`), clampe ao max.
    EntityHeal {
        entity: Entity,
        amount: f32,
    },
    /// Edição VIVA do terreno (`viber.terrain.lower/raise/flatten/crater`):
    /// vai para a [`crate::terrain::delta::TerrainEditQueue`], aplicada pelo
    /// `apply_terrain_edits` no máximo `EDITS_PER_FRAME` por frame; o
    /// rebuild das colunas afetadas (mesh + collider) segue pelo caminho
    /// staged do plugin de LOD.
    TerrainEdit {
        edit: crate::terrain::delta::TerrainEdit,
    },
    /// Instancia um `<Prototype>` do mundo (`viber.spawn_prototype`) — o
    /// spawn REAL é exclusivo (`recipes::spawn::apply_script_spawns`), pós-
    /// frame, com callback `(bits)`.
    SpawnPrototype {
        name: String,
        pos: Vec3,
        /// Sem cota explícita: assenta o Y na superfície renderizada.
        seat: bool,
        on_spawned: Option<Function>,
    },
}
