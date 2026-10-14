#!/usr/bin/env python3
"""Gera os PORTAIS e a BOLSA DE INTERIORES do simple-rpg.

Ficheiros escritos (os dois lados — procedural e estático):

* ``world/interiors.xml`` + ``static/world/interiors.xml``
* ``world/cities/discordia/portals.xml`` + ``static/world/cities/discordia/portals.xml``
* ``scripts/building-portal.lua`` (template data-driven; só as CONSTANTES
  — POCKET e a grelha — vêm daqui)

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

O REGISTO DE PORTAIS é DATA-DRIVEN (v6): o ``building-portal.lua`` NÃO embute
tabela nenhuma — percorre ``viber.find_all("portal.")`` e casa pelo NOME
(``portal.<room>``/``portal.<room>.<sufixo>`` na rua, ``portal.exit_<room>``
na bolsa). Mover/renomear/multiplicar portas no editor já não desincroniza
nada; este gerador apenas decide os NOMES canónicos e as posições de partida.
Mudar a bolsa = mudar ``POCKET`` aqui (o script herda a constante).

Correr:  python3 tools/gen_interiors.py
"""

from __future__ import annotations

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
    "stone_floor": (
        "/assets/textures/cobblestone_road/albedo.ktx2",
        "/assets/textures/cobblestone_road/normal.ktx2",
        3.2,
    ),
    "wood_floor": ("/assets/textures/wood_planks/albedo.ktx2", "/assets/textures/wood_planks/normal.ktx2", 3.0),
    "dirt_floor": ("/assets/textures/dirt_road/albedo.ktx2", None, 3.0),
    "plaster": ("/assets/textures/wall_plaster/albedo.ktx2", "/assets/textures/wall_plaster/normal.ktx2", 3.4),
    "stone_wall": ("/assets/textures/mountain_stone/albedo.ktx2", "/assets/textures/mountain_stone/normal.ktx2", 3.6),
    "beam": ("/assets/textures/wood_planks/albedo.ktx2", "/assets/textures/wood_planks/normal.ktx2", 1.6),
    "roof": ("/assets/textures/roof_tiles/albedo.ktx2", "/assets/textures/roof_tiles/normal.ktx2", 2.4),
    "cloth": ("/assets/textures/wood_planks/albedo.ktx2", None, 2.0),
}

WALL_H = 2.4  # altura da parede (m)
WALL_T = 0.14  # meia-espessura
DOOR_W = 2.6  # vão da porta
DOOR_H = 2.1  # altura do vão (o lintel fecha por cima)
WIN_W = 1.6
WIN_SILL = 0.9
WIN_TOP = 1.9


