# O Silêncio de Vael — bíblia narrativa do simple-rpg

> Capítulo I · A Nota. O fio que liga as 27 missões, os quatro chefes, os marcos da
> Nota e as cinemáticas. Quem escreve conteúdo novo (falas, missões, cenas) lê isto
> primeiro: nomes, tom e cronologia vivem aqui.

## O mundo

**O Vale** é uma bacia verde rodeada por quatro terras que já foram mansas. No meio
fica **Discórdia** — vila de muralha baixa, praça de pedra, forja, capela e um
**salão comunal** onde o conselho se senta desde antes de haver mapas. O nome é
velho e irónico: foi aqui que as quatro terras assinaram a paz.

No extremo nordeste, acima do Ermo, ergue-se o **Alto de Vael**. Lá em cima
crescem veios de cristal azul que *cantam* quando o vento passa — o **Coro**.
Enquanto o Coro cantou, a bruma ficou nos pântanos, os bichos nos covis e as
estradas abertas. A **Cidadela de Vael**, a sudoeste, guardava o caminho do Coro
e as suas cantoras.

## O que aconteceu (há trinta invernos)

A Cidadela caiu numa só noite. Uma coluna subiu do Vale para a defender e **nunca
chegou** — o que resta dela é o **Campo de Batalha**, onde hoje andam **sombras com
os rostos dos soldados**. Quando a última cantora morreu, o canto do Coro
**partiu-se em quatro Notas**. Cada Nota foi engolida por uma criatura, e cada
criatura cresceu à medida do pedaço de canto que guardava:

| Nota | Guardião | Terra | Sinal da ferida |
|------|----------|-------|-----------------|
| Nota do Bosque | **A Bruxa do Bosque** (canta numa clareira onde nem os lobos entram) | Floresta Sombria (norte) | sombras saem dos pinhais ao anoitecer |
| Nota do Ermo | **O Verme do Ermo** (a areia treme à volta dele) | Ermo Rubro (este) | as caravanas desaparecem |
| Nota do Lodo | **O Guardião do Lodo** — "o que conta os afogados" | Pântano da Bruma (oeste) | o lago leva barcos, redes e gente |
| Nota dos Picos | **O Ogro dos Picos** (só desce quando a serra fica em silêncio) | Picos Gelados (sul) | a estrada do sul fechou |

Desde então o Coro está calado. É o **Silêncio de Vael**.

## Quem és

**O Cartógrafo.** Chegas a Discórdia com uma caixa de mapas e um contrato do
conselho: medir a terra outra vez. A ferramenta é **a Nota** — o caderno onde se
*assinam* os marcos (o sistema de viagem: [F] assina, [G] viaja). O **Ancião
Edmar**, guardião do salão comunal e o último que ouviu o Coro cantar, percebe
logo o que o conselho não disse: quem mede a terra pode encontrar as quatro Notas.

## Elenco

- **Ancião Edmar** — salão comunal (Discórdia). Narrador do prólogo e do epílogo;
  dá a missão-mãe **As Quatro Notas**. Fala devagar, em imagens. PT-PT, "tu".
- **Bram, o ferreiro** — forja. Prático, sardónico ("ferro bom não se negocia
  barato").
- **Irmã Vessa, a vigia** — orla da floresta; conhece a Bruxa.
- **Kadi, guia de caravanas** — perdeu três caravanas para o Verme.
- **Onno, pescador do lago** — o lago levou-lhe o irmão.
- **Batedora Sig** — guarda a estrada do sul; sabe acordar o Ogro.
- **Veterano do Campo** — sobrevivente da coluna; narra a cena do Campo de Batalha.
- **Mestre da Romaria** — guia a romaria ao Alto de Vael.
- **Mira, a Batedora** · **Capataz do Posto** · os cartógrafos de cada terra
  (Casla, Vela, A Que Conta os Barcos, O Homem dos Mojões).

## Arco do Capítulo I

1. **Prólogo** (cinemática `prologo`, na primeira entrada no mundo): o Vale, o
   Coro calado, a Cidadela caída; o Ancião chama o Cartógrafo ao salão comunal.
2. **As Quatro Notas** (missão `story_notas`, Ancião Edmar): recuperar as quatro
   Notas (item `nota`, 0/4 no diário). Cada terra tem o seu guardião, o seu
   dador de missão e os seus marcos da Nota.
3. **O guardião desperta** (cinemáticas `guardiao-*`, a primeira vez que cada
   chefe vê o herói): nome, terra e o conselho de quem conhece o bicho — a cena
   ENSINA a mecânica do especial.
4. **Nota recuperada** (cinemática `nota`, ao derrotar um guardião): o canto volta
   um pouco; o Ancião sente-o do Vale.
5. **O Campo de Batalha** (cinemática `campo-de-batalha`, ao chegar ao campo): o
   Veterano conta a noite da Cidadela — a razão das sombras.
6. **Epílogo** (cinemática `epilogo`, ao entregar as quatro Notas): o Coro canta
   outra vez. *Fim do Capítulo I* — o mundo continua aberto.

## Tom e regras de escrita

- Português europeu, "tu", frases curtas. Nada de "você" nas falas novas.
- O mundo é bonito e cansado, não sombrio: há humor nos vilões menores (bandidos,
  goblins) e luto nos maiores.
- Cada cena ensina ou recompensa: a dos guardiões dá a dica de combate; a das
  Notas dá progresso visível (n/4).
- Legendas ≤ 2 linhas, ~4 s cada. Títulos curtos.

## Onde vive cada peça

| Peça | Ficheiro |
|------|----------|
| Cinemáticas (XML) | `world/cinematics.xml` — `viber cinematic list/show/play/shot` |
| Controlador da história (Notas, prólogo, epílogo) | `scripts/game/story.lua` |
| Gatilhos de área ("só uma vez") | `scripts/cinematic-trigger.lua` (`prop-cinematic`, `prop-radius`) |
| Cena de despertar dos chefes | `lib/fsm.lua` (`intro = "guardiao-…"`) |
| Missão-mãe | `quests/story_quests.json` |
