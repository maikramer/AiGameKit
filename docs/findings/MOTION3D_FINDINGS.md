# Motion3D — findings (SprongMotion-14M → sprong22 → SkinTokens)

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Package: [`Motion3D/README.md`](../../Motion3D/README.md) · [`Motion3D/AGENTS.md`](../../Motion3D/AGENTS.md).  
Retarget partilhado: [`ANIMATOR_RETARGET_FINDINGS.md`](ANIMATOR_RETARGET_FINDINGS.md).  
Perfil: `Animator3D/src/animator3d/data/retarget/sprong22.yaml`.

---

## Porquê a migração (2026-10-30)

O HY-Motion-1.0 (Tencent) foi avaliado e **não entrega movimento utilizável** de forma
consistente: o modelo ignora contacto/postura no prompt (mãos abrem, pés arrastam,
"swing down" dobra a coluna ~60°), o stack exige DiT em GPU + Qwen3-8B (5,7 GB de pesos,
~4 Gio de VRAM em int4) e cada clip custa dezenas de segundos. As constraints
mecânicas (`--max-lean`, `--hands-together`, `--plant-feet`) existiam para *remendar*
esses defeitos.

O SprongMotion-14M resolve o problema na raiz: 20 M parâmetros, 33 MB num GGUF, runtime
C11 **CPU-only** (~0,2 s por 2 s de clip) e opções nativas de `loop`, `foot_lock`,
`in_place`, `path` e edição por codes. Licença Apache-2.0.

## Factos medidos

| Item | Valor |
|------|-------|
| Modelo | `SprongMotion-14M-v1.0-Q8_0.gguf`, 33 337 824 B, sha256 `68fbd311…` |
| Runtime | v1.1.0 (commit `e11fe9ae`), 15 ficheiros C, build `make lib` limpo |
| Geração 2 s (40 frames) | **0,19 s** com 16 threads; 3 tentativas + NPZ/BVH = 1,03 s |
| Esqueleto | 22 joints @ **20 fps**, sem dedos, Y-up, frente +Z, metros |
| Determinismo | mesma seed + mesmo build = joints bit-idênticos |
| Sem GPU | não há backend vramd; o CLI corre in-process |

## Esqueleto

```
0 pelvis ── 1 l_hip ── 4 l_knee ── 7 l_ankle ── 10 l_foot
         ├─ 2 r_hip ── 5 r_knee ── 8 r_ankle ── 11 r_foot
         ├─ 3 spine1 ── 6 spine2 ── 9 spine3 ── 12 neck ── 15 head
         │                                     ├─ 13 l_collar ── 16 l_shoulder ── 18 l_elbow ── 20 l_wrist
         │                                     └─ 14 r_collar ── 17 r_shoulder ── 19 r_elbow ── 21 r_wrist
```

É o **mesmo corpo de 22 joints do HumanML3D**: os índices e os pais são exatamente os do
perfil `hml22`, só mudam os nomes (`l_hip` = thigh_l, `l_knee` = calf_l, `l_ankle` = foot_l,
`l_foot` = ball_l, `l_collar` = clavicle_l, `l_shoulder` = upperarm_l, `l_elbow` = lowerarm_l,
`l_wrist` = hand_l). Todas as tabelas index-based (hinges joelho/tornozelo/cotovelo, splay,
swing, upper-body) transitam sem alterações.

O **rest skeleton do modelo** (exposto em `sm_model.rest_offsets` e gravado no NPZ em
`offsets`) tem braços a pender na vertical, pés rasos e o **pelvis na origem** (pés a
−0,89 m no frame Y-up).

## Joints, não rotations

O runtime expõe `joints` (posições) **e** `rotations` (quaternions locais) + `offsets`.
FK a partir de rotations+offsets reproduz os joints com erro médio de **1,24 cm** e máximo
de **3,5 cm** (pulsos): as `offsets` são a mediana do clip e o IK analítico do runtime não
preserva posições exatamente. Conclusão: **`joints` é a fonte de verdade** e o bake continua
a ser por *look-at* (cada osso aponta ao joint filho), como no pipeline anterior.