def mat(prim: str, key: str, color: str | None = None) -> str:
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
    collider: str = "trimesh"  # trimesh | precompute | box
    script: str | None = None
    fire: bool = False  # chama + luz quente no topo
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
        model = ROLE_MODEL.get(self.role, self.role)
        return model if available(model) else ROLE_FALLBACK[self.role]


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
    beams: bool = True  # no-op desde 2026-09-13 (ver `shell`)
    windows: int = 2
    # Porta de rua (world/cities/discordia/portals.xml): o par
    # porta↔saída vive no NOME (`portal.<id>`/`portal.<id>.<sufixo>` ↔
    # `portal.exit_<id>`), não em tabelas alinhadas por ordem.
    door: tuple[float, float] = (0.0, 0.0)
    # Sufixo da porta primária (`market` → "stall_a"): None = `portal.<id>` puro.
    door_suffix: str | None = None
    # Portas EXTRA da mesma sala ((x, z), sufixo) — ex.: as 3 bancas do mercado.
    extra_doors: list[tuple[tuple[float, float], str]] = field(default_factory=list)
    # Nota de colocação (comentário no portals.xml: edifício/yaw/origem da porta).
    door_note: str = ""
    props: list[Prop] = field(default_factory=list)
    npcs: list[Npc] = field(default_factory=list)
    lights: list[tuple] = field(default_factory=list)  # (cor, intensidade, x, z, y)
    open_air: bool = False  # banca/mercado: sem paredes, só postes e toldo

    def street_doors(self) -> list[tuple[float, float, str]]:
        """Todas as portas de rua da sala: [(x, z, sufixo|None), …]."""
        doors = [(self.door[0], self.door[1], self.door_suffix)]
        doors.extend((x, z, suffix) for (x, z), suffix in self.extra_doors)
        return doors

    @property
    def cx(self) -> float:
        return POCKET[0] + self.ix * STEP_X

    @property
    def cz(self) -> float:
        return POCKET[1] + self.iz * STEP_Z

    @property
    def exit_z(self) -> float:
        """Z local do portal de saída (no vão -Z, 1 m fora da parede)."""
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
        w = (half_len - cursor) * 0.5
        boxes.append(Box((cursor + half_len) * 0.5, WALL_H * 0.5, z, w, WALL_H * 0.5, WALL_T, "wall"))
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
        w = (half_len - cursor) * 0.5
        boxes.append(Box(x, WALL_H * 0.5, (cursor + half_len) * 0.5, WALL_T, WALL_H * 0.5, w, "wall"))
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
    out += wall_run(hd, hw, [])  # norte: sólida
    out += wall_run(-hd, hw, [(0.0, DOOR_W)])  # sul: vão da porta
    out += wall_side(hw, hd, win_e)
    out += wall_side(-hw, hd, win_w)
    # postes nos cantos + ombreiras da porta
    for sx in (-1, 1):
        for sz in (-1, 1):
            out.append(Box(sx * (hw - 0.18), WALL_H * 0.5, sz * (hd - 0.18), 0.18, WALL_H * 0.5, 0.18, "beam"))
    for sx in (-1, 1):
        out.append(Box(sx * (DOOR_W * 0.5 + 0.16), DOOR_H * 0.5, -hd, 0.16, DOOR_H * 0.5, WALL_T + 0.04, "beam"))
    out.append(Box(0.0, DOOR_H + 0.09, -hd, DOOR_W * 0.5 + 0.3, 0.09, WALL_T + 0.06, "beam"))
    # SEM vigas de tecto: a câmara de interior olha a sala de cima (JRPG 16
    # bits) e o esqueleto do telhado ficava entre a câmara e o chão, a cortar
    # a divisão em tiras e a tapar mobília e NPCs (pedido do utilizador
    # 2026-09-13). As paredes e os postes chegam para a sala ler como
    # construída; o campo `beams` fica como no-op declarado para não partir
    # salas antigas.
    # rodapé
    for sx in (-1, 1):
        out.append(Box(sx * (hw - 0.16), 0.14, 0.0, 0.05, 0.14, hd - 0.6, "beam"))
    out.append(Box(0.0, 0.14, hd - 0.16, hw - 0.6, 0.14, 0.05, "beam"))
    return out


