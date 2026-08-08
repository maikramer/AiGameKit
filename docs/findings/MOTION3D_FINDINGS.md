# Motion3D — findings (HY-Motion → HML22 → SkinTokens)

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Package: [`Motion3D/README.md`](../../Motion3D/README.md) · [`Motion3D/AGENTS.md`](../../Motion3D/AGENTS.md).  
Retarget compartilhado: [`ANIMATOR_RETARGET_FINDINGS.md`](ANIMATOR_RETARGET_FINDINGS.md).  
Perfil: `Animator3D/src/animator3d/data/retarget/hml22.yaml`.

---

## Happy path (skinned)

```bash
./install.sh motion3d
motion3d doctor

# 1) gerar motion (GPU via vramd; hw-auto pode escolher Full em ~6GB com text-CPU)
motion3d generate "a person walks forward" -o walk.npz --quality medium
python -c "import numpy as np; d=np.load('walk.npz'); print(d['joints'].shape, int(d['fps']))"

# 2) aplicar no SkinTokens *_rigged.glb (in-place, loopable)
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb \
  --clip walk --in-place

# 3) vários NPZ → um GLB multi-clip (walk/run/jump/sprint, …)
motion3d pack-rigged hero_rigged.glb -o hero_locomotion.glb --active walk \
  -m walk=walk.npz -m run=run.npz -m jump=jump.npz -m sprint=sprint.npz
```

### Multi-clip pack (`pack-rigged`) — 2026-08-03

Um asset de jogo precisa de vários clips no mesmo skin (locomoção + salto), não
um GLB por motion. `apply-rigged` continua a ser o path de um clip; o pack é a
API canónica para N clips:

| Peça | Papel |
|------|-------|
| `MotionClip` / `parse_motion_spec("name=path.npz")` | Spec tipado + parse CLI |
| `apply_motions_to_rigged` / `apply_npzs_to_rigged` | Target uma vez → source HML22 por clip → `retarget_animation` → N actions |
| CLI `motion3d pack-rigged … -m name=path.npz` | Happy path; `--active` = action default |
| Export | `export_animation_mode="ACTIONS"` (igual Animator3D `game-pack`) — NLA-only perdia clips em reimport |

`apply_motion_to_rigged` (1 clip) é thin wrapper sobre o pack. Constraints
(`--max-lean`, `--hands-together`, `--plant-feet`) são pack-level; campos do
`MotionClip` override por clip quando preenchidos. Smoke:
`Motion3D/runs/hy_hero_pack/hero_locomotion.glb` (walk/run/jump/sprint).

### Backend: HY-Motion-1.0 (não Motius)

- Default QualityEngine: Lite; **hw-auto** (estilo Text2D `plan_offload`) pode **subir para Full** em GPUs ~6 GB.
- Staged load: DiT (+SDNQ) na GPU; Qwen/CLIP encode em **CPU** quando `offload_text_encoder` — nunca Qwen+DiT juntos em 6 GB.
- Prompt rewriter LLM **off** (`disable_prompt_engineering=True`).
- NPZ: `joints (T,22,3)`, `fps=30`, opcional `rot6d`/`transl`. Sem `hml263`.
- FOOTPRINTS `hy-motion-lite|full` = DiT residente (não soma Qwen).
- Após editar Motion3D: `vramd respawn motion3d`.

### Soft-tune hw-auto

| Pressão VRAM | Efeito típico |
|--------------|---------------|
| ~6 GB | Full + text-CPU + `validation_steps`↓ + `duration` cap |
| ≥20 GB | Full, encoders podem ficar em GPU |
| CPU | Lite, steps baixos |

QualityEngine mapeia `duration` / `cfg_scale` / `validation_steps` / `model` (não `max_frames`/`temperature` Motius).

---

Pipeline interno de `apply-rigged`:

