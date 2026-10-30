# Motion3D

CLI text-to-motion do AiGameKit com **SprongMotion-14M** a correr na **CPU**: um
único ficheiro GGUF de 33 MB e um runtime C11 vendorizado. Sem PyTorch, sem GPU,
sem contas de VRAM.

Pipeline: prompt → SprongMotion (`22 joints @ 20 fps`) → NPZ
(`joints`, `rotations`, `offsets`, `root`, `contacts`, codes) → `apply-rigged`
opcional (retarget Animator3D, perfil `sprong22`) → GLB skinned.

## Instalar

```bash
./install.sh motion3d     # já faz parte do perfil `core` por omissão
motion3d doctor           # compilador, runtime, modelo (sha256), bpy, perfil de retarget
motion3d download-model   # ~33 MB do HuggingFace (também corre no primeiro generate)
```

O runtime é compilado de `src/motion3d/vendor/sprongmotion/` para
`~/.cache/aigamekit/sprongmotion/<versão>-<hash-das-fontes>/` no primeiro uso.
Precisa de um compilador C11 (`cc`; em Debian/Ubuntu `build-essential`).
Overrides: `SPRONG_CC`, `SPRONG_CFLAGS`, `SPRONG_LIB` (biblioteca pré-compilada),
`SPRONG_MODEL` (`.gguf` local).

## Happy path

```bash
# Gerar (CPU; ~0,2 s por cada 2 s de clip num portátil)
motion3d generate "a person walks forward" -o walk.npz --duration 2

# Aplicar num SkinTokens *_rigged.glb (in-place, loopable)
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb --clip walk

# Empacotar um kit de locomoção num só GLB (export ACTIONS)
motion3d pack-rigged hero_rigged.glb -o hero_locomotion.glb --active walk \
  -m walk=walk.npz -m run=run.npz -m jump=jump.npz -m idle=idle.npz
```

Cada `-m nome=caminho.npz` vira uma animação glTF com nome no mesmo mesh/skin
(mesmo contrato do Animator3D `game-pack`); `--active` escolhe a action ativa
para viewers que ignoram os clips extra.

`-o ficheiro.glb` escreve a armature source intermédia em vez de um NPZ (útil
para inspecionar o movimento no Blender); `--bvh outro.bvh` escreve também o BVH
do próprio runtime.

### Seeds e `--attempts`

O model card é explícito: os resultados variam muito entre seeds e uma boa parte
dos clips precisa de outra seed antes de ser utilizável. Gerar é barato, por
isso o CLI gera **3 seeds por omissão** (`--attempts N`) e fica com a de menor
score mecânico (arrasto dos pés plantados, penetração no chão, jerk da raiz —
ver `motion3d/quality.py`). `--attempts 1` fixa uma seed; a seed escolhida fica
gravada no NPZ, por isso a corrida é reproduzível a partir do ficheiro.

## Flags

| Flag | Papel |
|------|-------|
| `--duration/--seconds` | Comprimento do clip (0,5–10 s; engine a 20 fps) |
| `--frames` | Alternativa a duration (`segundos = frames / 20`) |
| `--seed` | Seed RNG; mesma seed + mesmo build = movimento idêntico |
| `--attempts` | Seeds a experimentar, fica a mais limpa (default 3) |
| `--steps` | Passos de unmasking da camada base (1–64, default 10) |
| `--residual-layers` | Camadas residuais (0–5; 0 = rascunho rápido) |
| `--text-guidance` / `--residual-guidance` | Classifier-free guidance (0–20) |
| `--temperature`, `--top-k` | Controlo de amostragem |
| `--loop` | Ciclo sem costura (walk/run/idle) |
| `--foot-lock/--no-foot-lock` | Pés plantados ficam no sítio (default on) |
| `--in-place` | Remove a viagem horizontal já no clip gerado |
| `--quality` | Soft defaults de duração/steps/residual layers/attempts |
| `--threads` | Threads do runtime (0 = todos os cores) |
| `--bvh`, `--also-npz`, `--json` | Artefactos extra / resultado machine-readable |

`apply-rigged`/`pack-rigged` mantêm as constraints mecânicas do pipeline
anterior para clips de jogo — todas opt-in:

| Flag | Efeito |
|------|--------|
| `--in-place` (default on) | Clip loopable: viagem horizontal e yaw drift removidos |
| `--max-lean 25` | Limita a inclinação do torso (graus) |
| `--hands-together 0.10` | Pulsos a menos de N metros (prop de duas mãos) |
| `--plant-feet` | Congela o stance na horizontal (ações paradas) |
| `--arm-neutral auto\|on\|off` | Calibração suave de braços; `auto` salta clips de braços erguidos |

## Prompting

Uma ação por prompt, em inglês simples: *"a person walks forward"*, *"waves with
the right hand"*, *"jumps up high"*. Corridas levam direção (*runs forward*, não
*runs*). Estilo, lado, velocidade e número de repetições fazem parte das
captions de treino. Sentar/deitar usam um prop invisível (convenção de animação
de jogos).

## Limitações (do model card)

- Mortais e cambalhotas podem correr mal; piões rápidos podem falhar num frame.
- Costuras entre blocos com prompts diferentes são mais ásperas do que dentro de um bloco.
- Os pés podem arrastar em walks, sobretudo na passagem walk → parado.
- 22 joints, sem dedos; sem contacto com objetos.
- Apenas inglês.

## Pesos, runtime e licenças

- Modelo: [`SegerEnd/SprongMotion-14M`](https://huggingface.co/SegerEnd/SprongMotion-14M), Apache-2.0,
  `SprongMotion-14M-v1.0-Q8_0.gguf` (SHA-256 fixo em `motion3d/weights.py`).
- Runtime: [sprongmotion-runtime](https://github.com/SegerEnd/sprongmotion-runtime) v1.1.0, vendorizado em
  `src/motion3d/vendor/sprongmotion/` (ver `VENDORED.md` e [THIRD_PARTY.md](THIRD_PARTY.md)).

Depois de editar código do Motion3D não há worker vramd para respawn — o CLI
corre in-process.