# ── SALAS ──────────────────────────────────────────────────────────────────
# Convenção: a porta está SEMPRE no vão -Z (o shell abre o buraco aí), logo o
# ponto focal de cada sala (altar, fornalha, lareira, balcão) fica em +Z e o
# que sobra encosta às paredes laterais. `check_inside` recusa prop/NPC no
# corredor da porta.
def rooms() -> list[Room]:
    """As nove salas. O foco (altar, fornalha, lareira, balcão) fica SEMPRE em
    +Z e a porta em -Z, para o herói entrar e ver a sala pela frente."""
    R: list[Room] = []
    # ── CAPELA 24x18 ──────────────────────────────────────────────────────
    R.append(
        Room(
            "chapel",
            "capela",
            24,
            18,
            1,
            0,
            door=(7.46, 22.46),
            door_note="chapel @ (10,25) yaw -135 — +Z arched door",
            floor="stone_floor",
            wall="plaster",
            windows=2,
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
                # 3.ª fila + tapetes da nave: a capela é a sala que o jogador vê
                # primeiro e a que a câmara de interior enquadra inteira — vale o
                # detalhe (pedido do utilizador 2026-09-13).
                Prop("interiors/chapel_pew_lod0", -3.9, -6.2, 0),
                Prop("interiors/chapel_pew_lod0", 3.9, -6.2, 0),
                Prop("interiors/rug_woven_lod0", 0.0, -2.6, 0, 1.3),
                Prop("interiors/rug_woven_lod0", 0.0, -5.4, 0, 1.3),
                Prop("interiors/confessional_lod0", -9.4, -4.6, 90),
                Prop("interiors/chapel_statue_lod0", 9.6, -5.6, 90),
                # Sacristia encostada ao fundo: estante e cómoda em espelho.
                Prop("interiors/bookshelf_lod0", -10.9, 6.4, 90),
                Prop("interiors/cupboard_lod0", 10.9, 6.4, -90),
                Prop("interiors/stool_wood_lod0", -6.0, 4.0, 20),
                # Lanternas de parede: luz quente à altura dos olhos, sem sombra.
                Prop("interiors/lantern_hanging_lod0", -11.0, -1.6, 90, light=("0xffcf8a", 5_200, 1.9)),
                Prop("interiors/lantern_hanging_lod0", 11.0, -1.6, -90, light=("0xffcf8a", 5_200, 1.9)),
                Prop("interiors/candelabra_tall_lod0", -2.4, -7.4, 0, light=("0xffd9a0", 6_000, 2.2)),
                Prop("interiors/candelabra_tall_lod0", 2.4, -7.4, 0, light=("0xffd9a0", 6_000, 2.2)),
                Prop("props/stone_pillar_lod0", -10.6, 1.0, 0, 1.1),
                Prop("props/stone_pillar_lod0", 10.6, 1.0, 0, 1.1),
                Prop("village/iron_brazier_lod0", -4.8, 6.2, 0, fire=True, light=("0xffa83a", 3_600, 1.1)),
                Prop("village/iron_brazier_lod0", 4.8, 6.2, 0, fire=True, light=("0xffa83a", 3_600, 1.1)),
            ],
            npcs=[
                Npc("priest", 1.2, 3.4, 0, "interior-keeper.lua"),
                Npc("elder", -7.6, -7.0, 160, "interior-folk.lua"),
            ],
            lights=[("0xffd9a0", 8_000, 0.0, 0.0, 2.3)],
        )
    )
    # ── FERRARIA 22x16 ────────────────────────────────────────────────────
    R.append(
        Room(
            "forge",
            "ferraria",
            22,
            16,
            2,
            0,
            door=(-30.47, -29.06),
            door_note="village_forge @ (-31,-33) yaw 0 — +Z mouth slightly +X, north",
            floor="dirt_floor",
            wall="stone_wall",
            windows=2,
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
            lights=[("0xffc090", 6_000, 0.0, 0.0, 2.2)],
        )
    )
    # ── CASA COMUM 20x16 ──────────────────────────────────────────────────
    R.append(
        Room(
            "house_a",
            "casa comum",
            20,
            16,
            0,
            0,
            door=(26.35, 8.44),
            door_note="house_a @ (25,12) yaw -90 — village_house door on local -X → world south",
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
            npcs=[
                Npc("cook", -6.4, 5.2, 200, "interior-keeper.lua"),
                Npc("innkeeper", 2.6, 1.2, 20, "interior-keeper.lua"),
            ],
            lights=[("0xffcf9a", 9_000, 0.0, 0.4, 2.3)],
        )
    )
    # ── CASA B (biblioteca) 20x16 ─────────────────────────────────────────
    R.append(
        Room(
            "house_b",
            "casa do escriba",
            20,
            16,
            0,
            1,
            door=(-17.44, 22.47),
            door_note="house_b @ (-19,19) yaw 135 — door local -X → world NE",
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
            lights=[("0xffd0a0", 6_000, 0.0, -0.6, 2.3)],
        )
    )
    # ── CASA C (cozinha) 20x16 ────────────────────────────────────────────
    R.append(
        Room(
            "house_c",
            "cozinha",
            20,
            16,
            1,
            1,
            door=(-20.47, -18.44),
            door_note="house_c @ (-17,-20) yaw 45 — door local -X → world SW",
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
            npcs=[Npc("cook", -1.4, 3.4, 180, "interior-keeper.lua"), Npc("bard", 3.4, -3.0, 40, "interior-folk.lua")],
            lights=[("0xffcf9a", 8_000, 0.0, 0.0, 2.3)],
        )
    )
    # ── CABANA DO PASTOR 18x14 ────────────────────────────────────────────
    R.append(
        Room(
            "shepherd",
            "cabana do pastor",
            18,
            14,
            2,
            1,
            door=(-22.33, 12.95),
            door_note="shepherd_cottage @ (-21,8) yaw 0 — +Z door left of center",
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
            npcs=[
                Npc("scout", 1.6, -2.0, 180, "interior-folk.lua"),
                Npc("farmhand", -4.6, -2.4, 220, "interior-folk.lua"),
            ],
            lights=[("0xffd0a0", 6_000, 0.0, 0.0, 2.2)],
        )
    )
    # ── CELEIRO 28x20 ─────────────────────────────────────────────────────
    R.append(
        Room(
            "barn",
            "celeiro",
            28,
            20,
            0,
            2,
            door=(-26.11, 30.00),
            door_note="village_barn @ (-33.1,30) yaw 90 — +Z doors → world east",
            floor="dirt_floor",
            wall="stone_wall",
            windows=1,
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
            lights=[("0xffd0a0", 7_000, 0.0, 0.0, 2.3)],
        )
    )
    # ── CASA COMPRIDA (taberna) 28x20 ─────────────────────────────────────
    R.append(
        Room(
            "longhouse",
            "taberna",
            28,
            20,
            1,
            2,
            door=(35.53, -37.53),
            door_note="longhouse @ (31,-33) yaw 135 — +Z gable doors",
            floor="wood_floor",
            windows=2,
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
            npcs=[
                Npc("innkeeper", -2.0, 4.0, 0, "interior-keeper.lua"),
                Npc("bard", 2.6, -3.6, 30, "interior-folk.lua"),
                Npc("merchant", 11.0, 3.6, 90, "merchant.lua"),
            ],
            lights=[("0xffcf9a", 9_000, 0.0, -1.0, 2.4)],
        )
    )
    # ── BANCA DO MERCADO 18x14 (aberta) ───────────────────────────────────
    R.append(
        Room(
            "market",
            "banca do mercado",
            18,
            14,
            2,
            2,
            door=(10.10, -15.70),
            door_suffix="stall_a",
            # As 3 bancas partilham a sala `market`: o sufixo distingue as
            # portas, o NOME comum é o que as liga à sala (data-driven).
            extra_doors=[((11.7, -14.4), "stall_b"), ((18.9, -20.3), "stall_c")],
            door_note="market stalls — +Z counter",
            floor="stone_floor",
            open_air=True,
            props=[
                Prop("village/market_stall_lod0", 0, -2.0, 180, 1.0, "trimesh", "merchant.lua"),
                Prop("village/medieval_well_lod0", -6.4, 3.6, 0, 0.9),
                Prop("village/wooden_crate_lod0", 6.4, 4.2, 0),
                Prop("village/wooden_crate_lod0", 7.4, 3.2, 30),
                Prop("village/wooden_barrel_lod0", 6.8, 1.2, 0),
                Prop("village/market_stall_lod0", 5.4, -4.6, 0, 0.85),
            ],
            npcs=[Npc("merchant", -1.6, 1.6, 200, "merchant.lua"), Npc("farmhand", 5.0, -3.0, 0, "interior-folk.lua")],
            lights=[("0xffd9a0", 7_000, 0.0, 0.0, 2.6)],
        )
    )
    return R


