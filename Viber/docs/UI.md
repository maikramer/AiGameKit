# UI declarativa do Viber (`src/ui`)

Interface de jogo escrita como **XML + folha de estilo + Luau**, sem Rust.
Substitui os construtores de nós que viviam em `src/hud/`: um mundo altera o
seu HUD e os seus menus editando ficheiros de conteúdo, e a engine deixa de ter
cores e raios de canto compilados lá dentro.

Três camadas, uma responsabilidade cada:

| Camada | Onde vive | Responsabilidade |
|--------|-----------|------------------|
| Estrutura | `<UiRoot>` no world XML | que elementos existem e como se aninham |
| Apresentação | `<UiStyle>` / ficheiro `.css` | tamanhos, cores, estados, layout, movimento |
| Dados e comportamento | `bind="…"` + script Luau | que valores aparecem e o que os botões fazem |

Exemplos completos no `examples/simple-rpg`:

* HUD de jogo — `world/hud.xml` · `ui/hud.css` · `scripts/ui/hud.lua`
* Menu — `world/menu.xml` · `ui/menu.css` · `scripts/ui/menu.lua`
* **Vitrina de TODAS as capacidades** — `ui-showcase.xml` · `ui/showcase.css` ·
  `scripts/ui/showcase.lua` (`viber run examples/simple-rpg/ui-showcase.xml`)

---

## 1. Elementos

