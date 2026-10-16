# P2 — Mapa de colocação (torres · koroks · baús) + princípio aplicado

Mundo 4000×4000 centrado na origem. Biomas (`world/environment.xml`):
dark-forest = norte (z>56) · desert = este (x>56) · swamp = oeste (x<−56) ·
frozen-peaks = sul (z<−56; corredor central até −240 é o vale da vila).
Alturas amostradas de `world.data/terrain.ahgt` (viber terrain sample).

## Torres (o "maior triângulo" de cada bioma)

| Torre | Pos (x, z) | h terreno | Altura topo | Princípio (tese §5.1) |
|-------|-----------|-----------|-------------|------------------------|
| atalaia-do-vale | (90, −10) | 40.9 | +13.6 | Colina a leste da vila, FORA da exclusão de spawn (r52) mas à vista do portão leste: o jogador vê-a no primeiro minuto (gravity: landmark visível do hub). Silhueta única no horizonte do vale (screenshot 01). |
| atalaia-do-bosque | (30, 138) | 49.9 | +13.6 | Sanja entre o portão norte e os 3 marcos da floresta — estrutura o percurso da survey (gravity entre marcos). Rampa a poente, virada à estrada (screenshot 02). |
| atalaia-do-ermo | (130, 60) | 49.0 | +13.6 | CRSesta a entrada do deserto junto ao oásis (140,92), à vista da estrada leste (screenshot 19): vê-se ANTES do bioma — promete a revelação. |
| atalaia-do-pantano | (−130, 10) | 29.4 | +13.6 | Margem norte da lagoa (−190,−16), em frente à ponte de juncos: domina a planície aberta do pântano (bioma baixo — torre alta lê-se a 300+ m). |
| atalaia-dos-picos | (−40, −140) | 50.0 | +13.6 | Pé da subida, no corredor sul antes dos mojões: última "água" antes do bioma difícil (screenshot 18 — farol visível na rampa de acesso). |

Detalhe de geometria (todas iguais, cores por bioma): zigue-zague de duas
rampas (17.5°/15.3°, ponta da 1.ª ENTERRADA no solo — sem degraus) → deck de
viragem → plataforma oeste a +11.2 m com teodolito, cristal emissivo e farol.
Base com `<SpawnExclusion>` r18 (nada nasce dentro da estrutura).

## Koroks / Sementes de canção (22; fumo = indicador)

Floresta Sombria (6): pedra (−34,282) atrás da choupana da bruxa · anel
(−70,200) na clareira entre outpost e poço · pedra-que-canta (118,228) junto ao
círculo de menires · escondida (−110,150) no verso da colina do posto · pedra
(44,210) à beira do rio · escondida (140,260) no alto do chapadão (sem fumo —
recompensa a subida).

Ermo Rubro (5): pedra (128,84) na margem do oásis · pedra-que-canta (296,118)
no meio das ruínas · escondida (232,−20) atrás do relevo da caravana · pedra
(250,30) junto ao ninho de escorpiões · escondida (166,−108) atrás da cisterna.

Pântano (5): pedra (−150,−30) na margem da lagoa · pedra-que-canta (−244,24)
entre as covas submersas · escondida (−272,−34) atrás da palafita · anel
(−250,82) no acesso ao altar de ossos · escondida (−210,−40) no banco sul da
lagoa.

Picos (4): pedra (−30,−148) ao pé da subida · pedra-que-canta (−56,−184) na
crista do posto goblin · anel (−70,−206) à boca da mina do veio · escondida
(84,−196) na margem distante do lago gelado.

Vale (2): escondida (100,−24) atrás da colina da atalaia do vale · pedra
(18,78) nos campos norte.

Princípio: **density por bioma** ~1 semente por 2-3 min de exploração, todas
com micro-puzzle (erguer/cantar/anel) ou descoberta escondida — nenhuma é
"andar até". Fumo subindo = "há segredo aqui" (tese §5.1.2, item 3).

## Baús escondidos (11, fora do caminho principal)

| Baú | Pos | Esconderijo |
|-----|-----|-------------|
| bau-atalaia-vale | (75.9, 52.2, −7.9) | no TOPO da atalaia do vale — recompensa a subida |
| bau-atalaia-bosque | (16.0, 61.1, 140.3) | no TOPO da atalaia do bosque |
| bau-oasis | (137, 47.5, 86) | FUNDO DE ÁGUA rasa do oásis (~1.3 m submerso) |
| bau-ermo-ruinas | (304, 47.4, 128) | no interior das ruínas do ermo |
| bau-pantano-cais | (−266, 29.8, −12) | atrás do cais da palafita do pântano |
| bau-ponte-norte | (8, 38.9, 202) | à sombra do tabuleiro da ponte norte |
| bau-cidadela | (−870, 188.6, −290) | na Cidadela Caída (ponto mais remoto do SO) |
| bau-mina-veio | (−63, 95.8, −213) | à boca da gruta mina-do-veio |
| bau-bosque-encantado | (200, 70.1, 1400) | no fundo norte do bosque encantado |
| bau-vael | (700, 182.6, 210) | na mesa do Alto de Vael |
| bau-porta-ermo | (505, 81.5, −57) | escondido na boca da gruta porta-do-ermo |

Princípio: **rectangles/triângulos escondem** (tese §5.1.1) — nenhum baú no
eixo das estradas; todos exigem um desvio curto com recompensa acima do baú
médio dos POIs existentes (75-200 ouro vs 25-40 dos antigos).

## Gravity — verificação de "caminho entre marcos"

Cadeia de interesse no eixo vila→floresta (exemplo real do mundo):
portão norte → **atalaia do bosque** (landmark, 30,138) → korok pedra (44,210)
+ poço da encruzilhada (28,192) → círculo de menires + korok (118,228) →
choupana da bruxa + korok (−34,282). Cada perna tem 1-2 "distrações"
visíveis (torre/fumo) e 1 escondida — o ciclo de descoberta da tese §5.1.2.