# ── EMISSÃO ────────────────────────────────────────────────────────────────
def emit_shell(room: Room, out: list[str]) -> None:
    parts = shell(room)
    out.append(
        f'    <Composition name="interior.{room.id}.shell" '
        f'translation="{room.cx:.0f} 0 {room.cz:.0f}" body="fixed" collider="auto">'
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
        "trimesh": f"shape: trimesh; mesh-url: /assets/meshes/{base}_collision.glb; mesh-anchor: base",
        "precompute": f"shape: precompute; mesh-url: /assets/meshes/{p.asset}.glb",
        "box": "shape: box; size: 1.6 0.06 1.6",
    }[kind]
    transform = (
        f' transform="rotation: 0 {p.yaw} 0; scale: {p.scale} {p.scale} {p.scale}"' if (p.yaw or p.scale != 1.0) else ""
    )
    script = f' script="{p.script}"' if p.script else ""
    nome = f"interior.{room.id}.{Path(p.asset).name.replace('_lod0', '').replace('_', '.')}"
    out.append(
        f'    <Entity name="{nome}" translation="{x:.1f} 0 {z:.1f}"{transform}{script} '
        f'rigidbody="type: fixed; mass: 0; gravity-scale: 0" collider="{coll}">'
    )
    out.append(f'      <GltfScene url="/assets/meshes/{p.asset}.glb" />')
    if p.light:
        color, intensity, height = p.light
        out.append(
            f'      <PointLight translation="0 {height:.2f} 0" color="{color}" '
            f'intensity="{intensity}" shadows="false" />'
        )
    out.append("    </Entity>")
    if p.fire:
        out.append(f'    <Entity translation="{x:.1f} 0 {z:.1f}">')
        out.append(
            '      <ParticleSystem preset="fire" transform="pos: 0 0.75 0" '
            'particle-emitter="preset: fire; emission-rate: 30; shape-radius: 0.22; '
            "start-life-min: 0.4; start-life-max: 1.1; start-speed-min: 1.0; "
            "start-speed-max: 2.2; start-size-min: 0.2; start-size-max: 0.46; "
            'looping: 1; world-space: 1" />'
        )
        out.append("    </Entity>")


def emit_npc(room: Room, n: Npc, out: list[str]) -> None:
    x, z = room.cx + n.x, room.cz + n.z
    rot = f' transform="rotation: 0 {n.yaw} 0"' if n.yaw else ""
    out.append(
        f'    <Entity name="interior.{room.id}.{n.role}" translation="{x:.1f} 0 {z:.1f}"{rot} script="{n.script}">'
    )
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
        # A porta (-Z) tem de ficar livre: nenhum prop no corredor central.
        for p in r.props:
            if p.z < -r.d * 0.5 + 3.0 and abs(p.x) < 1.6:
                raise SystemExit(f"{r.id}: prop {p.asset} tapa o vão da porta ({p.x},{p.z})")


