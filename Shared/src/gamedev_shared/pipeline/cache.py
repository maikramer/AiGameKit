"""Content-addressed pipeline caching for GameDev tools.

NOTE: This module is not currently used by any downstream package.
Candidate for removal in a future cleanup pass.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class CacheEntry:
    """A single cache entry."""

    key: str
    input_hash: str
    output_path: str
    timestamp: float
    tool: str


class PipelineCache:
    """Cache pipeline outputs by content hash of inputs."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "gamedev-pipeline"
        self.cache_dir = cache_dir
        self.index_file = cache_dir / "index.json"
        self._entries: dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0
        self._load_index()

    def _load_index(self) -> None:
        """Load cache index from disk."""
        if self.index_file.is_file():
            try:
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
                for key, entry in data.get("entries", {}).items():
                    self._entries[key] = CacheEntry(**entry)
            except (json.JSONDecodeError, OSError, TypeError):
                pass

    def _save_index(self) -> None:
        """Save cache index to disk."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        data = {"entries": {k: asdict(v) for k, v in self._entries.items()}}
        self.index_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def compute_key(tool: str, inputs: dict[str, str | Path | int | float | bool]) -> str:
        """Compute cache key from tool name and input parameters."""
        serializable: dict[str, str] = {}
        for k, v in inputs.items():
            if isinstance(v, Path):
                serializable[k] = str(v.resolve())
            elif isinstance(v, bool):
                serializable[k] = str(v).lower()
            else:
                serializable[k] = str(v)
        raw = json.dumps({"tool": tool, "inputs": serializable}, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def get(self, tool: str, inputs: dict[str, str | Path | int | float | bool]) -> Path | None:
        """Return cached output path if valid, else None."""
        key = self.compute_key(tool, inputs)
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        output = Path(entry.output_path)
        if output.is_file():
            self._hits += 1
            return output
        # Stale entry
        del self._entries[key]
        self._save_index()
        self._misses += 1
        return None

    def put(self, tool: str, inputs: dict[str, str | Path | int | float | bool], output_path: Path) -> None:
        """Store cache entry."""
        key = self.compute_key(tool, inputs)
        self._entries[key] = CacheEntry(
            key=key,
            input_hash=self.compute_key(tool, inputs),
            output_path=str(output_path),
            timestamp=time.time(),
            tool=tool,
        )
        self._save_index()

    def invalidate(self, tool: str | None = None) -> int:
        """Invalidate cache entries. Returns count invalidated."""
        to_remove = [k for k, v in self._entries.items() if tool is None or v.tool == tool]
        for k in to_remove:
            del self._entries[k]
        if to_remove:
            self._save_index()
        return len(to_remove)

    def clear(self) -> int:
        """Clear all cache entries. Returns count removed."""
        count = len(self._entries)
        self._entries.clear()
        if self.index_file.is_file():
            self.index_file.unlink()
        return count

    def stats(self) -> dict[str, int | float]:
        """Return cache statistics."""
        return {
            "entries": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(self._hits + self._misses, 1),
        }


_default_cache: PipelineCache | None = None


def get_cache() -> PipelineCache:
    """Get the default pipeline cache singleton."""
    global _default_cache
    if _default_cache is None:
        _default_cache = PipelineCache()
    return _default_cache


def main() -> None:
    """CLI: python -m gamedev_shared.pipeline.cache [stats|invalidate|clear]"""
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline cache management")
    parser.add_argument("command", choices=["stats", "invalidate", "clear"], nargs="?", default="stats")
    parser.add_argument("--tool", type=str, default=None, help="Tool to invalidate (with invalidate)")
    args = parser.parse_args()

    cache = get_cache()

    if args.command == "stats":
        stats = cache.stats()
        print(f"📦 Pipeline Cache: {stats['entries']} entries")
        print(f"   Hits: {stats['hits']}, Misses: {stats['misses']}")
        print(f"   Hit rate: {stats['hit_rate']:.1%}")
        print(f"   Location: {cache.cache_dir}")
    elif args.command == "invalidate":
        count = cache.invalidate(args.tool)
        tool_msg = f" for '{args.tool}'" if args.tool else ""
        print(f"🗑️  Invalidated {count} entries{tool_msg}")
    elif args.command == "clear":
        count = cache.clear()
        print(f"🗑️  Cleared {count} entries")