```
NPZ joints (T,22,3) Y-up
  → export_joints_glb  (bpy_export.py)
       rest T-pose canónico
       look-at swing-only + HML22_AIM_CHILD
       HML22_NEUTRAL_AIM (braços/coluna) + target rest só nos pés
       --in-place: strip travel + stabilize_facing_zup
  → GLB source HML22 (SkinTokens names + root estático)
  → animator3d.retarget_animation(profile=hml22)
  → GLB skinned animado
```

**Não** bakear `joints` como location nos bones do hero — deforma. Retarget fica no Animator3D.

---

## Instalação / venv (padrão monorepo)

| Peça | Onde |
|------|------|
| Registo Clified | `tools.yaml` → `motion3d` (`needs_pytorch`, `install_before: [animator3d]`, `cross_deps: [animator3d]`) |
| Post-install | `aigamekit_shared.installer.clified_hooks:dev_extras_post_install` → pytest/ruff no **próprio** venv |
| Cross-dep | `_clified_animator3d.pth` → `Animator3D/src` (não declarar `file:../Animator3D` no `pyproject.toml`) |
| CLI no PATH | `~/.local/bin/motion3d` → `Motion3D/.venv` |
| Testes | `make test-motion3d` → `Motion3D/.venv/bin/python -m pytest` |

Sem `./install.sh motion3d`, o Makefile avisa e cai no python do sistema (`huggingface_hub` / `torch` / `bpy` em falta). Corrigir instalando a tool, não com deps globais.

Detalhe do hook `[dev]`: [`../INSTALLING.md`](../INSTALLING.md) · código `Shared/.../installer/dev_extras.py`.

---

## CLIs

| Comando | Função |
|---------|--------|
| `motion3d generate PROMPT -o out.npz\|.glb` | Inferência T2M-GPT; vramd por omissão; `--also-npz` com `.glb` |
| `motion3d export-glb walk.npz -o hml22.glb` | Só source HML22 (debug / retarget manual) |
| `motion3d apply-rigged walk.npz rigged.glb -o out.glb` | Happy path skinned |
| `motion3d doctor` | deps, bpy, cache, vramd |

Flags importantes em `export-glb` / `apply-rigged`:

| Flag | Default | Efeito |
|------|---------|--------|
| `--in-place` | on | Sem travel horizontal nem yaw drift (clip loopable; game move o personagem) |
| `--root-motion` | off | Mantém transladação + yaw do sample |
| `--clip` | `walk` (`apply-rigged`) / `t2m_motion` (`export-glb`) | Nome da action no target / source |
| `--keep-source PATH` | — | Guarda o GLB HML22 intermédio |
| `--profile` | `hml22` | Perfil Animator3D |

---

## Anatomia do source HML22

HumanML3D = 22 joints Y-up. Source Blender = Z-up (`yup_to_blender`: `(x,y,z)→(x,-z,y)`), nomes SkinTokens, `root` estático nos pés (fora do `bone_map`).

### Aim (`HML22_AIM_CHILD`)

Cada osso aponta para o joint-filho (tail). **Não** derivar de `t2m_kinematic_chain`: as 3 cadeias que arrancam em `spine_03` sobrescrevem-se e o peito aponta à clavícula → colapso de pescoço/cabeça/braço.

### Rest canónico (`_canonical_rest_joints_yup`)

`t2m_raw_offsets` crus põem a cabeça **à frente** do pescoço e braços a pender. Rest do source tem de ser T-pose real (cabeça `+Y`, braços `±X`) senão o retarget herda ~90° de compensação.

### Folhas (`HML22_LEAF_BONES`)

`Head`, `hand_*`, `ball_*` — HML não tem joint de ponta. Apontar com a direção do pai → mãos torcidas, dedos enrolados («sapato de palhaço»), nod duplo. Ficam no rest; o pai carrega o movimento.

### Orientação: swing-only

`to_track_quat` mete roll arbitrário → membros torcidos no hero. Usar:

```text
swing = rest_dir.rotation_difference(aim_dir)
quat  = swing @ rest_quat
```

---

## Neutro SMPL ≠ neutro de jogo

O retarget do Animator3D copia direções em **espaço-mundo**. Anatomia T2M típica num walk:

| Segmento | Mediana no sample | Problema no hero |
|----------|-------------------|------------------|
| pescoço→cabeça | ~28° à frente | cabeça a olhar para o chão |
| braços | verticais colados | braços colados / depois abertos se calibrar no T-pose |
| pé D toe-out | ~29° vs ~6° E | assimetria / botas tortas |
| tornozelos | cruzam até −8 cm | botas a atravessar-se |

Correção na **mira**, não no rest do source:

| Constante | O que faz |
|-----------|-----------|
| `HML22_NEUTRAL_AIM` | Roda a mediana do clip para A-pose soft (coluna/pescoço `+Z`, braços ~15° fora) |
| `HML22_TARGET_REST_BONES` = `{foot_l, foot_r}` | Só estes puxam o rest do **rig alvo** (pitch/toe-out SkinTokens) |
| `HML22_LEG_SPLAY_DEG` (±6°) | Alarga a base sem mexer no timing da passada |

**Regressão conhecida:** calibrar braços com o rest completo do hero (= T-pose ~horizontal) reabre o walk. Em clips de locomoção os braços ficam em `HML22_NEUTRAL_AIM`.

### Braços «tesoura» em clips de braço levantado (2026-08-03)

O neutro de braços é derivado da **mediana do clip**. Em walk/jump a mediana já está perto do hang (correção ~20–35°), mas num chop/reach a mediana está a ~130–146° do hang — a correção passa a reescrever o gesto inteiro e o hero abre os braços em tesoura (mãos a 0.41 m em vez das 0.21 m do source; 0/90 frames de cruzamento vs 23/90).

| Peça | Comportamento |
|------|---------------|
| `arm_neutral_applies(joints)` | `False` quando a mediana de `upperarm_*`/`lowerarm_*` excede `HML22_ARM_NEUTRAL_MAX_CORR_DEG` (50°) |
| `filter_neutral_aim_for_clip(..., arm_neutral="auto")` | Remove os neutros de braço nesses clips (`on`/`off` forçam) |
| `resolve_neutral_targets` | `neutral_aim` **substitui** os defaults — antes fazia merge, logo era impossível *remover* uma calibração |
| CLI | `motion3d apply-rigged … --arm-neutral auto\|on\|off` |

Diagnóstico: `bone_map` identidade e clavículas/pernas/coluna tinham erro ≤6° contra a mira do source; só `upperarm_*` (~55°/33°) e `lowerarm_*` (~145°/133°) estavam desviados com valor **constante** por frame = a assinatura de uma correção fixa mal aplicada, não de mapa trocado. Após o fix, braços reproduzem a mira do source a 0.0°.

`--in-place` = `stabilize_facing_zup` + strip do travel horizontal do pélvis (bob vertical mantém-se).

### Constraints opt-in vs. adivinhar o gesto — 2026-08-03

HY ignora pedidos de contacto no prompt: as mãos separam-se (~0.12–0.33 m) mesmo com «clasped», e os pés patinam em ações estacionárias. A primeira versão detetava «isto é uma machadada» pela cinemática (`two_hand_grip_applies`: mediana sep ≤0.28 m + span Y ≥0.35 m) e aplicava um clamp que também pinava os pés — **especialização a fingir de automatismo**: adivinhava a intenção, misturava dois efeitos sem relação num só switch, e falhava em qualquer gesto que não fosse chop.

O `sanitize_locomotion_joints` passou a ter duas camadas:

| Camada | Peças |
|--------|-------|
| Sempre (segurança de rig, agnóstica ao gesto) | joelhos agudos abertos (`HML22_MIN_KNEE_DEG`), pitch do pé em `[-48°, +12°]` |
| Opt-in, declarada pelo chamador | `max_lean_deg=N` → `_clamp_torso_lean_yup`; `hands_together_m=N` → `_join_wrists_yup`; `plant_feet=True` → `_plant_feet_yup` |