def interiors_xml(R: list[Room]) -> str:
    """A bolsa de interiores (lado PROCEDURAL — com comentários de autor)."""
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
    add("  ENTRADAS: cada sala tem `portal.exit_<room>` no vão -Z. O par porta↔saída")
    add("  é DATA-DRIVEN — o `scripts/building-portal.lua` casa pelo NOME das")
    add("  entidades (`portal.<room>[.sufixo]` na rua), sem tabela espelhada: mover")
    add("  uma porta no editor não desincroniza nada.")
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
    # `room-size` = o passo da grelha: dá à engine a câmara de interior no
    # estilo JRPG de 16 bits (uma SALA por ecrã, fixa, sem follow). O
    # enquadramento sai do centro da célula, por isso tem de ser a MESMA
    # grelha que posiciona as salas.
    add(
        f'  <InteriorScene at="{(min(xs) + max(xs)) / 2:.0f} {(min(zs) + max(zs)) / 2:.0f}"'
        f' size="{max(xs) - min(xs):.0f} {max(zs) - min(zs):.0f}"'
        f' room-size="{STEP_X:.0f} {STEP_Z:.0f}"'
        f' room-origin="{POCKET[0]:.0f} {POCKET[1]:.0f}"'
        f' camera-distance="24" camera-pitch="58" camera-yaw="0" />'
    )
    add("")
    # mobília partilhada entre salas: cada `Prop` gera uma Entity própria.
    for room in R:
        add(f"  <!-- ══════════════════ {room.name.upper()} ({room.w:.0f}x{room.d:.0f}) ══════════════════ -->")
        emit_shell(room, out)
        for p in room.props:
            emit_prop(room, p, out)
        for n in room.npcs:
            emit_npc(room, n, out)
        for color, intensity, lx, lz, ly in room.lights:
            add(f'    <Group translation="{room.cx + lx:.0f} 0 {room.cz + lz:.0f}" body="none" collider="none">')
            add(f'      <PointLight translation="0 {ly:.2f} 0" color="{color}" intensity="{intensity}" />')
            add("    </Group>")
        # saída: no vão -Z, 1 m fora da parede
        add(
            f'    <Entity name="portal.exit_{room.id}" '
            f'translation="{room.cx:.0f} 0 {room.cz + room.exit_z:.1f}" script="building-portal.lua" />'
        )
        add("")
    add("</world>")
    return "\n".join(out) + "\n"


# Anel de partículas à porta. LOOP D: o preset `magic` é TEAL (color_a
# [0.6,0.35,1.0] → color_b [0.3,0.8,1.0]) e à noite os anéis das portas
# liam-se como "chamas turquesa" no meio da vila (blobs R−B ≈ −60 medidos no
# frame do crítico). `start-color: #ffb35c` trava a matiz num dourado quente —
# o anel mantém o movimento mágico mas lê como luz de lareira à porta.
RING_BIG = (
    'preset: magic; start-color: #ffb35c; emission-rate: 16; shape-radius: 0.65; '
    "start-life-min: 0.7; start-life-max: 1.4; start-speed-min: 0.15; start-speed-max: 0.55; "
    "start-size-min: 0.1; start-size-max: 0.22; looping: 1; world-space: 1"
)
RING_SMALL = (
    'preset: magic; start-color: #ffb35c; emission-rate: 12; shape-radius: 0.5; '
    "start-life-min: 0.6; start-life-max: 1.2; start-speed-min: 0.1; start-speed-max: 0.4; "
    "start-size-min: 0.08; start-size-max: 0.18; looping: 1; world-space: 1"
)


def portals_xml(R: list[Room]) -> str:
    """As portas de rua (lado PROCEDURAL — com comentários de colocação).

    NOMES canónicos: `portal.<room_id>` puro, ou `portal.<room_id>.<sufixo>`
    quando a sala tem várias portas (as bancas do mercado). É o NOME que o
    `building-portal.lua` casa — NÃO a posição.
    """
    out: list[str] = []
    add = out.append
    add("<!--")
    add("  Discordia — portas de edifícios (marcadores à frente da porta).")
    add("  GERADO por tools/gen_interiors.py (posições de partida + nomes canónicos;")
    add("  depois disso o editor manda: o building-portal.lua é data-driven e lê")
    add("  nome+posição das entidades ao vivo).")
    add("")
    add("  NOME = sala: `portal.<room>` ou `portal.<room>.<sufixo>` (multi-porta;")
    add("  dentro da bolsa, `portal.exit_<room>` devolve à rua.")
    add("-->")
    add("<world>")
    add('  <Group name="city.portals" translation="0 0 0">')
    for room in R:
        for i, (x, z, suffix) in enumerate(room.street_doors()):
            name = f"portal.{room.id}.{suffix}" if suffix else f"portal.{room.id}"
            emitter = RING_SMALL if room.open_air else RING_BIG
            y = "0.3" if room.open_air else "0.35"
            if room.door_note and i == 0:
                add(f"    <!-- {room.door_note} -->")
            add(
                f'    <Entity name="{name}" translation="{x:g} 0 {z:g}" script="building-portal.lua">'
            )
            add(
                f'      <ParticleSystem preset="magic" transform="pos: 0 {y} 0" '
                f'particle-emitter="{emitter}" />'
            )
            add("    </Entity>")
    add("  </Group>")
    add("</world>")
    return "\n".join(out) + "\n"


