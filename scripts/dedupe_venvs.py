#!/usr/bin/env python3
"""Deduplica ficheiros byte-idênticos entre as venvs do monorepo (hardlinks).

O pip **copia** wheels para cada venv (sem hardlinks como o uv), por isso o
stack torch/CUDA (~5-6 GiB) vive replicado em cada `<Tool>/.venv` quando as
versões coincidem. Este script agrupa ficheiros >= ``--min-size`` por
(tamanho, blake2b) **entre todas as venvs recebidas** e substitui duplicados
por hardlinks para uma cópia guardiã — mesmo inode, conteúdo idempotente,
zero rede e zero reinstalações.

Segurança: a substituição é atómica por ficheiro (``os.link`` para um nome
temporário no mesmo diretório + ``os.replace``); um crash a meio deixa as
venvs consistentes (ficheiros antigos intactos até ao replace). Actualizar/
desinstalar um pacote numa venv faz ``unlink`` do link — as irmãs ficam
intactas. Ficheiros < ``min-size`` são ignorados (poupança marginal, custo
de hashing alto).

Uso::

    python3 scripts/dedupe_venvs.py                 # dry-run (estimativa)
    python3 scripts/dedupe_venvs.py --apply         # aplicar
    python3 scripts/dedupe_venvs.py Tool1/.venv Tool2/.venv --apply

Ver também: docs/findings/MODEL_CONSOLIDATION_STUDY.md §6.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

MIN_SIZE_DEFAULT = 1024 * 1024  # 1 MiB
_CHUNK = 8 * 1024 * 1024


def _hash_file(path: Path) -> str:
    h = hashlib.blake2b(digest_size=32)
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _collect(paths: list[Path], min_size: int) -> dict[int, list[Path]]:
    by_size: dict[int, list[Path]] = {}
    for root_path in paths:
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    st = p.stat()
                    if st.st_nlink > 1:
                        continue  # já partilhado (dedupe anterior) — nada a ganhar
                    size = st.st_size
                except OSError:
                    continue
                if size >= min_size:
                    by_size.setdefault(size, []).append(p)
    return {size: files for size, files in by_size.items() if len(files) > 1}


def _group_by_hash(candidates: dict[int, list[Path]]) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for files in candidates.values():
        by_digest: dict[str, list[Path]] = {}
        for p in files:
            try:
                by_digest.setdefault(_hash_file(p), []).append(p)
            except OSError as exc:
                print(f"  [aviso] falha ao ler {p}: {exc}", file=sys.stderr)
        for digest, paths in by_digest.items():
            if len(paths) > 1:
                groups[digest] = sorted(paths)
    return groups


def _reclaimable(groups: dict[str, list[Path]]) -> int:
    total = 0
    for paths in groups.values():
        total += paths[0].stat().st_size * (len(paths) - 1)
    return total


def _apply(groups: dict[str, list[Path]]) -> tuple[int, int]:
    linked = 0
    failed = 0
    for paths in groups.values():
        keeper = paths[0]
        for target in paths[1:]:
            tmp = target.with_name(target.name + ".dedupe-tmp")
            try:
                os.link(keeper, tmp)
                os.replace(tmp, target)
                linked += 1
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                failed += 1
                print(f"  [erro] {target}: {exc}", file=sys.stderr)
    return linked, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help="venvs (default: */.venv no cwd)")
    parser.add_argument("--apply", action="store_true", help="aplicar (sem isto é dry-run)")
    parser.add_argument(
        "--min-size", type=int, default=MIN_SIZE_DEFAULT, help="tamanho mínimo em bytes (default 1 MiB)"
    )
    args = parser.parse_args()

    paths = args.paths or sorted(Path.cwd().glob("*/.venv"))
    paths = [p for p in paths if p.is_dir()]
    if not paths:
        print("nenhuma venv encontrada", file=sys.stderr)
        return 1
    modo = "APLICAR" if args.apply else "dry-run"
    print(f"venvs: {len(paths)} · mínimo {args.min_size // (1 << 20)} MiB · modo {modo}")

    candidates = _collect(paths, args.min_size)
    n_files = sum(len(v) for v in candidates.values())
    print(f"candidatos por tamanho: {n_files} ficheiros em {len(candidates)} tamanhos partilhados; a fazer hash…")
    groups = _group_by_hash(candidates)
    bytes_saved = _reclaimable(groups)
    print(f"grupos idênticos: {len(groups)} · duplicados: {sum(len(v) - 1 for v in groups.values())} ficheiros")
    print(f"poupança estimada: {bytes_saved / (1 << 30):.1f} GiB")

    if not args.apply:
        print("dry-run — nada alterado (usar --apply)")
        return 0
    linked, failed = _apply(groups)
    print(f"hardlinks criados: {linked} · falhas: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
