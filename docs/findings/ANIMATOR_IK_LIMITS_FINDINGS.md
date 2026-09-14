# Animator3D — IK de 2 ossos com limites de juntas (`animator3d.ik`)

Data: 2026-09-14 · Estado: implementado e validado (goblin UAL + rig sintético)

## Problema

O retarget (`animator3d/retarget.py`) adopta quaternions **absolutos** do pack
source sem verificação anatómica — mocap/packs trazem poses impossíveis
(joelho a dobrar para a frente, pointe, cotovelo invertido, hiperextensão) que
chegam intactas ao engine. O Motion3D clampe dobradiças a montante
(`_clamp_hinges_yup`, joelho/ankle/elbow por ângulo interior) mas só no
esqueleto HML22 de 22 juntas **antes** do retarget e sem noção de *direção*.
Nem VibeGame nem Viber fazem IK em runtime — o que se corrigir na geração é o
que chega ao ecrã.

## Desenho

Módulo novo `Animator3D/src/animator3d/ik.py` — núcleo puro (numpy, testável
sem bpy) + camada bpy fina. **Sem dependências novas** (ikpy exigia scipy +
conversão bpy↔URDF; pybullet é uma engine de física com bug conhecido de
ultrapassar limites; todas as cadeias relevantes são de 2 ossos + effector,
território de IK analítica fechada).

Passe pós-animção, por action, por frame **com keyframes existentes** (nunca
acrescenta keys — densidade do clip preservada):

1. **Cadeias** de `_classify_bone_chains` (leg_r/l, legs_r/l multi-pata,
   arm_r/l): `TwoBoneChain(role, upper, lower, effector)`. Pernas usam os
   **primeiros** 3 ossos; braços os últimos 3 (salta o shoulder).
2. **Curso da junta**: ângulo interior prox←junta→distal (180° = esticado)
   fora da banda → nova distância root→effector pela lei dos cossenos
   (`distance_for_interior_angle`).
3. **Direção da dobradiça** (one_way): alinhamento do eixo de flexão
   `dot(normalize(T×S), normalize(T×pole))` — ≈−1 = joelho/cotovelo INVERTIDO
   → re-solução analítica da cadeia (`two_bone_knee_position`, lei dos
   cossenos) com o **pé/mão NO SÍTIO** (n_target = eff atual) e o joelho
   espelhado para o lado do pole.
4. **Ball joints** (anca/ombro/pé): clamp swing-from-rest + twist, **relativo
   ao pai** (a própria matrix_basis).

### Limites (`data/ik/limits.yaml`, override por rig em `data/retarget/*.yaml`)

Bloco `ik_limits:` nos perfis de retarget (merge raso por papel;
`enabled: false` desliga). Defaults leg: hinge [40°, 180°], arm: [20°, 180°],
caps swing/twist anca/ombro/pé. `hinge_max` 180 = desligado (perna esticada
179-180° é normal em suporte/roar — 175 dava falsos positivos). Os clamps só
tocam poses impossíveis (tolerância 0.5°) — clips corretos ficam bit-idênticos.

CLI: `--ik-limits/--no-ik-limits` (default ON) em `game-pack`, `retarget`,
`retarget-batch`; comando `ik-limits IN.GLB [OUT]` para reparar GLBs animados
existentes. GameAssets: `animator3d.ik_limits` no game.yaml (None = default ON;
False → `--no-ik-limits`).

## Lições duras (o que não funciona — e o que funciona)

1. **Identidade de cadeia**: `W = Wp·Rp⁻¹·R·B` ⇒ o basis (rotação da JUNTA,
   relativa ao pai) é `B = (Wp·Rp⁻¹·R)⁻¹·W` — **o termo Rp é essencial**
   (Quaternius tem pelvis/thigh com rest ~±90°; sem ele tudo explode).
   Validado empiricamente contra `pb.rotation_quaternion`.

