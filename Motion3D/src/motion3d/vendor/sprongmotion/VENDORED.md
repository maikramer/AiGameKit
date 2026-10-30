# SprongMotion runtime — vendored copy

Upstream: <https://github.com/SegerEnd/sprongmotion-runtime>
Tag: **v1.1.0** — commit `e11fe9ae04cefdf964345670eea0427e4d0049a3` (2026-10-26)
License: Apache-2.0 (see `LICENSE`, `NOTICE`)

Copied verbatim (do **not** patch):

| Path | sha256 |
|------|--------|
| `include/sprongmotion.h` | `d3fb0e086dbe68e0e550f66892f4bc6ec41f12bef8aa0da908589a474dc681fb` |
| `LICENSE` | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `NOTICE` | `aea5424376922c2262118b33e5d3470c3d2ba02da84498c4784ad2738f0c7017` |
| `src/` (tree hash of `sha256sum src/*.c src/*.h \| sort \| sha256sum`) | `55cd1c842be17fad67e8507512452637a939eca7d4e5ca68576f074e3c20df68` |

`CHANGELOG.md` and `examples/01_hello.c` are kept for provenance/debugging only.

## Build

`motion3d.sprong.runtime.ensure_runtime()` compiles this directory out-of-tree
into `~/.cache/aigamekit/sprongmotion/<version>-<source-hash>/`, using the same
flags as upstream `make lib` (minus `-Werror`, so a newer compiler cannot break
the install):

```
cc -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -ffp-contract=off \
   -Iinclude -pthread -O2 -fPIC -fvisibility=hidden -DSM_SHARED -DSM_BUILD \
   -shared src/*.c -o libsprongmotion.so -lm
```

`-ffp-contract=off` is what makes the output bit-identical to every other exact
build (native/wasm); do not add `FAST=1`/`-ffp-contract=fast`.

Overrides: `SPRONG_LIB` (prebuilt shared library), `SPRONG_CC` (compiler),
`SPRONG_CFLAGS` (extra flags, e.g. `-O3` — changes speed, not results).
