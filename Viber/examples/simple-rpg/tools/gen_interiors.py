#!/usr/bin/env python3
"""Gera ``world/interiors.xml`` — a bolsa de interiores do simple-rpg.

PORQUÊ UM GERADOR: nove salas partilham a mesma anatomia (soalho, paredes com
vão e janelas, postes, vigas, rodapé, mobília, luzes, NPCs, portal de saída).
Escrito à mão, cada sala repetia ~40 linhas de caixas com offsets à mão e
qualquer mudança de parede tinha de ser feita nove vezes. Aqui a anatomia vive
num sítio e cada sala é uma linha de dados.

A BOLSA: a cena vive FORA da pegada do heightmap (``|x| > world_size/2``), onde
a engine já não gera colunas de terreno nem colliders — ver
``TerrainRuntime::in_field``. O retângulo é declarado por ``<InteriorScene>``,
que isenta a fronteira do mundo, tira o bioma (névoa/tinta/exposição) e a chuva.
A distância (>2 km) desliga o resto por si: render, culling, IA e spawners.

O REGISTO DE PORTAIS: as saídas são colocadas a partir da MESMA tabela que o
``scripts/building-portal.lua`` usa (``ROOMS`` — espelhada lá, com um teste de
consistência no arranque). Mudar a bolsa = mudar ``POCKET`` aqui e ali.

Correr:  python3 tools/gen_interiors.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

# ── A BOLSA ────────────────────────────────────────────────────────────────
# Centro da bolsa. TEM de ficar fora da pegada do heightmap (world-size 4000 →
# |x|,|z| > 2000) e longe de qualquer conteúdo do mapa.
POCKET = (2600.0, 2600.0)
# Passo da grelha de salas (as salas maiores têm 28 m → 60/55 deixa corredor).
STEP_X, STEP_Z = 60.0, 55.0
#: Folga do retângulo da bolsa em volta das salas (m).
MARGIN = 22.0

# ── MATERIAIS DO POOL ──────────────────────────────────────────────────────
# Albedo + normal do pool, em UV world-space (`texture-tile-size` = metros por
# repetição). É o que faz um soalho ler como soalho em vez de "caixa cinzenta".
MAT = {
    "stone_floor": ("/assets/textures/cobblestone_road/albedo.ktx2", "/assets/textures/cobblestone_road/normal.ktx2", 3.2),
    "wood_floor": ("/assets/textures/wood_planks/albedo.ktx2", "/assets/textures/wood_planks/normal.ktx2", 3.0),
    "dirt_floor": ("/assets/textures/dirt_road/albedo.ktx2", None, 3.0),
    "plaster": ("/assets/textures/wall_plaster/albedo.ktx2", "/assets/textures/wall_plaster/normal.ktx2", 3.4),
    "stone_wall": ("/assets/textures/mountain_stone/albedo.ktx2", "/assets/textures/mountain_stone/normal.ktx2", 3.6),
    "beam": ("/assets/textures/wood_planks/albedo.ktx2", "/assets/textures/wood_planks/normal.ktx2", 1.6),
    "roof": ("/assets/textures/roof_tiles/albedo.ktx2", "/assets/textures/roof_tiles/normal.ktx2", 2.4),
    "cloth": ("/assets/textures/wood_planks/albedo.ktx2", None, 2.0),
}

WALL_H = 2.4          # altura da parede (m)
WALL_T = 0.14         # meia-espessura
DOOR_W = 2.6          # vão da porta
DOOR_H = 2.1          # altura do vão (o lintel fecha por cima)
WIN_W = 1.6
WIN_SILL = 0.9
WIN_TOP = 1.9


def mat(prim: str, key: str, color: str = None) -> str:
    """Atributos de material de uma primitiva: textura do pool + cor opcional."""
    albedo, normal, tile = MAT[key]
    attrs = []
    if color:
        attrs.append(f'base-color="{color}"')
    attrs.append(f'texture="{albedo}"')
    attrs.append(f'texture-tile-size="{tile}"')
    if normal:
        attrs.append(f'normal-map="{normal}"')
    return f"<{prim} " + " ".join(attrs)


@dataclass
class Box:
    """Uma parte do shell, em coordenadas LOCAIS da sala (centro no 0,0)."""
    x: float
    y: float
    z: float
    hx: float
    hy: float
    hz: float
    material: str
    yaw: float = 0.0


@dataclass
class Prop:
    """Mobília: asset do pool + offset local + colisor."""
    asset: str
    x: float
    z: float
    yaw: float = 0.0
    scale: float = 1.0
    collider: str = "trimesh"   # trimesh | precompute | box
    script: str | None = None
    fire: bool = False          # chama + luz quente no topo
    light: tuple | None = None  # (cor, intensidade, altura)


@dataclass
class Npc:
    """Um NPC dentro da sala, por PAPEL.

    O papel resolve para um modelo: o preferido (`ROLE_MODEL`) é o que
    `manifests/characters-interiors.yaml` gera (innkeeper, priest, scholar,
    cook, bard); se esse GLB ainda não estiver no pool, cai no `ROLE_FALLBACK`
    — o elenco de rua já existente. Assim a sala nunca fica com um NPC
    invisível, e no dia em que os modelos novos aterrarem basta voltar a
    correr este gerador para os lugares certos mudarem sozinhos.
    """
    role: str
    x: float
    z: float
    yaw: float = 0.0
    script: str = "interior-folk.lua"

    @property
    def model(self) -> str:
        return ROLE_MODEL.get(self.role, self.role) if available(ROLE_MODEL.get(self.role, self.role)) else ROLE_FALLBACK[self.role]


#: Papel → modelo gerado por `manifests/characters-interiors.yaml`.
ROLE_MODEL = {
    "innkeeper": "npc_innkeeper",
    "priest": "npc_priest",
    "scholar": "npc_scholar",
    "cook": "npc_cook",
    "bard": "npc_bard",
    "merchant": "npc_merchant",
    "blacksmith": "npc_blacksmith",
    "guard": "npc_guard",
    "elder": "npc_elder",
    "scout": "npc_scout",
    "healer": "npc_healer",
}
#: Papel → modelo do elenco de rua, quando o gerado ainda não existe.
ROLE_FALLBACK = {
    "innkeeper": "npc_merchant",
    "cook": "npc_healer",
    "scholar": "npc_elder",
    "bard": "npc_scout",
    "priest": "npc_elder",
    "farmhand": "npc_scout",
    "merchant": "npc_merchant",
    "blacksmith": "npc_blacksmith",
    "guard": "npc_guard",
    "elder": "npc_elder",
    "scout": "npc_scout",
    "healer": "npc_healer",
}

_CHAR_DIR = Path(__file__).resolve().parents[2] / "shared-assets" / "public" / "assets" / "meshes" / "characters"


#: Raiz do pool de meshes (o pool não está no git: isto é uma pergunta em
#: runtime, não uma constante).
_MESH_DIR = Path(__file__).resolve().parents[2] / "shared-assets" / "public" / "assets" / "meshes"


def glb_exists(asset: str) -> bool:
    """`asset` é um caminho tipo `interiors/tavern_bar_lod0` (sem .glb)."""
    return (_MESH_DIR / f"{asset}.glb").is_file()


def available(model: str) -> bool:
    """O GLB do modelo existe no pool? (o pool não está no git, logo isto é
    uma pergunta em runtime, não uma constante)."""
    return (_CHAR_DIR / f"{model}_lod0.glb").is_file()


@dataclass
class Room:
    id: str
    name: str
    w: float
    d: float
    ix: int
    iz: int
    floor: str = "stone_floor"
    wall: str = "plaster"
    beams: bool = True
    windows: int = 2
    # Porta de rua (world/cities/discordia/portals.xml): o par
    # porta↔saída vive NUMA linha, não em duas listas alinhadas por ordem.
    door: tuple[float, float] = (0.0, 0.0)
    props: list[Prop] = field(default_factory=list)
    npcs: list[Npc] = field(default_factory=list)
    lights: list[tuple] = field(default_factory=list)  # (cor, intensidade, x, z, y)
    open_air: bool = False      # banca/mercado: sem paredes, só postes e toldo

    @property
    def cx(self) -> float:
        return POCKET[0] + self.ix * STEP_X

    @property
    def cz(self) -> float:
        return POCKET[1] + self.iz * STEP_Z

    @property
    def exit_z(self) -> float:
        """Z local do portal de saída (no vão −Z, 1 m fora da parede)."""
        return -(self.d * 0.5 + 1.0)


# ── ANATOMIA DO SHELL ──────────────────────────────────────────────────────
def wall_run(z: float, half_len: float, openings: list[tuple[float, float]]) -> list[Box]:
    """Parede em X a z fixo, com vãos (centro, largura) recortados por caixas."""
    boxes: list[Box] = []
    cursor = -half_len
    for centre, width in sorted(openings):
        lo, hi = centre - width * 0.5, centre + width * 0.5
        if lo > cursor:
            boxes.append(Box((cursor + lo) * 0.5, WALL_H * 0.5, z, (lo - cursor) * 0.5, WALL_H * 0.5, WALL_T, "wall"))
        # lintel sobre o vão
        boxes.append(Box(centre, (DOOR_H + WALL_H) * 0.5, z, width * 0.5, (WALL_H - DOOR_H) * 0.5, WALL_T, "wall"))
        cursor = hi
    if cursor < half_len:
        boxes.append(Box((cursor + half_len) * 0.5, WALL_H * 0.5, z, (half_len - cursor) * 0.5, WALL_H * 0.5, WALL_T, "wall"))
    return boxes


def wall_side(x: float, half_len: float, openings: list[tuple[float, float]]) -> list[Box]:
    """Parede em Z a x fixo."""
    boxes: list[Box] = []
    cursor = -half_len
    for centre, width in sorted(openings):
        lo, hi = centre - width * 0.5, centre + width * 0.5
        if lo > cursor:
            boxes.append(Box(x, WALL_H * 0.5, (cursor + lo) * 0.5, WALL_T, WALL_H * 0.5, (lo - cursor) * 0.5, "wall"))
        # peitoril + lintel da janela (vão de janela: sobe do peitoril)
        boxes.append(Box(x, WIN_SILL * 0.5, centre, WALL_T, WIN_SILL * 0.5, width * 0.5, "wall"))
        boxes.append(Box(x, (WIN_TOP + WALL_H) * 0.5, centre, WALL_T, (WALL_H - WIN_TOP) * 0.5, width * 0.5, "wall"))
        cursor = hi
    if cursor < half_len:
        boxes.append(Box(x, WALL_H * 0.5, (cursor + half_len) * 0.5, WALL_T, WALL_H * 0.5, (half_len - cursor) * 0.5, "wall"))
    return boxes


def shell(room: Room) -> list[Box]:
    """Soalho + paredes com vão/janelas + postes + vigas + rodapé."""
    hw, hd = room.w * 0.5, room.d * 0.5
    out: list[Box] = []
    # soalho: uma laje de 0.12 (o collider é o próprio box) — ligeiramente
    # mais fundo que as paredes para ler como soleira.
    out.append(Box(0.0, 0.06, 0.0, hw + 0.6, 0.06, hd + 0.6, room.floor))
    if room.open_air:
        return out
    # janelas distribuídas pelas paredes laterais (nunca na do vão)
    win_e = [(-hd * 0.35, WIN_W), (hd * 0.35, WIN_W)] if room.windows >= 2 else []
    win_w = [(-hd * 0.35, WIN_W)] if room.windows >= 1 else []
    out += wall_run(hd, hw, [])                      # norte: sólida
    out += wall_run(-hd, hw, [(0.0, DOOR_W)])        # sul: vão da porta
    out += wall_side(hw, hd, win_e)
    out += wall_side(-hw, hd, win_w)
    # postes nos cantos + ombreiras da porta
    for sx in (-1, 1):
        for sz in (-1, 1):
            out.append(Box(sx * (hw - 0.18), WALL_H * 0.5, sz * (hd - 0.18), 0.18, WALL_H * 0.5, 0.18, "beam"))
    for sx in (-1, 1):
        out.append(Box(sx * (DOOR_W * 0.5 + 0.16), DOOR_H * 0.5, -hd, 0.16, DOOR_H * 0.5, WALL_T + 0.04, "beam"))
    out.append(Box(0.0, DOOR_H + 0.09, -hd, DOOR_W * 0.5 + 0.3, 0.09, WALL_T + 0.06, "beam"))
    # vigas do tecto (deixam ver para dentro: são um esqueleto, não um tecto)
    if room.beams:
        span = room.w + 0.4
        step = max(2.6, (room.d - 1.0) / max(1.0, round(room.d / 3.2)))
        n = max(1, int((room.d - 1.2) / step))
        for i in range(n + 1):
            z = -hd + 0.6 + i * step
            if z > hd - 0.6:
                break
            out.append(Box(0.0, WALL_H + 0.10, z, span * 0.5, 0.10, 0.12, "beam"))
        # madres ao longo dos dois lados
        for sx in (-1, 1):
            out.append(Box(sx * (hw - 0.15), WALL_H + 0.10, 0.0, 0.12, 0.10, hd - 0.2, "beam"))
    # rodapé
    for sx in (-1, 1):
        out.append(Box(sx * (hw - 0.16), 0.14, 0.0, 0.05, 0.14, hd - 0.6, "beam"))
    out.append(Box(0.0, 0.14, hd - 0.16, hw - 0.6, 0.14, 0.05, "beam"))
    return out


# ── SALAS ──────────────────────────────────────────────────────────────────
# Convenção: a porta está SEMPRE no vão −Z (o shell abre o buraco aí), logo o
# ponto focal de cada sala (altar, fornalha, lareira, balcão) fica em +Z e o
# que sobra encosta às paredes laterais. `check_inside` recusa prop/NPC no
# corredor da porta.
def rooms() -> list[Room]:
    """As nove salas. O foco (altar, fornalha, lareira, balcão) fica SEMPRE em
    +Z e a porta em −Z, para o herói entrar e ver a sala pela frente."""
    R: list[Room] = []
    # ── CAPELA 24×18 ──────────────────────────────────────────────────────
    R.append(Room("chapel", "capela", 24, 18, 1, 0, door=(7.46, 22.46),
        floor="stone_floor", wall="plaster", windows=2,
        props=[
            Prop("interiors/chapel_altar_lod0", 0, 6.4, 180),
            Prop("interiors/chapel_pulpit_lod0", 1.9, 5.0, 180),
            Prop("interiors/church_organ_lod0", -7.4, 5.6, -90),
            Prop("interiors/candelabra_tall_lod0", -6.8, 3.0, 0, light=("0xffd9a0", 10_000, 2.2)),
            Prop("interiors/candelabra_tall_lod0", 6.8, 3.0, 0, light=("0xffd9a0", 9_000, 2.2)),
            Prop("interiors/chapel_pew_lod0", -3.9, -1.4, 0),
            Prop("interiors/chapel_pew_lod0", 3.9, -1.4, 0),
            Prop("interiors/chapel_pew_lod0", -3.9, -3.8, 0),
            Prop("interiors/chapel_pew_lod0", 3.9, -3.8, 0),
            Prop("interiors/confessional_lod0", -9.4, -4.6, 90),
            Prop("interiors/chapel_statue_lod0", 9.6, -5.6, 90),
            Prop("props/stone_pillar_lod0", -10.6, 1.0, 0, 1.1),
            Prop("props/stone_pillar_lod0", 10.6, 1.0, 0, 1.1),
            Prop("village/iron_brazier_lod0", -4.8, 6.2, 0, fire=True, light=("0xffa83a", 3_600, 1.1)),
            Prop("village/iron_brazier_lod0", 4.8, 6.2, 0, fire=True, light=("0xffa83a", 3_600, 1.1)),
        ],
        npcs=[Npc("priest", 1.2, 3.4, 0, "interior-keeper.lua"),
              Npc("elder", -3.0, -6.4, 180, "interior-folk.lua")],
        lights=[("0xffd9a0", 8_000, 0.0, 0.0, 2.3)]))
    # ── FERRARIA 22×16 ────────────────────────────────────────────────────
    R.append(Room("forge", "ferraria", 22, 16, 2, 0, door=(-30.47, -29.06),
        floor="dirt_floor", wall="stone_wall", windows=2,
        props=[
            Prop("interiors/forge_furnace_lod0", 0, 5.8, 180, fire=True, light=("0xff8a40", 12_000, 1.6)),
            Prop("village/anvil_lod0", 0, 2.0, 180, script="anvil.lua"),
            Prop("interiors/sledge_hammer_lod0", 1.0, 3.0, 200),
            Prop("village/forge_bellows_lod0", -3.6, 5.4, 0),
            Prop("village/quench_trough_lod0", 3.6, 4.6, 0),
            Prop("village/horseshoe_pile_lod0", -2.6, 1.4, 0),
            Prop("village/weapon_rack_lod0", 8.6, -1.6, 90),
            Prop("village/weapon_rack_lod0", 8.6, -4.0, 90),
            Prop("village/chopping_block_lod0", -7.0, -3.4, 0),
            Prop("village/log_pile_lod0", -8.4, -5.4, 40),
            Prop("village/wooden_crate_lod0", 7.4, -5.6, 0),
            Prop("village/wooden_barrel_lod0", 8.6, -5.4, 0),
            Prop("village/wooden_barrel_lod0", -9.0, 3.6, 0),
        ],
        npcs=[Npc("blacksmith", 2.2, 3.0, 0, "anvil.lua")],
        lights=[("0xffc090", 6_000, 0.0, 0.0, 2.2)]))
    # ── CASA COMUM 20×16 ──────────────────────────────────────────────────
    R.append(Room("house_a", "casa comum", 20, 16, 0, 0, door=(26.35, 8.44),
        floor="wood_floor",
        props=[
            Prop("interiors/fireplace_hearth_lod0", 0, 6.2, 180, fire=True, light=("0xffb070", 11_000, 1.4)),
            Prop("interiors/dining_table_lod0", 0, 0.4, 0),
            Prop("interiors/wooden_chair_lod0", 0, -1.6, 180),
            Prop("interiors/wooden_chair_lod0", 0, 2.4, 0),
            Prop("interiors/wooden_chair_lod0", -1.8, 0.4, -90),
            Prop("interiors/wooden_chair_lod0", 1.8, 0.4, 90),
            Prop("interiors/rug_woven_lod0", 0, 0.4, 0, collider="box"),
            Prop("interiors/bed_simple_lod0", -7.0, -5.2, 0),
            Prop("interiors/cupboard_lod0", -8.6, 4.4, 0),
            Prop("interiors/bookshelf_lod0", 8.4, 4.6, 180),
            Prop("interiors/cauldron_iron_lod0", 6.8, 6.0, 180, fire=True),
            Prop("interiors/spinning_wheel_lod0", 7.2, -1.0, 90),
            Prop("village/wooden_barrel_lod0", -8.4, -1.4, 0),
            Prop("village/wooden_crate_lod0", 7.6, -5.4, 20),
        ],
        npcs=[Npc("cook", -6.4, 5.2, 200, "interior-keeper.lua"),
              Npc("innkeeper", 2.6, 1.2, 20, "interior-keeper.lua")],
        lights=[("0xffcf9a", 9_000, 0.0, 0.4, 2.3)]))
    # ── CASA B (biblioteca) 20×16 ─────────────────────────────────────────
    R.append(Room("house_b", "casa do escriba", 20, 16, 0, 1, door=(-17.44, 22.47),
        floor="wood_floor",
        props=[
            Prop("interiors/fireplace_hearth_lod0", 0, 6.2, 180, fire=True, light=("0xffb070", 8_000, 1.4)),
            Prop("interiors/bookshelf_lod0", -7.4, 3.4, 0),
            Prop("interiors/bookshelf_lod0", -7.4, 4.6, 0),
            Prop("interiors/bookshelf_lod0", 7.4, 3.4, 180),
            Prop("interiors/bookshelf_lod0", 7.4, 4.6, 180),
            Prop("interiors/dining_table_lod0", -1.0, -0.6, 0),
            Prop("interiors/wooden_chair_lod0", -1.0, -2.6, 180),
            Prop("interiors/wooden_chair_lod0", 3.2, -0.6, 90),
            Prop("interiors/candelabra_tall_lod0", 1.6, -0.6, 0, light=("0xffd9a0", 7_000, 2.2)),
            Prop("interiors/bookshelf_lod0", 7.6, -4.6, 180),
            Prop("interiors/rug_woven_lod0", -1.0, -0.6, 0, collider="box", scale=0.9),
            Prop("village/wooden_crate_lod0", -8.0, -4.8, 0),
        ],
        npcs=[Npc("scholar", -1.0, 0.6, 0, "interior-keeper.lua")],
        lights=[("0xffd0a0", 6_000, 0.0, -0.6, 2.3)]))
    # ── CASA C (cozinha) 20×16 ────────────────────────────────────────────
    R.append(Room("house_c", "cozinha", 20, 16, 1, 1, door=(-20.47, -18.44),
        floor="wood_floor",
        props=[
            Prop("interiors/fireplace_hearth_lod0", 0, 6.2, 180, fire=True, light=("0xffb070", 11_000, 1.4)),
            Prop("interiors/cauldron_iron_lod0", -2.4, 5.0, 180, fire=True),
            Prop("interiors/cauldron_iron_lod0", 2.4, 5.0, 180, fire=True),
            Prop("interiors/tavern_bar_lod0", 0, 1.6, 0),
            Prop("interiors/dining_table_lod0", 0, -4.8, 0),
            Prop("interiors/stool_wood_lod0", -1.6, -4.8, 90),
            Prop("interiors/stool_wood_lod0", 1.6, -4.8, -90),
            Prop("interiors/cupboard_lod0", -8.6, 4.6, 0),
            Prop("interiors/cupboard_lod0", 8.6, 4.6, 180),
            Prop("village/wooden_barrel_lod0", 7.6, 1.0, 0),
            Prop("village/wooden_barrel_lod0", 7.6, -0.6, 0),
            Prop("village/wooden_crate_lod0", -7.8, -1.2, 0),
            Prop("village/wooden_crate_lod0", -7.8, -3.0, 12),
        ],
        npcs=[Npc("cook", -1.4, 3.4, 180, "interior-keeper.lua"),
              Npc("bard", 3.4, -3.0, 40, "interior-folk.lua")],
        lights=[("0xffcf9a", 8_000, 0.0, 0.0, 2.3)]))
    # ── CABANA DO PASTOR 18×14 ────────────────────────────────────────────
    R.append(Room("shepherd", "cabana do pastor", 18, 14, 2, 1, door=(-22.33, 12.95),
        floor="wood_floor",
        props=[
            Prop("interiors/fireplace_hearth_lod0", 0, 5.2, 180, fire=True, light=("0xffb070", 9_000, 1.4)),
            Prop("interiors/bed_simple_lod0", -6.0, -3.6, 0),
            Prop("interiors/dining_table_lod0", 1.6, 0.0, 0),
            Prop("interiors/stool_wood_lod0", 3.4, 0.0, 90),
            Prop("interiors/stool_wood_lod0", -0.2, 0.0, -90),
            Prop("village/shepherd_cottage_lod0", 4.0, 3.6, 180, 0.5),
            Prop("village/log_pile_lod0", -6.6, 2.0, 20),
            Prop("village/quench_trough_lod0", 6.4, -3.4, 0),
            Prop("village/wooden_barrel_lod0", -6.8, -0.6, 0),
            Prop("farm/hay_bale_lod0", 6.6, 0.8, 30),
            Prop("farm/hay_bale_lod0", 6.6, -1.0, 0),
            Prop("farm/scarecrow_lod0", -3.0, 4.4, 180),
        ],
        npcs=[Npc("scout", 1.6, -2.0, 180, "interior-folk.lua"),
              Npc("farmhand", -4.6, -2.4, 220, "interior-folk.lua")],
        lights=[("0xffd0a0", 6_000, 0.0, 0.0, 2.2)]))
    # ── CELEIRO 28×20 ─────────────────────────────────────────────────────
    R.append(Room("barn", "celeiro", 28, 20, 0, 2, door=(-26.11, 30.00),
        floor="dirt_floor", wall="stone_wall", windows=1,
        props=[
            Prop("farm/hay_bale_lod0", -8.0, 5.0, 0),
            Prop("farm/hay_bale_lod0", -6.0, 3.4, 20),
            Prop("farm/hay_bale_lod0", 8.0, 5.0, 350),
            Prop("farm/hay_bale_lod0", 6.0, 3.2, 0),
            Prop("farm/hay_bale_lod0", -9.0, -4.0, 0),
            Prop("village/log_pile_lod0", 8.6, -4.4, 60, 1.3),
            Prop("village/wooden_crate_lod0", -2.6, -7.2, 0),
            Prop("village/wooden_crate_lod0", 2.8, -7.0, 24),
            Prop("village/wooden_barrel_lod0", 4.4, -7.2, 0),
            Prop("farm/chicken_coop_lod0", 10.6, -1.0, 90),
            Prop("farm/fence_segment_lod0", -11.0, 0.0, 90, 1.2),
            Prop("farm/fence_segment_lod0", -11.0, -3.2, 90, 1.2),
            Prop("village/chopping_block_lod0", 5.0, -6.0, 0),
            Prop("props/rock_mossy_lod0", 3.4, -6.6, 0, 1.2, "precompute"),
            Prop("village/iron_brazier_lod0", 0.0, 7.4, 180, fire=True, light=("0xffa83a", 3_400, 1.1)),
        ],
        npcs=[Npc("guard", 2.0, 4.6, 0, "watch-guard.lua")],
        lights=[("0xffd0a0", 7_000, 0.0, 0.0, 2.3)]))
    # ── CASA COMPRIDA (taberna) 28×20 ─────────────────────────────────────
    R.append(Room("longhouse", "taberna", 28, 20, 1, 2, door=(35.53, -37.53),
        floor="wood_floor", windows=2,
        props=[
            Prop("interiors/tavern_bar_lod0", 0, 5.4, 180),
            Prop("village/wooden_barrel_lod0", -8.0, 6.6, 0),
            Prop("village/wooden_barrel_lod0", -7.0, 6.6, 0),
            Prop("village/wooden_barrel_lod0", 8.0, 6.6, 0),
            Prop("interiors/fireplace_hearth_lod0", 0, 8.6, 180, fire=True, light=("0xffb070", 10_000, 1.4)),
            Prop("interiors/dining_table_lod0", -6.0, -1.0, 0),
            Prop("interiors/dining_table_lod0", 6.0, -1.0, 0),
            Prop("interiors/dining_table_lod0", -6.0, -5.4, 0),
            Prop("interiors/dining_table_lod0", 6.0, -5.4, 0),
            Prop("interiors/stool_wood_lod0", -7.8, -1.0, 90),
            Prop("interiors/stool_wood_lod0", -4.2, -1.0, -90),
            Prop("interiors/stool_wood_lod0", 7.8, -1.0, 90),
            Prop("interiors/stool_wood_lod0", 4.2, -1.0, -90),
            Prop("interiors/stool_wood_lod0", -7.8, -5.4, 90),
            Prop("interiors/stool_wood_lod0", 7.8, -5.4, 90),
            Prop("interiors/dining_table_lod0", 0, -3.4, 0),
            Prop("interiors/wooden_chair_lod0", 0, -5.4, 180),
            Prop("interiors/candelabra_tall_lod0", -12.0, 2.0, 0, light=("0xffd9a0", 8_000, 2.2)),
            Prop("interiors/candelabra_tall_lod0", 12.0, 2.0, 0, light=("0xffd9a0", 8_000, 2.2)),
            Prop("interiors/spinning_wheel_lod0", 11.4, -6.2, 90),
            Prop("village/notice_board_lod0", -13.0, 0.0, 90, script="notice-board.lua"),
        ],
        npcs=[Npc("innkeeper", -2.0, 4.0, 0, "interior-keeper.lua"),
              Npc("bard", 2.6, -3.6, 30, "interior-folk.lua"),
              Npc("merchant", 11.0, 3.6, 90, "merchant.lua")],
        lights=[("0xffcf9a", 9_000, 0.0, -1.0, 2.4)]))
    # ── BANCA DO MERCADO 18×14 (aberta) ───────────────────────────────────
    R.append(Room("market", "banca do mercado", 18, 14, 2, 2, door=(10.10, -15.70),
        floor="stone_floor", open_air=True,
        props=[
            Prop("village/market_stall_lod0", 0, -2.0, 180, 1.0, "trimesh", "merchant.lua"),
            Prop("village/medieval_well_lod0", -6.4, 3.6, 0, 0.9),
            Prop("village/wooden_crate_lod0", 6.4, 4.2, 0),
            Prop("village/wooden_crate_lod0", 7.4, 3.2, 30),
            Prop("village/wooden_barrel_lod0", 6.8, 1.2, 0),
            Prop("village/market_stall_lod0", 5.4, -4.6, 0, 0.85),
        ],
        npcs=[Npc("merchant", -1.6, 1.6, 200, "merchant.lua"),
              Npc("farmhand", 5.0, -3.0, 0, "interior-folk.lua")],
        lights=[("0xffd9a0", 7_000, 0.0, 0.0, 2.6)]))
    return R


# ── EMISSÃO ────────────────────────────────────────────────────────────────
def emit_shell(room: Room, out: list[str]) -> None:
    parts = shell(room)
    out.append(
        f'    <Composition name="interior.{room.id}.shell" translation="{room.cx:.0f} 0 {room.cz:.0f}" body="fixed" collider="auto">'
    )
    for b in parts:
        key = b.material if b.material != "wall" else room.wall
        attrs = f'translation="{b.x:.2f} {b.y:.2f} {b.z:.2f}" half-size="{b.hx:.2f} {b.hy:.2f} {b.hz:.2f}"'
        if b.yaw:
            attrs += f' transform="rotation: 0 {b.yaw} 0"'
        out.append(f"      {mat('Box', key)} {attrs} />")
    out.append("    </Composition>")


def emit_prop(room: Room, p: Prop, out: list[str]) -> None:
    x, z = room.cx + p.x, room.cz + p.z
    # Nome do colisor: o GLB de colisão NÃO leva o sufixo `_lod0` (o
    # ficheiro é `tavern_bar_collision.glb`, não `tavern_bar_lod0_collision`).
    # Um `collision` que não exista para aquele prop cai em `precompute`
    # (casco convexo do próprio mesh de render), que não precisa de ficheiro.
    base = p.asset[:-5] if p.asset.endswith("_lod0") else p.asset
    tem_colisao = glb_exists(f"{base}_collision")
    kind = p.collider
    if kind == "trimesh" and not tem_colisao:
        kind = "precompute"
    coll = {
        "trimesh": f'shape: trimesh; mesh-url: /assets/meshes/{base}_collision.glb; mesh-anchor: base',
        "precompute": f'shape: precompute; mesh-url: /assets/meshes/{p.asset}.glb',
        "box": "shape: box; size: 1.6 0.06 1.6",
    }[kind]
    transform = f' transform="rotation: 0 {p.yaw} 0; scale: {p.scale} {p.scale} {p.scale}"' if (p.yaw or p.scale != 1.0) else ""
    script = f' script="{p.script}"' if p.script else ""
    nome = f'interior.{room.id}.{Path(p.asset).name.replace("_lod0", "").replace("_", ".")}'
    out.append(f'    <Entity name="{nome}" translation="{x:.1f} 0 {z:.1f}"{transform}{script} rigidbody="type: fixed; mass: 0; gravity-scale: 0" collider="{coll}">')
    out.append(f'      <GltfScene url="/assets/meshes/{p.asset}.glb" />')
    if p.light:
        color, intensity, height = p.light
        out.append(f'      <PointLight translation="0 {height:.2f} 0" color="{color}" intensity="{intensity}" shadows="false" />')
    out.append("    </Entity>")
    if p.fire:
        out.append(f'    <Entity translation="{x:.1f} 0 {z:.1f}">')
        out.append('      <ParticleSystem preset="fire" transform="pos: 0 0.75 0" particle-emitter="preset: fire; emission-rate: 30; shape-radius: 0.22; start-life-min: 0.4; start-life-max: 1.1; start-speed-min: 1.0; start-speed-max: 2.2; start-size-min: 0.2; start-size-max: 0.46; looping: 1; world-space: 1" />')
        out.append("    </Entity>")


def emit_npc(room: Room, n: Npc, out: list[str]) -> None:
    x, z = room.cx + n.x, room.cz + n.z
    rot = f' transform="rotation: 0 {n.yaw} 0"' if n.yaw else ""
    out.append(f'    <Entity name="interior.{room.id}.{n.role}" translation="{x:.1f} 0 {z:.1f}"{rot} script="{n.script}">')
    out.append(f'      <GltfScene url="/assets/meshes/characters/{n.model}_lod0.glb" />')
    out.append("    </Entity>")


def check_inside(R: list[Room]) -> None:
    """Nada pode nascer fora do casco (ou em cima da parede).

    Os offsets da mobília são escritos à mão; um dedo a mais punha uma cama
    meio metro dentro da parede e o resultado só se via em jogo. Margem
    grosseira (0,6 m do centro) — não substitui o olho, apanha o grosseiro.
    """
    for r in R:
        hw, hd = r.w * 0.5 - 0.6, r.d * 0.5 - 0.6
        for p in r.props:
            if abs(p.x) > hw or abs(p.z) > hd:
                raise SystemExit(f"{r.id}: prop {p.asset} fora do casco ({p.x},{p.z})")
        for n in r.npcs:
            if abs(n.x) > hw or abs(n.z) > hd:
                raise SystemExit(f"{r.id}: NPC {n.role} fora do casco ({n.x},{n.z})")
        # A porta (−Z) tem de ficar livre: nenhum prop no corredor central.
        for p in r.props:
            if p.z < -r.d * 0.5 + 3.0 and abs(p.x) < 1.6:
                raise SystemExit(f"{r.id}: prop {p.asset} tapa o vão da porta ({p.x},{p.z})")


def main() -> None:
    R = rooms()
    check_inside(R)
    out: list[str] = []
    add = out.append
    add("<!--")
    add("  BOLSA DE INTERIORES — GERADO por tools/gen_interiors.py (não editar à mão).")
    add("")
    add("  PORQUÊ UMA BOLSA: os ambientes internos NÃO vivem no mundo. A grelha fica")
    add(f"  em ({POCKET[0]:.0f},{POCKET[1]:.0f}), FORA da pegada do heightmap (world-size 4000), onde a engine")
    add("  já não gera colunas nem colliders de terreno — ver `TerrainRuntime::in_field`.")
    add("  O `<InteriorScene>` declara o retângulo (bbox das salas + folga): isenta a `WorldBorder`, tira o")
    add("  bioma (névoa/tinta/exposição) e a chuva. O que sobra é distância: >2 km não")
    add("  entra em render-distance, cull-distance nem raio de ativação de IA.")
    add("")
    add("  ENTRADAS: o portal de saída de cada sala é colocado no vão −Z a partir da")
    add("  MESMA tabela de salas que `scripts/building-portal.lua` usa (ROOMS); o")
    add("  script confere no arranque que a sala existe a <1 m do sítio esperado, para")
    add("  XML e Lua não se afastarem em silêncio.")
    add("")
    add("  Regenerar:  python3 tools/gen_interiors.py")
    add("-->")
    add("<world>")
    # O retângulo é DERIVADO das salas (bbox + folga): escrito à mão, ficava
    # mais pequeno que a grelha ao primeiro acrescento e as salas de fora
    # perdiam a isenção de fronteira (o herói era devolvido ao vale a meio da
    # visita) e o corte de bioma/chuva.
    xs = [r.cx - r.w * 0.5 - MARGIN for r in R] + [r.cx + r.w * 0.5 + MARGIN for r in R]
    zs = [r.cz - r.d * 0.5 - MARGIN for r in R] + [r.cz + r.d * 0.5 + MARGIN for r in R]
    add(
        f'  <InteriorScene at="{(min(xs) + max(xs)) / 2:.0f} {(min(zs) + max(zs)) / 2:.0f}"'
        f' size="{max(xs) - min(xs):.0f} {max(zs) - min(zs):.0f}" />'
    )
    add("")
    # mobília partilhada entre salas: cada `Prop` gera uma Entity própria.
    for room in R:
        add(f"  <!-- ══════════════════ {room.name.upper()} ({room.w:.0f}×{room.d:.0f}) ══════════════════ -->")
        emit_shell(room, out)
        for p in room.props:
            emit_prop(room, p, out)
        for n in room.npcs:
            emit_npc(room, n, out)
        for color, intensity, lx, lz, ly in room.lights:
            add(f'    <Group translation="{room.cx + lx:.0f} 0 {room.cz + lz:.0f}" body="none" collider="none">')
            add(f'      <PointLight translation="0 {ly:.2f} 0" color="{color}" intensity="{intensity}" />')
            add("    </Group>")
        # saída: no vão −Z, 1 m fora da parede
        add(f'    <Entity name="portal.exit_{room.id}" translation="{room.cx:.0f} 0 {room.cz + room.exit_z:.1f}" script="building-portal.lua" />')
        add("")
    add("</world>")
    raiz = Path(__file__).resolve().parents[1]
    destino = raiz / "world" / "interiors.xml"
    destino.write_text("\n".join(out) + "\n", encoding="utf-8")
    portal = raiz / "scripts" / "building-portal.lua"
    portal.write_text(portal_lua(R), encoding="utf-8")
    print(f"{destino}: {len(R)} salas, {sum(len(r.props) for r in R)} props, {sum(len(r.npcs) for r in R)} NPCs")
    print(f"{portal}: registo de {len(R)} portais + lógica")


PORTAL_HEADER = """-- building-portal.lua: portas bidireccionais entre a VILA e a BOLSA DE INTERIORES.
--
-- GERADO por tools/gen_interiors.py — a tabela ROOMS do fim é derivada da
-- MESMA tabela de salas que gera world/interiors.xml. Não editar à mão: mudar
-- a bolsa (`POCKET`) ou as salas é mudar o gerador.
--
-- v5 (2026-09-11): a grelha de interiores saiu da área do mapa. Vive numa bolsa
-- declarada por <InteriorScene> a FORA da pegada do heightmap (world-size
-- 4000), onde a engine não gera colunas de terreno nem colliders. Um NPC/portal
-- que a navegue não tem terreno para pisar: o chão é o soalho da sala.
--
-- SENTIDO: decidido pela POSIÇÃO. Dentro da bolsa (muito para lá de POCKET) a
-- entidade é uma SAÍDA interior; fora é uma porta de rua. Nada de tabelas
-- inversas mantidas à mão — o par porta↔saída sai de uma linha só.
--
-- OS Y's NÃO SÃO ADIVINHADOS: dentro da bolsa o destino é o soalho (0.6 m,
-- não há terreno); fora, `viber.ground_below` resolve a cota do adro. A versão
-- anterior teleportava para y≈0.3 na vila e o herói caía 24 m.
local POCKET = { x = %(px).1f, z = %(pz).1f }
local POCKET_MIN_X = POCKET.x - 200.0   -- tudo acima disto é interiores

