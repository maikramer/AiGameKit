"""
Monitoramento de VRAM em tempo real para otimização de quantização.

Uso:
    from aigamekit_shared.vram_monitor import VRAMMonitor

    monitor = VRAMMonitor()
    monitor.start()

    # ... código que usa GPU ...

    stats = monitor.stop()
    print(f"VRAM pico: {stats['peak_allocated_mb']:.1f} MB")
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class VRAMSnapshot:
    """Snapshot de uso de VRAM em um momento."""

    timestamp: float
    allocated_mb: float
    reserved_mb: float
    free_mb: float
    total_mb: float


@dataclass
class VRAMStats:
    """Estatísticas de uso de VRAM durante um período."""

    snapshots: list[VRAMSnapshot] = field(default_factory=list)
    peak_allocated_mb: float = 0.0
    peak_reserved_mb: float = 0.0
    min_free_mb: float = float("inf")
    avg_allocated_mb: float = 0.0

    def add_snapshot(self, snapshot: VRAMSnapshot) -> None:
        self.snapshots.append(snapshot)
        self.peak_allocated_mb = max(self.peak_allocated_mb, snapshot.allocated_mb)
        self.peak_reserved_mb = max(self.peak_reserved_mb, snapshot.reserved_mb)
        self.min_free_mb = min(self.min_free_mb, snapshot.free_mb)

        # Recalcular média
        if self.snapshots:
            self.avg_allocated_mb = sum(s.allocated_mb for s in self.snapshots) / len(self.snapshots)


class VRAMMonitor:
    """Monitora VRAM em tempo real durante a execução."""

    def __init__(self, interval_sec: float = 0.5, device: int = 0):
        self.interval = interval_sec
        self.device = device
        self._stats = VRAMStats()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._callbacks: list[Callable[[VRAMSnapshot], None]] = []

    def on_snapshot(self, callback: Callable[[VRAMSnapshot], None]) -> None:
        """Registra um callback para ser chamado a cada snapshot."""
        self._callbacks.append(callback)

    def _get_snapshot(self) -> VRAMSnapshot | None:
        """Captura estado atual da VRAM."""
        try:
            import torch
        except ImportError:
            return None

        if not torch.cuda.is_available():
            return None

        try:
            torch.cuda.synchronize(self.device)
            allocated = torch.cuda.memory_allocated(self.device) / (1024**2)
            reserved = torch.cuda.memory_reserved(self.device) / (1024**2)
            total = torch.cuda.get_device_properties(self.device).total_memory / (1024**2)
            free = total - allocated

            return VRAMSnapshot(
                timestamp=time.time(),
                allocated_mb=allocated,
                reserved_mb=reserved,
                free_mb=free,
                total_mb=total,
            )
        except Exception:
            return None

    def _monitor_loop(self) -> None:
        """Loop principal de monitoramento."""
        while not self._stop_event.is_set():
            snapshot = self._get_snapshot()
            if snapshot:
                self._stats.add_snapshot(snapshot)
                for callback in self._callbacks:
                    with contextlib.suppress(Exception):
                        callback(snapshot)
            time.sleep(self.interval)

    def start(self) -> None:
        """Inicia o monitoramento em thread separada."""
        if self._thread is not None:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> VRAMStats:
        """Para o monitoramento e retorna estatísticas."""
        if self._thread is None:
            return self._stats

        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None

        return self._stats

    def get_current(self) -> VRAMSnapshot | None:
        """Retorna snapshot atual (não necessita de start())."""
        return self._get_snapshot()

    def print_summary(self) -> None:
        """Imprime resumo das estatísticas."""
        stats = self._stats
        if not stats.snapshots:
            print("[VRAMMonitor] Nenhum dado coletado")
            return

        print("\n" + "=" * 60)
        print("RESUMO DE VRAM")
        print("=" * 60)
        print(f"  Pico alocado:    {stats.peak_allocated_mb:7.1f} MB")
        print(f"  Pico reservado:  {stats.peak_reserved_mb:7.1f} MB")
        print(f"  Mínimo livre:    {stats.min_free_mb:7.1f} MB")
        print(f"  Média alocado:   {stats.avg_allocated_mb:7.1f} MB")
        print(f"  Snapshots:       {len(stats.snapshots)}")

        # Análise de margem
        if stats.snapshots:
            total = stats.snapshots[0].total_mb
            utilization = (stats.peak_allocated_mb / total) * 100
            print(f"  Utilização:      {utilization:.1f}% do total ({total:.0f} MB)")

            if stats.min_free_mb < 500:
                print("  ⚠️  ALERTA: Pouca VRAM livre no pico!")
            elif utilization > 90:
                print("  ⚠️  ALERTA: Alta utilização de VRAM!")
            else:
                print("  ✅ VRAM em níveis saudáveis")
        print("=" * 60)


def find_quantization_sweet_spot(
    test_load_model_fn: Callable[[str], object],
    quant_modes: list[str],
    target_vram_mb: float = 5500,
) -> dict[str, VRAMStats | None]:
    """
    Testa múltiplos modos de quantização e encontra o ideal.

    Args:
        test_load_model_fn: Função que recebe modo de quant e carrega o modelo
        quant_modes: Lista de modos a testar (ex: ['int8', 'uint8', 'fp8'])
        target_vram_mb: Meta de VRAM máxima (padrão: 5500 para 6GB GPUs)

    Returns:
        Dict com estatísticas por modo
    """
    results: dict[str, VRAMStats | None] = {}

    try:
        import torch
    except ImportError:
        torch = None

    print("=" * 70)
    print("BUSCANDO SWEET SPOT DE QUANTIZAÇÃO")
    print(f"Target VRAM: {target_vram_mb:.0f} MB")
    print("=" * 70)

    for mode in quant_modes:
        print(f"\n🧪 Testando modo: {mode}")
        print("-" * 70)

        monitor = VRAMMonitor(interval_sec=0.2)

        try:
            # Limpar VRAM antes do teste
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

            monitor.start()
            model = test_load_model_fn(mode)

            # Se chegou aqui, carregou com sucesso
            stats = monitor.stop()
            results[mode] = stats

            # Verificar se atende ao target
            if stats.peak_allocated_mb <= target_vram_mb:
                print(f"  ✅ ATENDE AO TARGET ({stats.peak_allocated_mb:.0f} MB <= {target_vram_mb:.0f} MB)")
            else:
                print(f"  ❌ EXCEDE TARGET ({stats.peak_allocated_mb:.0f} MB > {target_vram_mb:.0f} MB)")

            # Limpar
            del model
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()

        except RuntimeError as e:
            monitor.stop()
            if "out of memory" in str(e).lower():
                print("  💥 OOM - Não cabe nesta configuração")
                results[mode] = None
            else:
                print(f"  ❌ Erro: {e}")
                results[mode] = None

            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()

    # Resumo final
    print("\n" + "=" * 70)
    print("RESULTADO FINAL")
    print("=" * 70)

    valid_modes = {k: v for k, v in results.items() if v is not None}
    if not valid_modes:
        print("❌ Nenhum modo funcionou! Considere reduzir resolução.")
        return results

    # Ordenar por VRAM pico
    sorted_modes = sorted(valid_modes.items(), key=lambda x: x[1].peak_allocated_mb, reverse=True)

    print("\nModos válidos (ordem de maior para menor qualidade/VRAM):")
    for mode, stats in sorted_modes:
        status = "✅" if stats.peak_allocated_mb <= target_vram_mb else "⚠️"
        print(f"  {status} {mode:10s}: {stats.peak_allocated_mb:6.1f} MB (pico)")

    # Recomendar melhor modo que atende ao target
    qualifying = [(m, s) for m, s in sorted_modes if s.peak_allocated_mb <= target_vram_mb]
    if qualifying:
        # Pegar o primeiro (maior qualidade que ainda atende ao target)
        best_mode, best_stats = qualifying[0]
        print(f"\n🎯 RECOMENDAÇÃO: {best_mode}")
        print(f"   VRAM pico: {best_stats.peak_allocated_mb:.1f} MB")
        print(f"   Margem livre: {best_stats.min_free_mb:.1f} MB")
    else:
        # Nenhum atende, pegar o mais próximo
        closest = min(valid_modes.items(), key=lambda x: abs(x[1].peak_allocated_mb - target_vram_mb))
        print("\n⚠️  Nenhum modo atendeu ao target.")
        print(f"   Mais próximo: {closest[0]} ({closest[1].peak_allocated_mb:.1f} MB)")

    return results
