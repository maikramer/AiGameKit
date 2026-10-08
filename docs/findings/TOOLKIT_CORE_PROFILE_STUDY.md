# Toolkit core — uso real nos exemplos e proposta de redução

**Data:** 2026-10-08 · **Estado:** estudo concluído — perfis e cortes **aguardam aprovação**.
Complementa [`MODEL_CONSOLIDATION_STUDY.md`](MODEL_CONSOLIDATION_STUDY.md) (modelos/venvs).

Objetivo: reduzir o toolkit ao que os jogos de exemplo realmente exercitam —
facilitar instalação/uso, poupar disco (modelos HF, pós-dedupe de venvs) e
reorganizar a apresentação (AGENTS/README). Método: varrimento de evidências nos
exemplos (`VibeGame/examples/*`, `Viber/examples/*`, manifests `game.yaml`,
scripts de regen, pool `shared-assets`) + mapa de wiring interno do GameAssets
(que sub-tool cada stage invoca) + `tools.yaml`/install.sh.

## 1. Matriz de uso (20 pacotes)

| Pacote | Usado nos exemplos | Evidência-chave |
|--------|-------------------|-----------------|
| Shared, Vramd, GameAssets | SIM (fundação/orquestração) | regen scripts importam `aigamekit_shared.vramd_client`; waves vramd |
| Text2D | SIM | `game.yaml:25-26`; ícones `--category icon` (sidecars `icons/*.json`); retratos NPC |
| Text3D | SIM | stages `3d/paint/lod/collision` ×116 rows nos manifests do pool |
| Paint3D | SIM | stage `paint` ×116; `repaint_rock_albedo.py` |
| Rigging3D | SIM | stage `rig` ×23 (`characters.yaml:31`) |
| Animator3D | SIM | `game.yaml:41-43` (`anim_pack: all`); `apply_quaternius_animations.sh`; venv bpy da vegetação |
| Texture2D + Materialize | SIM | `regen_textures.py` (texture2d via vramd + materialize PBR); `patch_pbr: true` |
| Skymap2D | SIM | `README.md:153`; `public/assets/sky/` |
| Text2Sound | SIM | `regen_sounds.py`, `gen-sfx.sh`, manifest `audio.yaml` (SA3) |
| AiGameKitLab | SIM | validate do batch (`pipeline.py:1533` — falha dura sem ele) + `precompute` sidecars |
| Terrain3D | SIM (VibeGame) | `index.html:71` "heightmap 100% Terrain3D" → `terrain.ahgt` (Viber nativo usa script próprio) |
| Rocks3D | SIM (parcial) | formações `form_*` do pool (`world/context.md:195`; stage 0 categoria rock) |
| VibeGame, Viber | SIM (engines) | os próprios exemplos + porta nativa |
| **Part3D** | **NÃO** | zero stages `parts` nos manifests; **nenhum wiring no GameAssets** (só `.gitignore` scratch) |
| **Motion3D** | **NÃO** | 0 refs nos exemplos; wave `run_motion3d_wave_or_fallback` existe **sem callers** |
| **Intrinsic** | **NÃO** | 0 refs; opção `materialize_intrinsic: False` (profile.py:133); pesos nem em disco |

Clips dos exemplos = **Animator3D (Quaternius/UAL)**, não Motion3D. Docs canónicos
(`MONOREPO_GAME_PIPELINE.md`, `ZERO_TO_GAME_AI.md`, README) listam exatamente as
tools usadas — Part3D/Motion3D/Intrinsic só aparecem no catálogo de pacotes.

## 2. Perfis de instalação propostos

`install.sh` hoje é flat (17 tools em `tools.yaml`, sem grupos; `--all` respeita
`install_order`). Proposta sem tocar no Clified upstream — um `case` no `install.sh`
expande o perfil em nomes de tools antes do bootstrap:

| Perfil | Tools | Para quê |
|--------|-------|----------|
| `core` (zero-a-jogo mínimo) | `vramd text2d text3d paint3d rigging3d animator3d gameassets materialize aigamekitlab vibegame` | DAG completo GLB animado → browser (docs `ZERO_TO_GAME_AI`) |
| `examples` (default sugerido) | core + `texture2d skymap2d text2sound terrain3d rocks3d viber` | tudo que os jogos de exemplo usam (céu, áudio, texturas, heightmap, rochas, track nativa) |
| `all` (actual) | + `part3d motion3d intrinsic` | catálogo completo |

Notas de dependências a respeitar: `text3d` cross-dep de `text2d` (já em
`tools.yaml:47-48`); `materialize` esperado pelo `patch-pbr`/PBR-enrich (soft);
`aigamekitlab` obrigatório para o validate do batch. Vramd dá erro **lazy e
acionável** se um backend apontar para tool não instalada (`subprocess_pool.py:615`:
"corre ./install.sh <tool>") — os backends `part3d/motion3d/intrinsic` podem ficar
no `backends.yaml` sem penalizar o arranque.

## 3. Disco recuperável com os 3 extras fora (~41,7 GB)

| Extra | venv | Modelos | Total |
|-------|------|---------|-------|
| Part3D | 1,7 GB | `Hunyuan3D-Part` 12 GB + space 0,1 GB | **≈13,8 GB** |
| Motion3D | 0,6 GB | Qwen3-8B 16 GB + CLIP 1,6 GB + hy-motion 5,7 GB | **≈24 GB** |
| Intrinsic | 3,9 GB | (pesos não instalados) | **≈3,9 GB** |

Caveats: Motion3D tem investimento recente (`apply-rigged`, findings MOTION3D) —
cortar implica re-download ~23 GB se o text-to-motion voltar ao roadmap (a wave do
batch está por ligar de qualquer forma). Part3D fica documentado em lições passadas
mas sem wiring. Intrinsic é licença académica, opção desligada por defeito.

## 4. Ajustes de consistência encontrados (fazer independentemente dos cortes)

1. **AGENTS.md diz "stages (3D, rig, parts, animate) são auto-detetados"** — `parts`
   não tem wiring no GameAssets (grep vazio); e os flags listados são só
   `--no-3d/--no-rig/--no-animate`. Corrigir a frase.
2. Wave `run_motion3d_wave_or_fallback` (`vramd_batch.py:1083`) definida sem callers
   — remover com o demote do Motion3D (ou ligar, se for roadmap).
3. `docs/INSTALLING.md:174-177` já tem 2 grupos de teste Docker (leves / cadeia
   GPU) — alinhar a secção com os perfis novos.

## 5. Plano de execução proposto

1. `install.sh` perfis `core|examples|all` (+ help/README/AGENTS com a tabela por
   perfil; `tools.yaml` intocado).
2. AGENTS/README: tabela de pacotes reordenada core → extras; corrigir "parts".
3. Limpar a wave morta do Motion3D no GameAssets (se demote aprovado).
4. Cortes de disco opcionais (aprovar caso a caso): venvs + modelos dos extras
   demoted (~41,7 GB); reexecutar `make dedupe-venvs` depois de qualquer reinstalação.
5. CI: sem mudanças (Part3D/Motion3D/Intrinsic já estão fora da matriz Python).