CLI: `motion3d apply-rigged … --max-lean 25 --hands-together 0.10 --plant-feet` (todas off por omissão). Os nomes são mecânicos, não do gesto — `--hands-together` serve machado, cajado, montante ou arco; `--plant-feet` serve chop, cast, idle; `--max-lean` serve qualquer swing. Quem sabe a intenção (operador ou o planner do `dream`) escolhe as constraints; o motor só as impõe. Adicionar um switch novo por gesto é regressão.

**Referencial: eixo das ancas, nunca X/Z do mundo.** `--in-place` (`stabilize_facing_zup`) e o root motion aplicam uma transformação rígida *por frame*, logo qualquer constraint ancorada em X/Z global é destruída ou passa a lutar contra ela. Primeira versão do `plant_feet` pinava o tornozelo em XZ do mundo enquanto a bacia oscilava 1.22 m de transferência de peso numa machadada parada → pernas abertas e tronco torcido nos frames de impacto. `_body_lateral_yup` (anca direita → esquerda, horizontal) dá o referencial: `_plant_feet_yup` congela o offset pélvis→pé no frame 0 nesse referencial (com `in_place` a bacia é zerada, logo o pé fica de facto imóvel), e o anti-cruzamento das mãos é julgado nesse eixo em vez de `+X`.

**`--max-lean` mede pelvis→pescoço (12), não pelvis→peito (9).** A dobra do HY vive na coluna alta: no chop medido, `pelvis→chest` = 38° mas `pelvis→neck` = 60°. Clampar pelo peito baixava 38→30 e deixava o pescoço a 53° (continuava a parecer que se dobrava para apanhar algo). Pelo pescoço, `--max-lean 25` levou o ponto baixo das mãos de 0.83 m → 1.06 m com a cintura a 1.15 m — ou seja, a machadada passou a bater à altura da cintura como pedido, sem tocar no prompt. Roda-se o encadeamento superior inteiro (`HML22_UPPER_BODY_INDICES`, braços incluídos) em torno da bacia, senão os braços descolam dos ombros.

### Clamp genérico de dobradiças (joelho / tornozelo / cotovelo) — 2026-08-03

HY gera poses impossíveis: pointe ~−88°, dorsiflexão extrema (dedos à canela),
joelho a 49° no sprint. O pitch do pé no **mundo** não chega — um shin quase
horizontal com pitch −26° ainda é pointe relativo à canela.

| Peça | Papel |
|------|-------|
| `HML22_HINGE_LIMITS` / `_clamp_hinges_yup` | Ângulo interior (prox←joint→dist): joelho `[120°,172°]`, tornozelo `[70°,115°]` (flat~90°, pointe~180°, dorsiflex~0°), cotovelo `[35°,170°]` |
| Ordem | joelho → pitch mundo `[-35°,+8°]` → tornozelo (vence) → cotovelo; followers (`ball_*`) viajam com o ankle |
| Pés fora de `MAX_SWING` / `NEUTRAL_AIM` / `TARGET_REST` | Cap de swing 22–28° no pé impedia o look-at de apontar ao `ball_*` → boot ficava no rest enquanto a canela rodava → lia-se como sola pra cima. Hinge nos joints é o dono |

Sempre ligado (segurança de rig). Constraints opt-in (`--max-lean`, etc.) ficam à parte.

### Root do salto vem de `transl`, não de `keypoints3d`

`keypoints3d` sai ground-aligned: pelvis Y quase fixa (span ~0) enquanto `transl` tem o salto real (~0.52 m). `_reanchor_joints_to_transl` reancora o esqueleto em `transl` depois do FK; sem isso `--in-place` (que só corta XZ) entrega um salto sem subida.

---

## QA numérico (pós `apply-rigged`)

Importar o GLB skinned, action `walk`, por frame:

| Métrica | Esperado (walk in-place no hero) |
|---------|----------------------------------|
| `neck_01` / `Head` dir mundo | ≈ `+Z` (0.99–1.0), quase iguais |
| `upperarm_*` xz | ≈ (±0.26…0.30, −0.96) — **não** (±0.92, −0.38) |
| `foot_*` pitch / toe-out | ≈ rest do alvo (hero: −34° / ±21°) |
| sep. tornozelos X | min > 0 (sem cruzar) |
| `pelvis` XY | ~0 travel; Z oscila ~cm |
| sola mesh Z | ~0 ± poucos cm |