def static_for(text: str) -> str:
    """Lado ESTÁTICO: mesmo conteúdo sem os comentários de autor (o estilo do
    `viber bake`), com a declaração XML ao topo. Re-correr `viber bake --force`
    re-normaliza este ficheiro — o que está aqui é para o static tree committed
    não ficar com nomes/portas velhos quando a tool regenera o lado procedural."""

    import re

    body = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = [ln.rstrip() for ln in body.splitlines()]
    body = "\n".join(ln for ln in lines if ln.strip())
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def main() -> None:
    R = rooms()
    check_inside(R)
    raiz = Path(__file__).resolve().parents[1]

    interiors = interiors_xml(R)
    portals = portals_xml(R)
    ficheiros = {
        raiz / "world" / "interiors.xml": interiors,
        raiz / "static" / "world" / "interiors.xml": static_for(interiors),
        raiz / "world" / "cities" / "discordia" / "portals.xml": portals,
        raiz / "static" / "world" / "cities" / "discordia" / "portals.xml": static_for(portals),
    }
    for destino, conteudo in ficheiros.items():
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(conteudo, encoding="utf-8")
        print(f"{destino.relative_to(raiz)}")

    portal = raiz / "scripts" / "building-portal.lua"
    portal.write_text(portal_lua(), encoding="utf-8")
    portas = sum(len(r.street_doors()) for r in R)
    print(f"{portal.relative_to(raiz)}: lógica data-driven ({len(R)} salas, {portas} portas)")