## Bug encontrado: rest do pelvis na origem → corpo voa

O Animator3D escala a location transferida por
`loc_scale = target_height / source_height`, onde `source_height` é a altura rest do osso
hips (`max(|head.y|, |head.z|)`, mínimo 1e-3). Como o rest do SprongMotion tem o pelvis em
`(0, 0, 0)`, o rácio colapsava (~800×) e o herói subia a **20–40 m** no GLB final
(frames intermédios com o corpo deitado na render).

Fix em `export_joints_glb`: **levantar o rest skeleton para a altura do clip**
(`rest_y += joints[0, 0]`). Só translada o rest — as direções dos ossos não mudam — e o
rácio passa a ≈1. Verificado: pelvis 0,926 → 0,975 m com bob de caminhada no source e
0,80–0,85 m no herói retargetizado.

## Export e retarget

- Source GLB: armature de 22 ossos com os **nomes dos joints** (não SkinTokens), 1 action
  `sprong_motion`, root estático nos pés, bake look-at com `make_compatible` (sem saltos
  de quaternion).
- Perfil `sprong22`: mapeia `l_hip→thigh_l`, … (candidatos UE5/Mixamo), **sem `root`**.
- `_axis_correction` do Animator3D cancela o *swing* entre as direções de rest, por isso o
  rest de braços em baixo do SprongMotion vs T/A-pose do alvo não duplica rotação.
- As constraints (`--max-lean`, `--hands-together`, `--plant-feet`, `--arm-neutral`,
  `--in-place`) continuam disponíveis e são aplicadas sobre os joints antes do bake; com
  `foot_lock`/`loop` nativos, raramente são precisas.
- O artefacto visual na bota do herói (uma tira esticada junto ao chão) **já existe** no
  `hero_rigged_animated.glb` do pool (clip `attack`) — é skinning do rig, não regressão do
  retarget.

## Variação entre seeds

O model card avisa: "results vary a lot between prompts and seeds". Medido em "a person
walks forward" (2 s): seeds 7/8/9 deram scores 0,27–0,29 (arrasto 2,6–5,9 mm/frame, jerk
0,008–0,013). Por isso o CLI gera `--attempts` seeds (default **3**) e escolhe pelo score
mecânico de `quality.py`:

- `foot_slide`: deslocamento horizontal médio dos pés em contacto (contactos do runtime),
  reconstruindo a viagem a partir de `root` para clips `in_place`;
- `ground_penetration`: profundidade abaixo do plano de apoio do próprio clip (p05 dos pés);
- `jerk`: terceira diferença da trajetória da raiz (glitches de um frame).

Só a ordenação entre seeds importa; a seed escolhida fica no NPZ.

## NPZ

`joints (T,22,3)`, `offsets (22,3)`, `rotations (T,22,4)`, `root (T,3)`, `speed (T,)`,
`contacts (T,4)`, `codes (tokens,6)`, `fps`, `seed`, `prompt`, `model` (`…@crc`),
`skeleton="sprong22"`, `joint_names`, `joint_parents`, `quality_*`.
`allow_pickle=False` na leitura; NPZs do Motion3D antigo (HY/HML22) são recusados com
instrução para regenerar.

## Ops

- Build do runtime: `~/.cache/aigamekit/sprongmotion/<versão>-<hash12>/`, flags upstream
  (`-ffp-contract=off`, sem `-Werror`); `SPRONG_LIB`/`SPRONG_CC`/`SPRONG_CFLAGS`.
- Modelo: cache HF; `SPRONG_MODEL` aponta para um `.gguf` local (offline/CI).
- `vramd`: o backend `motion3d` foi removido do `backends.yaml` e do catálogo calibrado;
  não há worker para respawn.
- Testes: `make test-motion3d` (276 casos; e2e com GGUF gate por `SPRONG_MODEL`/cache).