| Tag | Rende como | Notas |
|-----|-----------|-------|
| `UiRoot` | camada absoluta de ecrã inteiro | uma por camada; aceita `script="…"` |
| `UiPanel` | caixa | o elemento genérico |
| `UiRow` / `UiColumn` | caixa com direção pré-definida | atalho de `direction:` |
| `UiGrid` | **grelha** | `cols="repeat(4, 1fr)"`, `rows="40 40"`, `flow="row dense"`; ver §2.4 |
| `UiText` | texto | conteúdo = texto do elemento ou `text="…"` |
| `UiIcon` | imagem | `src="/assets/…"` |
| `UiBar` | calha + preenchimento | `value="0..1"`; o filho ganha a classe `fill` |
| `UiCooldown` | slot + veladura que desce | `value` = fração **restante** |
| `UiButton` | caixa clicável | reportado a `viber.ui.clicked(id)` |
| `UiCheck` | **toggle** | `value="1"` começa ligado; classes `checked`/`unchecked`; ver §1.1 |
| `UiSlider` | **gama arrastável** | `min`/`max`/`step`, filhos `.fill` + `.handle` criados pela engine; ver §1.2 |
| `UiInput` | **campo de texto** | clique foca, Enter/Escape desfoca; ver §1.3 |
| `UiSpacer` | espaço flexível | `grow: 1` por omissão |
| `UiModal` | overlay com tecla de abrir | `key="q"` — letras, dígitos, `f1`–`f12`, pontuação (`` ` `` `,` `[`…) e navegação (`pageup`, `home`, `arrowleft`…); `escape-closes` |
| `UiList` | repetidor | ver §4 |

Atributos universais: `id`, `class`, `style` (declarações inline), `bind`,
`fade`, `hidden`, `disabled`, `tab-group` + `tab`, `scroll="y|x"`, `anim`
(movimento contínuo, §2.7) e `tooltip` (dica em hover, §1.4).

Num elemento com `bind`, `fade="in out [piso]"` transforma o aparecer/desaparecer
numa dissolução (segundos). Sem `fade`, o elemento liga/desliga a seco. O
**piso** (3.º número, opcional) é a opacidade mínima com o binding a falso:
`fade="0.25 1.8 0.7"` nos corações significa «derrete até 70% em exploração
calma, sobe a 100% em combate/dano, NUNCA some» — só o que é transitório (XP,
avisos) é que dissolve até zero.

### 1.1 `UiCheck` — toggle

```xml
<UiCheck id="mute" class="check" value="1">
  <UiPanel class="check-box"><UiPanel class="tick" /></UiPanel>
  <UiText class="check-label">Som</UiText>
</UiCheck>
```

* Um clique alterna o estado. O elemento recebe a classe `checked` **ou**
  `unchecked` — a folha de estilo decide o aspecto de cada um.
* O filho com a classe `tick` é mostrado/escondido automaticamente
  (`display`), pelo que o "quadradinho" do check é CSS puro.
* `value="1"` começa ligado. Estado inicial também define as classes no
  primeiro frame.
* Leitura e escrita por script: `viber.ui.read("mute").checked` e
  `viber.ui.set_checked("mute", true)`.

### 1.2 `UiSlider` — gama arrastável

```xml
<UiSlider id="volume" class="slider" min="0" max="100" step="5" value="70" />
```

* A engine cria dois filhos: `.fill` (cresce com o valor, como uma barra) e
  `.handle` (pousa na ponta do fill). `direction="vertical"` inverte o eixo.
* Arrastar funciona mesmo com o ponteiro fora da calha (o valor clampeia).
  Enquanto arrasta, o elemento leva a classe `dragging`.
* `step` quantisa o valor (`0` = contínuo). O `value` vive em `min..max`.
* Script: `viber.ui.read("volume").value` e `viber.ui.set_value("volume", 40)`
  (o valor é clampado ao intervalo do slider).

### 1.3 `UiInput` — campo de texto

```xml
<UiInput id="hero-name" class="input" placeholder="Escreve aqui…" max-length="16" />
```

* Clique foca (classe `focused`); Enter ou Escape desfocam. Enquanto focado,
  o campo consome os caracteres (Backspace apaga; ctrl/alt/meta continuam a
  funcionar como atalhos e não escrevem).
* Com o campo vazio mostra o `placeholder` e ganha a classe `placeholder`
  para a folha de estilo o apagar.
* O texto vive no elemento (não no filho): `viber.ui.set_text("hero-name", …)`
  escreve o valor, `viber.ui.read("hero-name").text` lê o que foi digitado.
  `focus("hero-name")` foca por script; `viber.ui.focused()` devolve o id do
  campo com o teclado (ou `nil`).
* Enquanto um campo está focado, as teclas SÃO texto: os modais não reagem
  (o `m` de "Menu" não abre menu) e o Escape desfoca antes de fechar qualquer
  coisa. Atalhos com ctrl/alt/meta continuam a funcionar.

### 1.4 `tooltip` e `anim` — em qualquer elemento

```xml
<UiPanel class="slot" tooltip="Espada curta — dano 8">…</UiPanel>
<UiPanel class="seal" anim="spin 6" />
```

* `tooltip="…"`: enquanto o elemento estiver sob o rato, a engine mostra UMA
  janela flutuante perto do ponteiro com esse texto. A janela tem a classe
  `tooltip` (pinte-a no CSS; sem regra, usa um escuro por omissão). Nunca
  rouba o hover ao elemento por baixo.
* `anim="tipo [período] [amplitude]"` — movimento contínuo, sem scripts:

| spec | efeito | omissões |
|------|--------|----------|
| `spin`   | rotação contínua              | 2 s/volta |
| `pulse`  | escala respira (±amplitude)   | período 1.2 s, amplitude 0.08 |
| `bob`    | flutua na vertical            | período 2 s, amplitude 6 px |
| `shake`  | tremor curto                  | período 0.4 s, amplitude 3 px |

  Exemplos: `anim="spin"`, `anim="bob 1.5 10"`, `anim="shake 0.3 5"`.
  A animação ganha ao `rotate`/`scale`/`translate` do CSS e corre mesmo com o
  elemento em `fade`. Em runtime: `viber.ui.set_anim(id, "pulse")` liga,
  `set_anim(id, "none")` desliga.

## 2. Folha de estilo

Dialeto CSS **plano**, feito para ser lido por quem nunca o viu — com
poderes ao nível do Tailwind: paleta completa por nome, sombras, grelhas e
decoração de texto.

```css
.panel            { background: slate-900ee; radius: 14; padding: 10 8;
                    box-shadow: 0 8 24 #00000099 }
.panel:hover      { background: slate-800ee }
.track.hp .fill   { background: rose-500 }
UiText            { color: stone-200; font-size: 14 }
#hp-bar           { width: 190 }
```

* **Seletores**: tag, `.classe`, `#id`, compostos (`.track.hp`), descendentes
  (`.panel .fill`), **filho directo** (`.panel > .fill`) e as pseudos
  `:hover` / `:active` / `:disabled` / `:focus` / `:checked` / `:empty` /
  `:first-child` / `:last-child` / `:nth-child(an+b|odd|even)` — e compostas
  (`.btn:hover:focus`), mais a NEGAÇÃO **`:not(.a)`** / `:not(#x)` /
  `:not(uibutton)` (um composto por argumento). Não há combinador de irmão
  (`+`/`~`) nem de atributo (`[...]`). `:focus` é o elemento com o teclado; `:checked` é um
  `<UiCheck>` ligado; `:empty` é um elemento sem filhos nem texto; os
  `*-child` leem a posição entre irmãos na ordem do DOM.
* **Cascata**: id 100 · classe (e pseudo) 10 · tag 1; empate resolve por ordem
  no ficheiro; `style=""` inline ganha sempre.
* **Valores**: número nu é **píxeis** (`width: 12`), `%` e `auto` também;
  `padding`/`margin`/`border-width`/`radius` seguem o atalho CSS de 1–4 valores.
  Longhands disponíveis: `margin-top/right/bottom/left`, `padding-*`,
  `border-width-*` (ou `border-top-width`…) — misturam com o shorthand.
  Texto: `line-height` (px ou múltiplo do font-size; herda) e
  `align-content` (linhas de um flex com `wrap`/grelha).
* Propriedades desconhecidas e regras malformadas são **avisadas e saltadas** —
  um erro de escrita numa cor não leva o HUD inteiro abaixo.

O ficheiro vem de `<UiStyle src="ui/hud.css" />` (resolvido a partir da pasta
do mundo) ou do texto do próprio elemento.

**Scoping por raiz (shadow-DOM-lite):** cada folha alimenta as `<UiRoot>`
declaradas DEPOIS dela até à próxima raiz — as regras de `ui/hud.css` nunca
tocam a raiz do menu, e uma classe comum às duas folhas (um `.empty` em cada)
deixa de vazar de uma para a outra. O tema partilhado (`ui/theme.css`) é
declarado em cada fragmento e por isso vale em ambos. Uma raiz sem folhas
próprias só recebe estilo de inline/presets.

### 2.0.1 Herança — o texto desce a árvore

Como no CSS, as propriedades que descrevem **TEXTO** herdam do ancestral mais
próximo que as declare — `color`, `font-size`, `font-weight`, `text-align`,
`line-break` e `text-shadow`. A cascata ganha sempre à herança: uma regra
própria do elemento override, o pai só preenche o que ficou em aberto.

```css
uiroot  { color: stone-100; font-size: 14 }   /* a voz base do mundo */
.card   { }                                    /* os textos dentro herdam */
.tag    { color: amber-300 }                   /* só o que muda */
```

Tamanhos de fonte RELATIVOS fecham contra a base herdada (16 px quando ninguém
declara nada, como no browser): `font-size: 120%` e `1.2em` = fração do PAI,
`0.9rem` = fração da fonte do `uiroot`. `em`/`rem` só existem em `font-size`.

### 2.0.2 Expressões — `calc()`, `min()`, `max()`, `clamp()`

As medidas escalares (`width`/`height`/`min-*`/`max-*`/`top`/`right`/`bottom`/
`left`/`gap`/`font-size`) aceitam funções de valor, avaliadas contra o espaço
autoral a cada resize:

```css
.dock  { width: clamp(280px, 60vw, 426px); }      /* nunca aperta nem estica */
.clock { bottom: calc(18px + 148px + 8px); }       /* acima do minimapa */
.hero  { font-size: clamp(14px, 2vmin, 22px); }
```

* Operadores `+ - * /` com precedência normal, parêntesis e negativos
  (`calc(-4px + 10px)`); funções aninham (`min(max(30px, 10px), 1vmin)`).
* Operandos: número nu = px (regra do dialecto), `px`, `vw`, `vh`, `vmin`,
  `vmax` e **`%`** — fecha contra a LARGURA do pai (frame anterior; como no
  CSS para `width`/`padding`/`margin`/`gap`; um `height` com `%` em `calc()`
  usa a largura — divergência documentada). Sem pai conhecido, `%` vale 0.
* `padding`/`margin`/`border`/`radius` não levam expressões — px/% e unidades
  de viewport chegam para espaçamentos responsivos.
* Divisão por zero ou valor ilegível para uma propriedade conhecida → a
  declaração é descartada COM warn (nunca em silêncio).

### 2.1 Cores — paleta Tailwind por nome

Qualquer propriedade de cor aceita:

* hex: `#rgb` `#rgba` `#rrggbb` `#rrggbbaa`;
* `rgba(r,g,b,a)` (a em 0–1) e **`hsl(h,s%,l%)` / `hsla(h,s%,l%,a)`**
  (separadores vírgula ou espaço: `hsl(120 50% 40%)`);
* `transparent`/`none` e **nomes do CSS** — `red`, `teal`, `gold`,
  `rebeccapurple`, `crimson`, `skyblue`… (~50 nomes comuns);
* **paleta Tailwind v3 completa por nome** — `slate-900`, `rose-400`,
  `amber`, `emerald-600`… (22 tons × 11 shades: slate, gray, zinc, neutral,
  stone, red, orange, amber, yellow, lime, green, emerald, teal, cyan, sky,
  blue, indigo, violet, purple, fuchsia, pink, rose);
* tom nu = shade 500 (`rose` ≡ `rose-500`);
* **modificador de opacidade** à Tailwind: `rose-500/25` = 25 % de alpha;
  valores fora de 0–100 clampeiam.

### 2.1.1 Gradientes — `linear-gradient` e `radial-gradient`

`background` (e `background-image`) aceitam gradientes, pintados POR CIMA da
cor de fundo (as duas coexistem: cor de reserva + gradiente):

```css
.barr     { background: linear-gradient(45deg, #000 0%, #fff 100%) }
.aviso    { background: linear-gradient(to right, red, amber-400 70%, transparent) }
.orbe     { background: radial-gradient(closest-side, white 0%, blue 100%) }
.plain    { background-image: none }        /* tira um gradiente herdado */
```

* Ângulo: `45deg`, número nu (graus) ou `to top|right|bottom|left` (e as
  diagonais `to top right`…). Omissão: `to bottom` (180°), como no CSS.
* Stops: `cor` ou `cor posição` (`red 30%`, `#fff 40px`); stops sem posição
  espaçam uniformemente. Mínimo de 2 stops.
* Radial: prefixo opcional `circle`/`ellipse`/`closest-side`/`closest-corner`/
  `farthest-side`/`farthest-corner` (omissão `farthest-corner`); centro fixo
  ao meio do elemento.
* `conic-gradient(...)` é aceite e lido como radial (a Bevy não pinta conic).

### 2.2 Profundidade — `outline` e `box-shadow`

```css
.card    { box-shadow: 0 8 24 #00000099, 0 2 6 #00000066 }  /* lista, por vírgulas */
.legend  { box-shadow: 0 0 12 amber-50066 }                  /* brilho */
.flat    { box-shadow: none }                                /* desliga herdada */
.chip    { outline: 1 #2b1d10aa }                            /* rim FORA da borda */
```

Ordem por sombra: `x y [blur] [spread] cor`. O `box-shadow` não mexe no
layout; várias sombras empilham de trás para a frente.

### 2.3 Texto — peso, quebra e decoração

```css
h1    { font-weight: bold; }        /* ou 100–1000; light/medium/semibold/black */
.dica { line-break: char; }         /* word (omissão) | char | word-char | none/nowrap */
.term { text-decoration: underline amber-400; }
.risc { strikethrough: 1; }         /* ou text-decoration: line-through */
.liso { underline: none; }          /* desliga explicitamente uma herdada */
```

`font-weight` só tem efeito visível em fontes variáveis. Novos:

```css
.grito  { text-transform: uppercase; }   /* uppercase | lowercase | capitalize */
.airy   { letter-spacing: 0.1em; }       /* px ou em/rem — herda como o texto */
```

`text-transform` é um espelho: o texto original fica guardado e o
transformado vai para o ecrã — `set_text`/XML continuam a mandar no conteúdo.
`letter-spacing` herda: uma `uiroot` com `letter-spacing: 1px` espaça tudo
lá dentro.

`text-overflow: ellipsis` corta o texto com `…` quando transborda a caixa —
`clip`/`visible` (omissão) não cortam. Duas regras: pede **`line-break: none`**
(texto de uma linha; o que quebra transborda na vertical e não é tocado) e
uma **largura limitada** (`width`/`max-width` — texto de largura automática
nunca transborda). O corte é medido pós-layout: muda o texto ou a janela e
estabiliza num frame ou dois; larguras maiores restauram o texto completo
sozinhas.

### 2.4 Grelhas — `display: grid`

```css
.inv {
  display: grid;
  grid-template-columns: repeat(4, 1fr) 24;   /* lista de pistas */
  grid-template-rows: 40 40;
  gap: 6;
}
.inv .wide { grid-column: 1 / span 3; }        /* colocação no FILHO */
.inv .fim  { grid-row: 2; }
```

* Pistas: `64` (px), `25%`, `2fr`, `auto`, `min-content`, `max-content` e
  `repeat(n, pista)`.
* Colocação do item: `auto`, `2`, `span 3`, `1 / 4`, `2 / span 3`.
* `grid-auto-flow`: `row` (omissão) | `column` | `row dense` | `column dense`.
* Atalho declarativo: `<UiGrid cols="repeat(5, 44)" rows="repeat(2, 44)"
  flow="row">` pré-ene o `display: grid` e as pistas como estilos inline
  (uma folha de estilo ainda pode sobrepor-se).

### 2.5 Pormenores de pintura

```css
.sobe  { translate: 0 -4; }        /* offset que NÃO ocupa layout */
.trás  { z-index: -1; }
.quiet { pointer-events: none; }   /* sem hover, sem cliques, sem tooltip */
.mão   { cursor: pointer; }        /* pointer | text | crosshair | grab | … */
```

`pointer-events: none` é completo: o elemento não acende `:hover`, não recebe
cliques nem mostra tooltip — o rato passa através.
Por omissão, raízes, textos, ícones e restantes elementos decorativos deixam
passar o foco: um texto dentro de um botão não intercepta o clique do botão,
e uma camada `UiRoot` não tapa os controles de outra. Botões, checks, sliders,
inputs, modais e contentores com `scroll` bloqueiam o que está por trás.
`pointer-events: auto` (ou remover `none`) restaura esse comportamento próprio
do elemento, em vez de transformar toda a decoração numa barreira.

Cursores aceites: `default`, `pointer`/`hand`, `text`, `crosshair`, `move`,
`wait`, `progress`, `help`, `not-allowed`, `grab`, `grabbing`, `cell`,
`col-resize`, `row-resize`, `ew-resize`, `ns-resize`, `all-scroll`.

### 2.6 Lista completa de propriedades

`display position direction wrap grow shrink align align-self justify width
height min-* max-* top right bottom left padding margin border border-width
margin-* padding-* border-width-* align-content line-height
border-color gap row-gap column-gap aspect overflow` ·
`grid-template-columns grid-template-rows grid-auto-flow grid-column grid-row` ·
`background background-image border-color border-top-color border-right-color
border-bottom-color border-left-color border radius outline box-shadow opacity
z rotate scale translate cursor pointer-events` ·
`color font-size font-weight text-align text-transform letter-spacing
text-overflow line-break text-decoration underline strikethrough text-shadow` ·
`tint fit` · motion: `transition animation`

`border-color` aceita 1–4 cores (atalho CSS `top right bottom left`) e os
longhands por lado combinam com o uniforme. `radius` aceita 1–4 valores
(2 = diagonais, 3 = `tl tr+bl br`).

### 2.7 Motion — `transition` e `@keyframes`

O mesmo motor serve as transições CSS, as animações nomeadas e o
`viber.ui.tween()`:

```css
.btn        { background: slate-800ee; transition: background .2s ease-out, opacity .3s }
.btn:hover  { background: slate-700ee }
.slot.danger { animation: danger-pulse 1.2s ease-in-out infinite alternate }
.toast.in    { transition: translate .25s ease-out; translate: 0 0 }

@keyframes danger-pulse {
    from { background: #7f1d1d; transform: scale(1) }
    50%  { border-color: rose-400 }
    to   { background: #b91c1c; scale: 1.06 }
}
```

* **`transition: <prop> <duração> [easing] [atraso] [, mais…]`** — trocas de
  classe/estado/media interpolem em vez de saltarem. Campos animáveis:
  `opacity background border-color color width height top right bottom left
  rotate scale font-size`. Easings: `linear ease ease-in ease-out ease-in-out
  cubic-bezier(x1,y1,x2,y2) steps(n)`. O estilo INICIAL de um elemento não
  transita (como no browser). Unidades mistas (`%` ↔ `px`) não transitam —
  salto seco.
* **`@keyframes nome { from|50%|to { declarações } }`** + **`animation:
  nome <duração> [easing] [atraso] [infinite|n] [alternate] [reverse]
  [forwards]`** — os frames são blocos de declarações; campos interpoláveis
  interpolam entre frames, os discretos trocam ao meio. `forwards` segura o
  último frame. Um bloco `@keyframes` vive na folha que o declarou (mesmo
  scoping das regras).
* Opacidade, cores e medidas animam **através do mesmo fan-out do re-estilo**:
  a opacidade de grupo e a herança continuam certas durante o tween. Custo: o
  elemento é re-estilizado por frame ENQUANTO anima (o mesmo de um hover;
  parado custa zero).
* O atributo `anim="…"` continua a funcionar; quando um `anim` e uma
  `animation` disputam o transform do MESMO elemento, o `anim` (que corre
  por último) vence — não mistures os dois no mesmo elemento.

### 2.8 Nota sobre `anim` vs CSS

`anim` vive no XML (ou em `set_anim`) e não é propriedade de estilo: quando
presente, sobrepõe-se ao `rotate`/`scale`/`translate` resolvidos pela cascata
nesse elemento. Todo o resto (cor, sombra, borda) continua a vir da folha.

## 3. Bindings

`bind="nome"` liga um elemento a um valor da engine, alimentado uma vez por
frame (`src/ui/bind.rs`, `src/ui/collect.rs`):

* **calhas**, **cooldowns** e restantes widgets com valor recebem a fração `0..1`;
* **textos** recebem a string formatada;
* qualquer outro elemento usa a verdade do valor para aparecer/desaparecer.

Nomes disponíveis: `health` `health.text` `health.value` `health.low` `xp`
`xp.text` `level` `level.text` `gold` `wood` `stone` `potion` `antidote` `bomb`
`cd.dash` `cd.heal` `cd.strike` `cd.dash.ready` `cd.heal.ready` `cd.strike.ready`
`potion.empty` `antidote.empty` `bomb.empty` `target` `target.name`
`target.alive` `clock` `day` `prompt.key` `prompt.label` `prompt.active`
`quest.title` `quest` `quest.text` `quest.active` `toast` `toast.active`
`combo` `combo.text` `purse.recent` `belt.recent` `xp.recent` `quest.recent`
`combat.active` `abilities.active` `vitals.active` `zone.name` `zone.active`
`status.venom` `weather.rain` `weather.wet` `talent.points` `talent.ready`.

Um nome desconhecido é avisado **uma vez** e o elemento fica intocado.

Além de mostrar/esconder, `bind="nome:classe"` troca uma CLASSE com a verdade
do binding (`health.low:danger` liga a classe `danger` no card de vitais com
HP ≤ 30 % — engine-driven, sem loop de Luau; `src/ui/bind.rs`).

`prompt.key` publica **E** para um `DialogueNPC` próximo (a tecla do diálogo);
interações de script mantêm a tecla que o script definiu.
A camada nativa de toasts só é retirada quando existe um texto declarativo
com `bind="toast"` (o `id` é livre). Sem esse consumidor, o fallback nativo
continua disponível; um contentor com `bind="toast.active"` não o substitui.

## 4. Listas (`<UiList>`)

Um menu é sobretudo listas cujo comprimento é dados, não layout.

```xml
<UiList class="rows" bind="quests" scroll="y">
  <UiTemplate>
    <UiRow id="quest-{id}" class="row {status}">
      <UiText class="row-title">{title}</UiText>
      <UiBar  class="row-bar" value="{progress}" />
    </UiRow>
  </UiTemplate>
  <UiEmpty><UiText class="empty">Nada por aqui.</UiText></UiEmpty>
</UiList>
```

`{campo}` é substituído em **todos os atributos e textos** do template;
`{index}` e `{n}` (1-based) estão sempre disponíveis; um campo inexistente
resolve a vazio. A lista só é reconstruída quando o conteúdo muda de facto —
uma mochila de 40 slots não custa nada enquanto ninguém apanha nada.

Fontes vêm de DOIS sítios:

1. **Engine** (`src/ui/menu_data.rs`): `quests` `bag` `skills` `shop`
   `controls` `system`;
2. **Scripts** — qualquer nome (INCLUINDO um da engine, que fica "possuído"
   pelo script e deixa de ser alimentado pelo colector automático):
   `viber.ui.list("bag-demo", { {name="Poção", count=12}, … })`
   (números e booleanos são convertidos em texto). `viber.ui.unlist(nome)`
   devolve a fonte à engine. Ver o `ui-showcase`.

## 5. Modais, abas e scroll

```xml
<UiModal id="menu" key="q" class="overlay">
  <UiRow class="tabbar">
    <UiButton class="tab" tab-group="menu" tab="quests"><UiText>Missões</UiText></UiButton>
  </UiRow>
  <UiColumn class="page" tab-group="menu" tab="quests" scroll="y"> … </UiColumn>
</UiModal>
```

* O botão activo ganha a classe `active` — a folha de estilo decide o aspeto.
* A página inactiva fica em `display: none` (fora do layout), não apenas
  invisível: caso contrário o painel dimensiona-se pela aba mais alta.
* `scroll="y"` liga a roda do rato; a procura sobe a hierarquia, por isso
  funciona com o cursor sobre uma linha e não só sobre o viewport.
* Navegação por teclado dentro do modal: dígitos **1–9** saltam para a aba,
  **`,` / `.`** percorrem-na.
* `UiModalsOpen` é espelhado em `menus::MenusOpen`, que é o que a hotbar, o
  movimento e a câmara consultam para saber se o input lhes pertence.

## 6. API Luau (`viber.ui.*`)

Instalada por `src/ui/script.rs` sobre o host de scripting existente.

**Escrita** (enfileirada; aplica depois de todos os scripts correrem):

| Chamada | Efeito |
|---------|--------|
| `set_text(id, texto)` | escreve um `UiText` — ou o VALOR de um `UiInput` |
| `set_value(id, v)` | valor de barra, cooldown **ou slider** (clampado ao intervalo) |
| `set_visible(id, bool)` / `set_disabled(id, bool)` | visibilidade / estado `:disabled` |
| `add_class(id, c)` / `remove_class(id, c)` / `toggle_class(id, c, on)` | estado visual |
| `set_style(id, "prop: valor; …")` | override inline (todo o dialecto do §2) |
| `set_style(id, "…", true)` | SUBSTITUI o inline inteiro (o merge nunca desfaz; isto sim) |
| `clear_style(id)` | remove o inline — a folha volta a mandar |
| `set_checked(id, bool)` | estado de um `UiCheck` |
| `set_anim(id, "pulse")` / `set_anim(id, "none")` | liga/desliga movimento em runtime |
| `focus(id)` | dá o teclado a um `UiInput` (desfoca o anterior) |
| `open(id, bool)` / `is_open(id)` | modais |
| `select_tab(grupo, aba)` / `tab(grupo)` | abas |
| `action(nome, arg)` | acção de jogo: `learn`, `buy`, `sell`, `save`, `load` |
| `create{ tag=…, id=…, parent=…, class=…, text=…, style=…, … }` | **cria um elemento** em runtime (mesmo construtor do XML; sem `id` gera `ui-gen-N`; devolve o id) |
| `destroy(id)` | remove o elemento e a sua subárvore (registry incluído) |
| `tween(id, {property=, to=, from=, duration=, easing=, delay=})` | interpola um campo animável; termina com um evento `tween_done` |
| `set(nome, valor)` | **bind de script**: alimenta `bind="nome"`, `bind="nome:classe"`, `get`/`number` (nomes da engine têm prioridade) |

**Leitura** (snapshot do INÍCIO da frame; uma escrita e a sua verificação têm
de ser chamadas separadas):

| Chamada | Devolve |
|---------|---------|
| `read(id)` | `{text, value, visible, checked, disabled, rect={x,y,w,h}}` ou `nil` — QUALQUER elemento com id (inputs reportam o texto digitado, sliders o valor) |
| `rect(id)` | `{x, y, w, h}` pós-layout no espaço autoral (píxeis do CSS), ou `nil` |
| `query(seletor)` | ids que casam um seletor do §2 (tag/classe/id/descendente/`>`/pseudos) |
| `classes(id)` / `children(id)` / `parent(id)` | estrutura do elemento |
| `exists(id)` | `true` se o id é endereçável agora |
| `get(nome)` / `number(nome)` | ler um binding (texto / fração) — engine primeiro, depois os do `set()` |
| `clicked(id)` | verdadeiro no frame do clique |
| `events([prefixo])` | **drena** a fila de eventos do frame — ver abaixo |
| `focused()` | id do `UiInput` com o teclado, ou `nil` |
| `list_count(nome)` | nº de linhas numa fonte de lista |
| `rows(nome)` | cópia das linhas — `{{campo=…}, …}` |

**Listas por script:**

```lua
viber.ui.list("bag-demo", {
  { name = "Poção",  count = 12 },
  { name = "Flechas", count = 40 },
})
```

Cria/repõe a fonte `bag-demo`; um `<UiList bind="bag-demo">` reconstrói-se
sozinho. Números/booleanos stringify-se. Repor a MESMA lista não custa nada
(detecção de mudança por conteúdo).

**Eventos** — a engine acumula o que acontece aos elementos e
`viber.ui.events()` devolve o lote (e esvazia a fila; consumidor único):

```lua
for _, ev in ipairs(viber.ui.events("shop-")) do
  if ev.type == "click" then
    comprar(ev.id)
  elseif ev.type == "value_changed" then
    ajustar_volume(ev.id, ev.value)
  elseif ev.type == "text_changed" then
    filtrar(ev.text)
  elseif ev.type == "checked_changed" then
    ligar(ev.id, ev.checked)
  elseif ev.type == "focus_changed" then
    teclado(ev.id, ev.focused)
  elseif ev.type == "tab_changed" then
    abrir_aba(ev.group, ev.tab)
  elseif ev.type == "tween_done" then
    viber.log("tween " .. ev.property .. " acabou em " .. ev.id)
  end
end
```

Isto substitui o padrão frágil do `clicked()` (verdade num ÚNICO frame — um
polling mais lento perdia cliques) e a detecção de mudança manual
(`ultimo_valor ~= viber.ui.number(...)` em cada frame). Barras/cooldowns não
emitem `value_changed` — quem quiser segui-las lê `number()` como sempre.
Com prefixo, só o que casa SAI da fila — o resto fica para outro script
cujos prefixos não colidam; sem prefixo, drena tudo. Há também
`hover_enter`/`hover_leave` (o ponteiro entrou/saiu) e `read(id).hovered`
para quem prefere perguntar.

**Criação dinâmica** — o mesmo construtor do XML, acionado de Luau:

```lua
local id = viber.ui.create{
  tag = "uibutton", parent = "hud", class = "toast in",
  text = "Quest concluída!", style = "background: emerald-700ee",
  tooltip = "Clica para fechar",
}
viber.ui.tween(id, {property = "opacity", from = 0, to = 1, duration = 0.25})
-- … e mais tarde:
viber.ui.destroy(id)

-- Subárvore NUMA chamada (a chave `children` é recursiva; ids em falta
-- geram `ui-gen-N` e cada nível fica endereçável):
local painel = viber.ui.create{
  tag = "uipanel", parent = "hud", class = "card",
  children = {
    { tag = "uitext", class = "titulo", text = "Diário" },
    { tag = "uibutton", class = "fechar", text = "×" },
  },
}
```

Os atributos da tabela são os mesmos do XML (`tag` obrigatório; `parent` é
o id do elemento pai; o resto — `class`, `style`, `text`, `value`, `bind`,
`tooltip`, `src`, `min`/`max`/`step`, `placeholder`… — passa como attr). O
elemento entra no fim dos irmãos e APARECE no frame seguinte (comandos em
fila), mas é endereçável por id logo na chamada seguinte. Criados sobrevivem
até ao rebuild da UI (recarregar o mundo).

**Binds por script** — o estado do script pode pilotar class-binds sem loops:

```lua
viber.ui.set("radar.mode", "combate")     -- <UiText bind="radar.mode"/>
viber.ui.set("radar.alerta", true)        -- <UiPanel bind="radar.alerta:alerta"/>
```

O nome tem de NÃO colidir com um bind da engine (`health`, `quest.title`…):
colisão = warn e recusa. `get`/`number` leem a engine primeiro, os binds de
script depois.

O script de uma `<UiRoot>` corre sempre: a UI não é um NPC, por isso está
isenta do LOD de raio de activação que congela scripts distantes.

## 7. Responsividade — qualquer tamanho, qualquer proporção

Três camadas que se combinam (o `ui-showcase` usa as três):

1. **Escala global** — automática: o HUD inteiro cresce sub-linearmente com a
   altura da janela (`src/ui/scale.rs`, 720p = referência). A largura limita
   este crescimento em janelas estreitas/retrato (`hud_scale_for_window`),
   para uma janela alta não ampliar painéis que já têm pouco espaço lateral.
2. **UM espaço de píxeis** — como num browser, o CSS só tem um metro: o
   **espaço autoral**, a janela dividida pela escala do HUD
   (`scale::ui_viewport`). Píxeis autorais (`24`), unidades de viewport
   (`30vw`, `4vh`, `2.4vmin`) e media queries (`max-width: 900`) vivem TODOS
   nele e falam do mesmo tamanho. Duas consequências práticas:
   * `100vw` ocupa a largura da janela no ecrã (o valor autoral resultante é
     ampliado de volta pela escala) e `width: 426` é o mesmo "426" que um
     `@media (max-width: 900)` compara — num retrato 1000×1600 a escala
     limitada pela largura deixa o espaço autoral em ~720 px e a faixa
     estreita avalia, sem duplicar breakpoints;
   * ⚠ **breaking (2026-09-05):** antes as media queries comparavam a janela
     física; mundos com breakpoints px podem ter de os rever. Breakpoints por
     rácio (`max-aspect`, `portrait`) são invariantes — não mudam.
3. **Media queries** — blocos da folha de estilo que só valem quando a janela
   (no espaço autoral) cumpre uma condição. A cascata reavalia a cada resize
   (o re-estilo é acionado por `WindowResized`):

```css
.readout { right: 3vw; top: 6vh; width: 26vw; }

/* Em retrato/janela estreita o cartão muda de canto e alarga. */
@media (max-aspect: 0.95) {
  .readout {
    right: auto;         /* `auto` desfaz o valor do lado de fora */
    top: auto;
    left: 3vw;
    bottom: 3vh;
    width: 60vw;
  }
}
```

   Predicados (ligados por `and`): `min-width`/`max-width`,
   `min-height`/`max-height` (píxeis do espaço autoral; `"900"` e `"900px"`
   são iguais), `min-aspect`/`max-aspect` (largura ÷ altura), `portrait`
   (proporção < 1) e `landscape` (≥ 1). Blocos **aninham-se** — as condições
   empilham. Uma condição falhada remove as regras do bloco DA CASCATA por
   inteiro: é o mesmo mecanismo do Tailwind (`sm:`/`md:`/`lg:`), em sintaxe
   de CSS.

Receita para um HUD que não rebenta: âncoras em `vw`/`vh`, tamanhos de texto
em `vmin`, contentores com `min-*`/`max-*` + `overflow: clip`, grelhas com
`1fr`, e `@media` só para TROCAR elementos de sítio — nunca para redefinir
tudo.

## 8. Armadilhas conhecidas

* **`Visibility::Visible` num filho ignora um pai `Hidden`** na Bevy. Para
  esconder um ramo inteiro usa-se `Inherited` nos filhos e `Hidden` na raiz —
  foi este o bug que deixava a lista de controlos do menu permanentemente por
  cima do mundo.
* `BorderRadius` é **campo de `Node`** na Bevy 0.19, não um componente;
  `UiTransform.translation` é um `Val2` (a animação fala píxeis, o CSS
  converte).
* `--` é proibido dentro de comentários XML; os separadores dos ficheiros de
  mundo usam `=`.
* Uma classe mudada num **ascendente** tem de re-estilizar os descendentes
  (`.track.danger .fill`): `propagate_style_dirty` trata disso.
* `repeat(4, 1fr)` tem um espaço dentro do parêntesis — o tokenizer das
  pistas de grelha respeita parêntesis exactamente por isso.
* Enquanto um `UiInput` está focado, as teclas de modal são ignoradas (o
  campo é que as come) — por isso é que "Menu" num campo não abre o menu.