PORTAL_LUA = r"""-- building-portal.lua: portas bidireccionais entre a VILA e a BOLSA DE INTERIORES.
--
-- GERADO por tools/gen_interiors.py (só as CONSTANTES vêm de lá — POCKET e a
-- grelha têm de bater com o <InteriorScene> que a tool emite). Não editar à
-- mão: a lógica vive no template PORTAL_LUA do gerador.
--
-- v6 (2026-10-14), DATA-DRIVEN: o registo porta↔sala sai dos NOMES das
-- entidades — `portal.<room>`/`portal.<room>.<sufixo>` (porta de rua) e
-- `portal.exit_<room>` (saída na bolsa) — lidos com `viber.find_all` +
-- `viber.entity_name`/`viber.entity_position`. A v5 embutia uma tabela ROOMS
-- gerada: mover uma porta no editor desincronizava-a em silêncio e uma porta
-- a >3 m da posição gerada morria (ex-portal.market_stall_c, a 9,9 m da
-- sala). Agora o NOME carrega a sala e a POSIÇÃO sai da própria entidade —
-- mover/renomear portas no editor (ou multiplicá-las por sala) simplesmente
-- funciona.
--
-- A bolsa vive FORA da pegada do heightmap (world-size 4000), declarada por
-- <InteriorScene>: a engine não gera terreno nem colliders lá — o chão é o
-- soalho da sala (FLOOR_Y). Os Y's NÃO são adivinhados: fora, `viber.
-- ground_below` resolve a cota do adro (a v4 teleportava para y≈0.3 na vila
-- e o herói caía 24 m).
local POCKET = { x = %(px).1f, z = %(pz).1f }
local POCKET_MIN_X = POCKET.x - 200.0   -- tudo acima disto é interiores

local FLOOR_Y = 0.6      -- soalho da sala (a laje está a 0.12)
local INWARD = 6.0       -- quanto o herói entra para dentro ao chegar
local MATCH_R = 3.0      -- sanity: a saída tem de estar PERTO da parede -Z
local AUTO_EXIT_R = 1.6  -- encostar à porta POR DENTRO sai sem tecla
local ROOM_W = %(room_w).1f      -- passo X da grelha (= <InteriorScene room-size>)
local ROOM_H = %(room_h).1f      -- passo Z da grelha
local HEAL_EVERY = 2.0   -- de quanto em quanto tempo se re-tenta o registo

-- ── REGISTO derivado das entidades ──────────────────────────────────────
-- Construído 1× e cacheado em `viber.game().portal_registry` (sobrevive a
-- hot-reload e ao save). Cada portal VALIDA A PRÓPRIA entrada em cada frame
-- (`self_stale`) — mover/renomear uma porta ao vivo reconstrói o registo.
--   doors[room] = { {x=…, z=…}, … }         portas de rua (várias por sala OK)
--   rooms[room] = { ox=…, oz=…, exit_dz=… } saída na bolsa → célula da grelha
--
-- NOTA (snapshot com cap): o registo é construído nos primeiros frames, quando
-- os portais (spawnados antes da vegetação em massa) ainda cabem no snapshot
-- de nomes da engine; DEPOIS de construído, o on_update só precisa do NOME
-- próprio (`viber.entity_name()`, semeado fora do cap) e de posições em cache.
-- Se a minha sala não estiver no registo, `heal` re-tenta periodicamente.
local function build_registry()
  local ids = viber.find_all("portal.")
  if #ids == 0 then return nil end  -- entidades ainda não spawnaram: NÃO cachear
  local reg = { doors = {}, rooms = {} }
  for _, id in ipairs(ids) do
    local name = viber.entity_name(id)
    local ok, x, y, z = viber.entity_position(id)
    if name and ok then
      local exit_room = name:match("^portal%%.exit_([%%w_]+)$")
      if exit_room then
        -- Posição relativa à bolsa → célula da grelha (ox/oz, o CENTRO da
        -- sala) + o resta até à saída (exit_dz, ~1 m fora da parede -Z).
        local rx, rz = x - POCKET.x, z - POCKET.z
        local ox = math.floor(rx / ROOM_W + 0.5) * ROOM_W
        local oz = math.floor(rz / ROOM_H + 0.5) * ROOM_H
        local exit_dz = rz - oz
        -- Sanity (MATCH_R): a saída pertence à parede -Z da célula — um
        -- exit_dz perto de 0 (centro da sala) ou além do limite da célula é
        -- grelha↔XML desincronizadas, avisa em vez de adivinhar.
        if exit_dz > -MATCH_R or exit_dz < -ROOM_H * 0.5 then
          viber.log("building-portal: saída '" .. name .. "' fora da parede -Z da célula"
            .. " (exit_dz=" .. string.format("%%.1f", exit_dz) .. ") — grelha e XML dessincronizados?")
        end
        reg.rooms[exit_room] = { ox = ox, oz = oz, exit_dz = exit_dz }
      else
        local room = name:match("^portal%%.([%%w_]+)%%.[%%w_]+$")
          or name:match("^portal%%.([%%w_]+)$")
        if room then
          reg.doors[room] = reg.doors[room] or {}
          reg.doors[room][#reg.doors[room] + 1] = { x = x, z = z }
        end
        -- Nomes que não casam nenhum padrão são avisados pelo DONO (on_update).
      end
    end
  end
  return reg
end

-- A MINHA entrada no registo ainda bate com a minha posição? (O registo é um
-- cache; uma porta MOVIDA ao vivo no editor tinha entradas velhas — cada
-- entidade valida-se a si própria e dispara o rebuild geral.)
local function self_stale(reg, name, x, z, inside)
  if inside then
    local room = name and name:match("^portal%%.exit_([%%w_]+)$")
    local r = room and reg.rooms[room]
    if not r then return false end  -- sem entrada: o on_update trata (heal)
    local rx, rz = x - POCKET.x, z - POCKET.z
    local ox = math.floor(rx / ROOM_W + 0.5) * ROOM_W
    local oz = math.floor(rz / ROOM_H + 0.5) * ROOM_H
    return r.ox ~= ox or r.oz ~= oz or r.exit_dz ~= (rz - oz)
  end
  local room = name
    and (name:match("^portal%%.([%%w_]+)%%.[%%w_]+$") or name:match("^portal%%.([%%w_]+)$"))
  local doors = room and reg.doors[room]
  if not doors then return false end
  for _, d in ipairs(doors) do
    local dx, dz = d.x - x, d.z - z
    if dx * dx + dz * dz <= 0.25 then return false end  -- 0,5 m: cobre-me
  end
  return true  -- nome válido mas NENHUMA entrada me cobre → mudei
end

-- Re-tenta o registo de tanto em tanto (a minha sala não tem entrada): o
-- snapshot de nomes da engine tem cap e o registo pode ter sido construído
-- a meio; se os portais voltarem a estar visíveis ao `find_all`, a
-- reconstrução cura o registo sozinha.
local function heal(st, g, dt, msg)
  if not st.warned then
    st.warned = true
    viber.log("building-portal: " .. msg)
  end
  st.retry = (st.retry or 0.0) + dt
  if st.retry >= HEAL_EVERY then
    st.retry = 0.0
    g.portal_registry = nil
  end
end

function on_update(dt)
  local g = viber.game()
  if not g.portal_registry then
    g.portal_registry = build_registry()  -- nil se find_all vazio → retry
  end
  local reg = g.portal_registry

  local st = viber.state()
  local x, y, z = viber.position()
  local inside_pocket = x > POCKET_MIN_X
  local my_name = viber.entity_name()

  -- Cache fresco? Cada portal valida a própria entrada (ver self_stale).
  if reg and my_name and self_stale(reg, my_name, x, z, inside_pocket) then
    reg = build_registry()
    g.portal_registry = reg
  end
  if not reg then return end  -- entidades ainda a spawnar; retry no próximo frame

  if not st.ready then
    st.ready = true
    viber.set_interaction(inside_pocket and "Sair" or "Entrar", "e", 2.8)
  end

  -- SAÍDA AUTOMÁTICA (interiores): a porta de uma sala é um vão aberto sobre
  -- o vazio — encostar a ela tem de tirar o herói dali, não pedir uma tecla
  -- (pedido do utilizador 2026-09-13: "bloquear e imediatamente me fazer
  -- sair"). O piso duro da bolsa trata da queda; este gatilho trata da
  -- saída. Só vale de DENTRO: uma porta de rua continua a exigir [E], senão
  -- passar à frente de uma casa sugava o jogador.
  local auto = inside_pocket and viber.distance_to_player() <= AUTO_EXIT_R
  -- `interacted` não consome o evento duas vezes no mesmo frame entre
  -- entidades: cada portal decide pelo seu próprio cooldown.
  if not (auto or viber.interacted("e")) then
    st.cd = false
    return
  end
  if st.cd then return end

  if inside_pocket then
    -- Sou `portal.exit_<room>`: a sala vem do NOME (sem matching por raio).
    local room = my_name and my_name:match("^portal%%.exit_([%%w_]+)$")
    if not room then
      if not st.warned then
        st.warned = true
        viber.log("building-portal: '" .. tostring(my_name)
          .. "' não casa portal.exit_<room> — renomear a entidade no editor")
      end
      return
    end
    local doors = reg.doors[room]
    if not doors or #doors == 0 then
      heal(st, g, dt, "sala '" .. room .. "' sem porta de rua registada (portals.xml?)")
      return
    end
    -- Várias portas para a mesma sala (market.stall_a/b/c): sair pela porta
    -- por onde se ENTROU (g.last_door, tolerância 1 m — a porta pode ter
    -- sido movida um pouco); sem memória, a 1.ª registada. A distância ao
    -- jogador não discrimina (as portas ficam a ~2,6 km daqui).
    local door = doors[1]
    local want = g.last_door and g.last_door[room]
    if want then
      local bd = 1.0
      for _, d in ipairs(doors) do
        local dx, dz = d.x - want.x, d.z - want.z
        local dd = dx * dx + dz * dz
        if dd <= bd then bd, door = dd, d end
      end
    end
    st.cd = true
    local gy = viber.ground_below(door.x, 200.0, door.z)
    viber.sound("door_close")
    viber.teleport_player(door.x, (gy or 25.0) + 0.25, door.z)
    viber.toast("A porta devolve-te à vila.")
  else
    -- Sou `portal.<room>[.sufixo]`: a sala vem do NOME.
    local room = my_name
      and (my_name:match("^portal%%.([%%w_]+)%%.[%%w_]+$") or my_name:match("^portal%%.([%%w_]+)$"))
    if not room then
      if not st.warned then
        st.warned = true
        viber.log("building-portal: '" .. tostring(my_name)
          .. "' não casa portal.<room>[.sufixo] — renomear a entidade no editor")
      end
      return
    end
    local r = reg.rooms[room]
    if not r then
      heal(st, g, dt, "porta '" .. room .. "' sem sala na bolsa (portal.exit_"
        .. room .. " em falta no interiors.xml?)")
      return
    end
    st.cd = true
    -- Memória da porta usada: a MINHA posição (sou a porta de rua) — ao sair,
    -- o herói devolve a esta porta em vez da 1.ª da sala.
    g.last_door = g.last_door or {}
    g.last_door[room] = { x = x, z = z }
    viber.sound("door_open")
    viber.teleport_player(POCKET.x + r.ox, FLOOR_Y, POCKET.z + r.oz + r.exit_dz + INWARD)
    viber.toast("Entras no edifício.")
  end
end
"""


def portal_lua() -> str:
    """O script data-driven — só POCKET e a grelha vêm do gerador."""
    return PORTAL_LUA % {"px": POCKET[0], "pz": POCKET[1], "room_w": STEP_X, "room_h": STEP_Z}


if __name__ == "__main__":
    main()