local FLOOR_Y = 0.6      -- soalho da sala (a laje está a 0.12)
local INWARD = 6.0       -- quanto o herói entra para dentro ao chegar
local MATCH_R = 3.0      -- raio de casamento porta↔saída

function on_update(dt)
  local st = viber.state()
  local x, y, z = viber.position()
  local inside_pocket = x > POCKET_MIN_X

  if not st.ready then
    st.ready = true
    viber.set_interaction(st.inside and "Sair" or "Entrar", "e", 2.8)
  end
  -- `interacted` não consome o evento duas vezes no mesmo frame entre
  -- entidades: cada portal decide pelo seu próprio cooldown.
  if not viber.interacted("e") then
    st.cd = false
    return
  end
  if st.cd then return end

  if inside_pocket then
    -- Saída interior: casar a minha posição RELATIVA à bolsa com uma sala.
    local rx, rz = x - POCKET.x, z - POCKET.z
    local best, bd = nil, MATCH_R * MATCH_R
    for _, r in ipairs(ROOMS) do
      local dx, dz = r.ox - rx, r.oz + r.exit_dz - rz
      local d = dx * dx + dz * dz
      if d < bd then bd, best = d, r end
    end
    if not best then
      viber.log("building-portal: saída sem sala a <" .. MATCH_R .. "m — grelha e XML fora de sincronia?")
      return
    end
    st.cd = true
    local gy = viber.ground_below(best.door_x, 200.0, best.door_z)
    viber.sound("door_close")
    viber.teleport_player(best.door_x, (gy or 25.0) + 0.25, best.door_z)
    viber.toast("A porta devolve-te à vila.")
  else
    -- Porta de rua: casar com a porta mais próxima.
    local best, bd = nil, MATCH_R * MATCH_R
    for _, r in ipairs(ROOMS) do
      local dx, dz = r.door_x - x, r.door_z - z
      local d = dx * dx + dz * dz
      if d < bd then bd, best = d, r end
    end
    if not best then
      viber.log("building-portal: porta sem sala declarada a <" .. MATCH_R .. "m")
      return
    end
    st.cd = true
    viber.sound("door_open")
    viber.teleport_player(POCKET.x + best.ox, FLOOR_Y, POCKET.z + best.oz + best.exit_dz + INWARD)
    viber.toast("Entras no edifício.")
  end
end

-- ── REGISTO (gerado: { id, porta exterior (x, z), offset da sala (x, z), z local do vão }) ──
local ROOMS = {
%(linhas)s}
"""


def portal_lua(R: list[Room]) -> str:
    linhas = "".join(
        f'  {{ "{r.id}",'
        f" {r.door[0]:8.2f}, {r.door[1]:8.2f},"
        f" {r.ix * STEP_X:7.1f}, {r.iz * STEP_Z:7.1f},"
        f" {r.exit_z:7.2f} }},\n"
        for r in R
    )
    return PORTAL_HEADER % {
        "px": POCKET[0],
        "pz": POCKET[1],
        "linhas": linhas,
    }




if __name__ == "__main__":
    main()
