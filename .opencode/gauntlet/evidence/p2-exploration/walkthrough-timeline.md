# P2 — Linha do tempo do run de validação (labels completos)

Engine: `cargo run -p viber -- run examples/simple-rpg/world.xml --bridge`
(pid por boot; porta 15702). Comandos `viber debug …` a partir de `Viber/`.

```
BOOT 1 (world.xml, bridge 15702)
├─ [07:27] spawn OK, toasts de vila, adaptive-quality a acomodar
│
├─ PASSO 1 · TRANCAS ANTES DA ATALAIA
│  ├─ debug lua 'viber.biome_revealed("dark_forest")' → false
│  ├─ tp(-91,172) [junto ao forest-outpost-tower]
│  ├─ [F] → [toast] "A terra aqui ainda não foi cartografada — procura a
│  │        atalaia de Floresta Sombria."            ← [F] trancado c/ guia
│  └─ minimapa/compasso: SEM blips de marcos do bioma; só atalaisas (04)
│
├─ PASSO 2 · SUBIDA REAL À ATALAIA DO BOSQUE (30,138)
│  ├─ tp(17.5,141.2) → pé da rampa A
│  ├─ face + W 5.5 s → deck (y 49.9 → 55.2)     ← rampa A caminhável
│  ├─ face sul + W 0.9 s → linha da rampa B
│  ├─ face oeste + W 6 s → TOPO (y 61.15 = plataforma 61.13)
│  └─ screenshot 05-atalaia-topo-ativada-toast.png (vantage: vale inteiro)
│
├─ PASSO 3 · ATIVAÇÃO [E] "Sondar a terra"
│  ├─ [toast] "A Atalaia do Bosque acorda: a Nota do bioma entra na tua
│  │        bússola! (+40 XP)"
│  ├─ [log viber::nota] bioma 'dark_forest' revelado por atalaia
│  ├─ biome_revealed("dark_forest") → true
│  ├─ re-[E] → prompt "A ATALAIA JÁ FALOU" (idempotente)
│  └─ minimapa/compasso: 3 marcos da floresta ACENDEM (06)
│
├─ PASSO 4 · [F] ABERTO PÓS-REVELAÇÃO
│  ├─ tp(28,190) [poço da encruzilhada]
│  └─ [F] → "Medido e assinado: Poço da Encruzilhada (faltam 2 em Floresta
│           Sombria)."                                          ← quest survey intacta
│
├─ PASSO 5 · [G] VIAGEM RÁPIDA (fogueira da praça, tp(0,-6))
│  ├─ [G] → painel "VIAJAR": lista "> Poço da Encruzilhada" (assinado e
│  │        disponível); nenhum marco de bioma escuro           (07)
│  └─ [G] fecha
│
BOOT 2 (mesma engine, reinícios de conteúdo durante o desenvolvimento)
├─ PASSO 6 · KOROK rock — korok-bosque-pedra-1 (−34,282)
│  └─ [E] → "A pedra ergue-se: uma semente de canção! (1 na bolsa)"
├─ PASSO 7 · KOROK ring — anel do bosque (−70,200): 3× [E]
│  ├─ "Uma nota se acende no ar (2/3)..."
│  └─ "O anel ressoa inteiro: semente de canção!"  → 2 sementes no vault
├─ PASSO 8 · KOROK hide — korok-bosque-hide-1 (−110,150)
│  └─ [E] → "Escondida onde ninguém olha: uma semente de canção!"
├─ PASSO 9 · KOROK odd — korok-bosque-odd-1 (118,228)
│  └─ [E] → "A pedra que canta guarda uma semente!"   (4 modos validados)
│
BOOT 3 (com fix do snapshot do vault + ranges 3.0)
├─ PASSO 10 · BAÚS ESCONDIDOS (4 abertos ao vivo)
│  ├─ bau-ermo-ruinas (304,128) → "+50 XP, +95 ouro, +2 bombas"
│  ├─ bau-oasis (137,86, EM ÁGUA) → "+110 ouro, +1 poção"
│  ├─ bau-ponte-norte (8,202) → "+75 ouro, +1 poção"        (12)
│  └─ bau-atalaia-bosque (16,140, TOPO da atalaia) → "+90 ouro,
│     +2 antídotos"                                          (13)
│
├─ PASSO 11 · PERSISTÊNCIA save → load
│  ├─ ativação da atalaia do bosque ([E] no topo) → revealed=true
│  ├─ korok pedra-2 (44,210) → seeds=1
│  ├─ viber.save() → "Jogo gravado."
│  ├─ viber.load_save() → "Jogo carregado."
│  └─ pós-load: revealed("dark_forest")=TRUE · seeds=1 · desert=false
│     └─ [F] no poço continua aberto ("Medido e assinado...")
│
├─ PASSO 12 · PRÉMIO DOS 5 (recompensa incremental)
│  ├─ debug.give("korok-seed",4) + korok-vale-hide-1 [E]
│  └─ [toast] "...semente 5/20: +1 PONTO DE TALENTO!"   ← SkillTree.points+1
│
├─ PASSO 13 · PERF A/B interleaved (mesmo processo)
│  ├─ A (com conteúdo): 48.18 / 48.10 / 48.20 ms  (prof --samples 10)
│  ├─ despawn ao vivo dos 5 grupos tower.* via bridge
│  └─ B (sem atalaisas): 48.73 / 48.72 / 51.02 ms  → delta ≤ ruído (gate 5%)
│
BOOT 4 (passada de screenshots — 19 ficheiros em evidence/screenshots)
├─ 01 atalaia do vale a dominar a estrada leste da vila
├─ 02/03 atalaia do bosque de longe + chegada
├─ 04 minimapa ANTES da revelação (só atalaisas) · 06 DEPOIS (marcos acesos)
├─ 05 topo da atalaia + toast de ativação
├─ 07 painel [G] com os marcos revelados
├─ 08/09/10/11 koroks: fumo da pedra, anel de cristais, pedra que canta,
│   escondida atrás do relevo
├─ 12/13/14/15/16/17 baús: ponte, topo de atalaia, ruínas, oásis, cidadela,
│   boca do Ermo
└─ 18/19 atalaisas dos picos e do ermo dominando o horizonte dos caminhos
```

Estado final do gates: `cargo test -p viber` 1671 ✓ · `clippy -D warnings` ✓ ·
`analyze world.xml` OK ✓ · perf dentro do ruído ✓.
