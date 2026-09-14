# Viber — "o ecrã pisca": passes de pós-processamento sem ordem declarada

**Data:** 2026-09-13 · **Sintoma:** imagem a piscar e a vibrar no `simple-rpg`,
pior ao anoitecer/entardecer, algumas vezes por segundo de dia. O utilizador
descreveu-o como "parecido com o que aconteceu quando tentámos colocar SSR".

## Causa

O bevy 0.19 regista o CAS (sharpening) **sem aresta nenhuma** para o resto da
cadeia (`bevy_anti_alias 0.19.1`, `contrast_adaptive_sharpening/mod.rs:122`):

```rust
cas.after(fxaa).after(smaa).in_set(Core3dSystems::PostProcess)
```

Nada o liga ao `tonemapping`, ao bloom, ao DOF, ao effect stack — nem ao nosso
passe de split-tone (`postfx.rs`, `SplitToneSettings::schedule_configs`), que
também vive pós-tonemap.

Nesta versão os passes do render graph são **sistemas normais** e o
`RenderContext` é `Deferred` + `Res` — não conflitua com nada. Dois passes sem
ordem declarada **correm em paralelo**. E `ViewTarget::post_process_write` é um
`main_texture.fetch_xor(1)` GLOBAL (`bevy_render/src/view/mod.rs:952`): dois XOR
concorrentes trocam source/destination um do outro, e os command buffers ainda
são submetidos por ordem indefinida. A imagem alterna entre frames — pisca.

## Fix

`postfx.rs`: o split-tone passa a declarar `.after(cas)` (além do
`.after(tonemapping)` que já tinha). A cadeia pós-tonemap fica determinística:

```
tonemapping → cas → split-tone
```

**Não dá para resolver pelo lado do upstream:** `configure_sets` sobre um system
type set panica no bevy 0.19 (`configuring system type sets is not allowed` —
crash no boot, testado). Só se pode pendurar o NOSSO sistema na cadeia.

## Dívida conhecida

O `auto_exposure` do bevy tem o mesmo defeito (declara apenas
`.before(tonemapping)`, sem relação com o bloom) e a função do sistema é
**privada** no upstream — não há como lhe dar a aresta a partir do Viber. Se
aparecer latejo de exposição (brilho global a respirar), é o primeiro sítio a
olhar.

## Como se chegou lá (protocolo que funcionou)

Ler código não chegou: as duas primeiras hipóteses (ordem do `aerial_pass`;
auto-exposure) foram descartadas por medição. O que resolveu foi **bissecção ao
vivo com gates de env**, um efeito de cada vez, com os olhos do utilizador a
decidir cada arranque.

Armadilhas de medição encontradas pelo caminho, para não se repetirem:

- **Screenshots pelo bridge saem a ~0,5 Hz** — não apanham um flash de 1–3
  frames. Mediram "estável" enquanto o utilizador via o problema.
- **O relógio anda a 1,2 min/s** no `simple-rpg`: duas capturas a segundos de
  distância têm iluminação diferente. A "alternância de 3% em duas famílias de
  brilho" que parecia flicker era o sol a mexer-se + drift da câmara.
  `viber.debug.set_time_scale(0)` + `set_clock(n)` congelam as duas coisas.
- `x11grab` não serve (Wayland: captura preto).

Mundo de teste: [`Viber/worlds/qa-flicker.xml`](../../Viber/worlds/qa-flicker.xml)
— 40 lanternas com `shadows="true"`, noite FIXA
(`minutes-per-real-second="0"`), arranca em segundos. O `simple-rpg` corre a
~30 fps com quedas a ~10 e não serve de banco de testes.

## Gates de A/B (novos, `postfx::fx_off`)

`VIBER_NO_<KEY>=1` tira um efeito e deixa o resto da lente intacta:

`AUTOEXPOSURE` · `BLOOM` · `DOF` · `SSAO` · `CONTACT_SHADOWS` · `AERIAL` ·
`SPLITTONE` · `LENS` (= `VIGNETTE` + `CHROMATIC` + `CAS`, ou cada um por si) ·
`MOTION_BLUR` · `TAA` · `VOLUMETRICS`. `VIBER_NO_POSTFX=1` continua a ser o
corte grosso.

Nota: `VIBER_NO_AUTOEXPOSURE=1` num mundo nocturno dá ecrã preto — é o medidor
que levanta a noite. Esperado, não é bug.
