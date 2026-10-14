# Sessão partilhada de QA + armadilhas conhecidas

Fonte: `Viber/AGENTS.md` §Sessão partilhada + §BRIDGE, `Viber/src/session.rs`.

## Protocolo de sessão (`viber session …`)

Estado em `<cache>/viber/session-<slug-do-mundo>/`: `lease.json` (criado com
`create_new` — o mutex é o SO) e `engine.json` (pid/porta/mundo/log; o cliente
descobre a porta por aqui — não é preciso `VIBER_BRIDGE_PORT` no fluxo normal).

```bash
viber session status   # SEM SESSÃO | LIBERADO | OCUPADO por X | engine MORTA
viber session list     # todas: mundo, porta, viva/morta, dono
viber session up [--port N]     # sobe a engine + espera o bridge (log em ~/.cache/viber/logs/)
viber session claim --owner <tarefa>   # exit 0 = conseguido; exit 3 = OCUPADO
viber session touch --owner <tarefa>   # renova TTL (default 300 s)
viber session release --owner <tarefa> # liberta (--owner tem de bater)
viber session down                 # desce a engine (exige sessão livre)
```

**Regras duras:**

1. Nunca `viber run --bridge` diretamente para QA — `status` primeiro, `up` se
   "SEM SESSÃO".
2. Claim antes de TOCAR na engine. Exit 3 (OCUPADO) = fazer outro trabalho e
   voltar; no máximo UMA vez `claim --wait 120`.
3. 1–2 testes e `release` imediato; QA longa renova com `touch`. O lease expira
   sozinho (TTL 300 s) — quem vem depois rouba-o sem cerimónia.
4. **Nunca matar processos de engine** — só `session down` (exige sessão livre).
   SIGTERM não mata a engine (kill -9 mataria, mas NÃO é a via — a sessão é).
5. Engine "MORTA" → `viber session down && viber session up`.

Porquê: várias engines simultâneas esgotam a VRAM ("Quitting the application due
to OutOfMemory RenderError" sem panic).

## Resolução de porta e múltiplas engines

- Precedência do cliente: `--port` → `--world` (via `engine.json` + ping de
  identidade; registo stale → erro, NÃO muta o mundo errado) → `VIBER_BRIDGE_PORT`
  → descoberta implícita → 15702.
- **Várias engines vivas + escolha implícita = ERRO intencional** com a lista
  `:porta — mundo`. Apontar SEMPRE com `--world` (caminho, nome de ficheiro ou
  stem: `--world qa-pontes`).
- `viber debug probe` com ambiguidade LISTA as engines (não falha).
- `viber run --bridge` sem valor escolhe a 1.ª porta LIVRE ≥ 15702 (linha de
  arranque sugere o `--world` certo).

## Armadilhas conhecidas (vivas, 2026-08/10)

- **Screenshots pretos** com a GPU disputada por outra engine/processo — confirmar
  com `nvidia-smi --query-compute-apps=pid,used_memory --format=csv` antes de
  citar números de performance; A/B entre BOOTS vale ±100%. Só A/B interleaved no
  MESMO processo com `viber.debug.set_clock(720)` preso vale medição.
- **Janela VISÍVEL para medir performance** — ocluída, o compositor estrangula a
  apresentação e `render.prepare_views` salta 8 → 15–26 ms.
- **Validar o mundo antes de confiar num screenshot** — ping/probe devolve o
  `world` servido; um screenshot de outra engine parece "sucesso".
- **Input `key` é TAP, não hold** — para segurar, enviar eventos repetidos.
- **Debug key do profiler é toggle** (P abre modal; F12/Pause congela).
- **`world_hash` exige mundo congelado** (`step 0`; hash 2× iguais; `play`).
  Inclui posições e HP — mundo a correr dá valores diferentes a cada chamada.
- **Ops de debug que escrevem direto (`damage`/`heal`/`kill`/`give`) NÃO geram
  eventos** no ring — provoca com gameplay real (ou `toast`, que escreve
  `ScriptToast`).
- **Snapshot/escrita em 2 chamadas**: `tp` + `player()` na MESMA chamada devolve a
  posição de ANTES (snapshot do início).
- **`stats()` custa ~100 ms** (60k entidades) — não poluir medições; `prof`/ping
  são baratos.
- **Sem guard de instruções na REPL** — `while true do end` congela o frame
  (risco aceite; como um script de página no Chrome).
- **`set_material` só em materiais STANDARD** (primitivas/GLB) — os bindless do
  terreno destruíam o bind group.
- **Bevy não expõe tempos por sistema/entidade de render** — o real: `physics()`,
  `prof()` e proxies em `stats()`. Para regressões: comparar `stats()`/`prof()`
  antes/depois da mudança suspeita.
- **Screenshot de janela minimizada não captura** (guard de stall 30 s → error).
- **`scene --live` não tem `--dry-run`**; ops não incrementais (terreno, céu, UI,
  spawners) ficam `SceneSyncStatus::needs_reload` — a edição grava no doc mas o
  live-sync desses tipos exige reload.
- **`viber scene`/`bake`/`tool` no binário instalado**: exigem rebuild da fonte
  (`./install.sh viber`) se o binário for anterior a 2026-10-13 — o CLI devolve
  "unrecognized subcommand".
- **`viber run` dentro do checkout delega a `cargo run`** (rebuild; mais lento na
  1.ª vez) — usar `--no-cargo` para forçar o binário instalado.
- **Save/load por bridge** usa o mesmo caminho da UI
  (`~/.local/share/viber/<mundo>.save.json`).

## Edição viva (`viber scene --live …`)

Mesma API do offline; a edição entra no histórico da engine (undo/redo), grava e
sincroniza ao vivo: mudanças de pose → patch do Transform (+ teleporte Rapier);
inserir/remover/mover → spawn/despawn incremental com realinhamento de origens;
`viber.scene.entities {selector}` mostra as entidades VIVAS do nó (pose local +
mundo) — é a verificação de uma edição; `viber.scene.pick {x,y}` devolve o seletor
sob um pixel (câmara de editor).