Screenshots: `aigamekit-lab debug screenshot hero_walk.glb -o shots/`.

### Fingerprint do walk bom (restaurado 2026-08-02)

Referência: `outputs/motion3d_hero_test/hero_t2m_walk.glb` + `walk.npz`.
Rebake do **mesmo** NPZ com o `apply-rigged` actual → métricas **idênticas**
(o «horrível» foi NPZ de 8 frames, não regressão de retarget).

| Campo | Valor |
|-------|------:|
| prompt | `a person walks forward` |
| NPZ frames @ 20 fps | **116** (~5.8 s) |
| pelvis travel XY no NPZ | ~0.52 m (stripado no clip) |
| GLB `pelvis` XY travel | **0** (`--in-place`) |
| `pelvis` Z amp | ~6 cm (bob) |
| `upperarm_l` xz median | **(0.27, −0.96)** — A-pose soft, não T-pose |
| `neck_01` z median | **0.998** |
| foot pitch / toe-out median | **−34° / ±21°** (= rest SkinTokens) |
| ankle sep min / mean | **0.20 / 0.32 m** (0 cruzados) |
| `hand_*` vs `lowerarm_*` | ângulo **0°** (folhas no rest) |
| `ball_*` vs `foot_*` | ângulo **0°** |
| sola Z | −1.1 … +3.2 cm |

Receita: NPZ longo (≥80 frames) + `apply-rigged --in-place` + A-pose braços/
coluna + rest do alvo só nos pés + leg splay + folhas quietas.

---

## Anti-padrões

1. Bake locations HML nos bones SkinTokens.
2. Reimplementar retarget no Motion3D.
3. Aim via `t2m_kinematic_chain`.
4. Rest cru de `t2m_raw_offsets`.
5. Aim em folhas (`Head` / `hand_*` / `ball_*`).
6. Neutro dos braços = rest T-pose do alvo.
7. Calibrar neutro rodando o rest do *source* (retarget é absoluto em mundo).
8. `kill`/pkill GPU com vramd busy — usar `vramd` / `--vramd-stream`.
9. Correr testes fora de `Motion3D/.venv`.

---

## Ficheiros

| Ficheiro | Papel |
|----------|-------|
| `Motion3D/src/motion3d/bpy_export.py` | Source HML22, aim, neutro, in-place |
| `Motion3D/src/motion3d/apply_rigged.py` | Orquestra export + `target_rest_aims` (pés) + retarget |
| `Motion3D/src/motion3d/cli.py` | `generate` / `export-glb` / `apply-rigged` / `doctor` |
| `Animator3D/.../retarget/hml22.yaml` | `bone_map` + `clip_map` |
| `Animator3D/src/animator3d/retarget.py` | `retarget_animation` |
| `tools.yaml` → `motion3d` | Install / cross_deps / post_install |

---

## Changelog

| Data | Nota |
|------|------|
| 2026-08-03 | Migrar generate → HY-Motion-1.0; hw-auto estilo Text2D (Full em ~6GB, staged text-CPU, plan_offload); Motius removido |
| 2026-08-02 | Happy path `apply-rigged`; aim explícito; folhas no rest; neutro A-pose; pés do rest alvo; leg splay; in-place; install + `[dev]` no venv |
| 2026-08-02 | Anti-EOS precoce: min tokens + temperature 0.5 em fast/low/medium (stubs de 8 frames) |
| 2026-08-02 | Run "horror": samples assimétricos (1 joelho ~70°, outro ~170°) esticam SkinTokens. Fix: `sanitize_locomotion_joints` (min joelho 105°) + `HML22_MAX_SWING_DEG`; strip Icosphere; action activa no export. Preferir prompts bilaterais (`a man is running`) + seed search; Quaternius `run` no `hero_lod0` continua a referência de jogo. |
