"""Testes do AffinityScheduler — prioridade + cuts de afinidade VRAM."""

from __future__ import annotations

from modelserver import protocol as P
from modelserver.job_queue import Job
from modelserver.scheduler import AffinityScheduler


def _job(backend: str, seq: int, *, priority: str = P.PRIORITY_INTERACTIVE, cuts: int = 0) -> Job:
    j = Job(
        job_id=f"j-{seq}",
        backend=backend,
        request={"prompt": "x"},
        priority=priority,
        seq=seq,
    )
    j.affinity_cuts = cuts
    return j


class TestAffinityScheduler:
    def test_empty_queue(self) -> None:
        assert AffinityScheduler().pick_next([], {"text3d"}) is None

    def test_fifo_when_nothing_loaded(self) -> None:
        jobs = [_job("alpha", 1), _job("beta", 2)]
        picked = AffinityScheduler().pick_next(jobs, loaded=set())
        assert picked is not None
        assert picked.backend == "alpha"

    def test_affinity_skips_cold_head_for_hot_job(self) -> None:
        head = _job("alpha", 1)
        hot = _job("text3d", 2)
        picked = AffinityScheduler(max_cuts=3).pick_next([head, hot], loaded={"text3d"})
        assert picked is hot
        assert head.affinity_cuts == 1

    def test_affinity_stops_after_max_cuts(self) -> None:
        head = _job("alpha", 1, cuts=3)
        hot = _job("text3d", 2)
        picked = AffinityScheduler(max_cuts=3).pick_next([head, hot], loaded={"text3d"})
        assert picked is head
        assert head.affinity_cuts == 3  # não incrementa quando força

    def test_third_cut_still_allows_skip_fourth_forces(self) -> None:
        sched = AffinityScheduler(max_cuts=3)
        head = _job("alpha", 1)
        hot = _job("text3d", 2)
        for expected_cuts in (1, 2, 3):
            picked = sched.pick_next([head, hot], loaded={"text3d"})
            assert picked is hot
            assert head.affinity_cuts == expected_cuts
        # 4.ª tentativa: force head
        picked = sched.pick_next([head, hot], loaded={"text3d"})
        assert picked is head

    def test_interactive_before_batch(self) -> None:
        batch = _job("text3d", 1, priority=P.PRIORITY_BATCH)
        interactive = _job("alpha", 2, priority=P.PRIORITY_INTERACTIVE)
        # text3d loaded — affinity would prefer batch, but interactive band wins
        picked = AffinityScheduler().pick_next([batch, interactive], loaded={"text3d"})
        assert picked is interactive

    def test_affinity_only_within_priority_band(self) -> None:
        # interactive cold head + batch hot — must NOT skip interactive for batch
        head = _job("alpha", 1, priority=P.PRIORITY_INTERACTIVE)
        batch_hot = _job("text3d", 2, priority=P.PRIORITY_BATCH)
        picked = AffinityScheduler().pick_next([head, batch_hot], loaded={"text3d"})
        assert picked is head
        assert head.affinity_cuts == 0

    def test_head_already_hot_taken_immediately(self) -> None:
        head = _job("text3d", 1)
        other = _job("alpha", 2)
        picked = AffinityScheduler().pick_next([head, other], loaded={"text3d"})
        assert picked is head
        assert head.affinity_cuts == 0

    def test_skips_cancel_requested_and_non_queued(self) -> None:
        a = _job("alpha", 1)
        a.cancel_requested = True
        b = _job("beta", 2)
        b.state = P.JOB_RUNNING
        c = _job("gamma", 3)
        picked = AffinityScheduler().pick_next([a, b, c], loaded=set())
        assert picked is c

    def test_all_ineligible_returns_none(self) -> None:
        a = _job("alpha", 1)
        a.cancel_requested = True
        assert AffinityScheduler().pick_next([a], loaded=set()) is None

    def test_picks_oldest_hot_candidate(self) -> None:
        head = _job("cold", 1)
        hot_old = _job("text3d", 2)
        hot_new = _job("text3d", 3)
        picked = AffinityScheduler().pick_next([head, hot_old, hot_new], loaded={"text3d"})
        assert picked is hot_old
        assert head.affinity_cuts == 1

    def test_max_cuts_zero_forces_head(self) -> None:
        head = _job("cold", 1)
        hot = _job("text3d", 2)
        picked = AffinityScheduler(max_cuts=0).pick_next([head, hot], loaded={"text3d"})
        assert picked is head
        assert head.affinity_cuts == 0

    def test_loaded_fn_refreshed(self) -> None:
        head = _job("cold", 1)
        hot = _job("text3d", 2)
        picked = AffinityScheduler().pick_next([head, hot], loaded=set(), loaded_fn=lambda: {"text3d"})
        assert picked is hot
        assert head.affinity_cuts == 1

    def test_is_hot_requires_shape_match(self) -> None:
        """Backend loaded com shape diferente ≠ hot — não saltar cold head."""
        head = _job("alpha", 1)
        same_backend_wrong_shape = _job("text3d", 2)
        same_backend_wrong_shape.request = {"max_num_view": 4}

        def _hot(job: Job) -> bool:
            # Só hot se pedir 6 views (shape "certo").
            return job.backend == "text3d" and job.request.get("max_num_view") == 6

        picked = AffinityScheduler().pick_next([head, same_backend_wrong_shape], loaded={"text3d"}, is_hot=_hot)
        assert picked is head
        assert head.affinity_cuts == 0

        same_backend_ok = _job("text3d", 3)
        same_backend_ok.request = {"max_num_view": 6}
        picked = AffinityScheduler().pick_next(
            [head, same_backend_wrong_shape, same_backend_ok], loaded={"text3d"}, is_hot=_hot
        )
        assert picked is same_backend_ok
        assert head.affinity_cuts == 1

    def test_unknown_priority_rank_last(self) -> None:
        weird = _job("alpha", 1, priority="turbo")
        batch = _job("beta", 2, priority=P.PRIORITY_BATCH)
        picked = AffinityScheduler().pick_next([weird, batch], loaded=set())
        assert picked is batch