2. **Ball clamps relativos ao PAI, não ao rest em espaço armature**: medir
   swing/twist contra o rest global disparava caps em TODOS os frames de
   death/roll (corpo deitado roda as pernas com a pelve sem as juntas
   rodarem). O basis é exatamente a rotação da junta.

3. **Sinal de quaternion**: quaternions de produtos de Hamilton chegam com
   `w<0` (≡ mesma rotação); `twist = 2·atan2(d, w)` explode para ~±2π e
   dispara clamps em pose neutra. Canonicalizar `q←−q` se `w<0` no
   `swing_twist_split`.

4. **Avaliação subframe**: bpy 5.x `frame_set` é int-only; clips com keys
   fracionárias (source 30fps em cena 24fps — keys a 16.8, 17.6…) exigiam
   `frame_set(int) + frame_subframe`. Avaliar no `round()` lia a pose
   interpolada no tempo ERRADO e escrevia a correção na key do frame vizinho
   (criou um joelho a 5.7° num clip limpo).

5. **Teste de lado por EIXO, não por posição**: o teste "joelho do lado X da
   corda root→eff" dá falsos positivos em pernas em balanço profundo (joelho
   legitimamente à frente da corda) e em pontapés. O correto: alinhamento do
   EIXO de flexão `T×S` contra o eixo esperado `T×pole` — inversão = eixo
   ao contrário, independente do swing.

6. **Pole no frame do osso upper + reparo com sinal garantido**: deteção e
   reparo são consistentes quando o pole vive no frame de REST do upper
   (constante) e roda com W1(t). O espelho do reparo ainda precisa de um
   guard: com a coxa além de ±90° o pole pode apontar para o MESMO lado do
   joelho invertido (espelho vira no-op) — o reparo alinha o sinal do pole
   com o lado desejado (oposto ao atual no flip, o atual no clamp).

7. **Auto-pole validado contra o prior anatómico**: o lado de flexão do rest
   é enganador quando a perna quase esticada tem a rótula ligeiramente à
   frente (a anatomia humana!) — o auto-pole saía para a FRENTE e marcava
   tudo como invertido. Se o rest-bend aponta contra o `pole_fallback`
   (`back` para pernas, `out-back` para braços, relativo à frente detetada
   por `_detect_forward`), vence o fallback.

8. **Maioria-voto no one_way** (≥70% dos frames do clip do lado do pole):
   clips com o corpo rodado (death/roll deitados, knockback) têm joelhos
   legitimamente de "lado errado" de um pole estático — sem o voto, o passe
   distorcia-os. Trade-off: um clip >70% corrupto só leva clamps de ângulo
   (direção não é espelhada) — correto por defeito (não adivinhar).

## Resultados medidos (goblin UAL rigged_animated, 12 clips, 2 cadeias)

- **Clips corretos intactos**: idle/hit/attack/run/roar/jump/knockback/death/
  sworda/swordb — 0 correções (idle: 0 correções em 76 frames × 2 cadeias).
- **Joelho invertido injetado** (rotação 180° no calf de walk): reparado
  (ângulo interior 7.5°→ banda válida; pé preservado ±1 mm).
- **roll pós-fix subframe**: nenhum frame fora da banda (antes do passe nem
  existia violação — zero falsos positivos em tumble).
- Custo: ~4.8 s para 12 clips no comando `ik-limits` (GLB com textura).

## Uso

```bash
animator3d game-pack rigged.glb animated.glb                 # passe ON por defeito
animator3d game-pack rigged.glb animated.glb --no-ik-limits  # opt-out
animator3d ik-limits animated.glb animated_fixed.glb         # reparar GLB existente
# game.yaml
animator3d: { ik_limits: false }   # None/omissão = ON
```

QA visual pendente: A/B de screenshots antes/depois num hero rigged com mocap
(aguardar regen do pool); os valores do YAML podem exigir afinação por rig via
bloco `ik_limits:` do perfil.
