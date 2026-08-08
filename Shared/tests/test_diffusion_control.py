"""Hooks de cancel/progress Diffusers (vramd P0)."""

from __future__ import annotations

import pytest

from aigamekit_shared.diffusion_control import GenerationAborted, attach_step_hooks


class TestAttachStepHooks:
    def test_noop_without_hooks(self) -> None:
        kwargs: dict = {"prompt": "x"}
        attach_step_hooks(kwargs, num_inference_steps=4)
        assert "callback_on_step_end" not in kwargs

    def test_progress_callback(self) -> None:
        seen: list[tuple[int, int]] = []
        kwargs: dict = {}
        attach_step_hooks(kwargs, num_inference_steps=4, on_step=lambda i, n: seen.append((i, n)))
        cb = kwargs["callback_on_step_end"]
        cb(object(), 1, 0, {})
        assert seen == [(2, 4)]

    def test_abort_raises(self) -> None:
        kwargs: dict = {}
        attach_step_hooks(kwargs, num_inference_steps=4, should_abort=lambda: True)
        pipe = type("P", (), {"_interrupt": False})()
        with pytest.raises(GenerationAborted):
            kwargs["callback_on_step_end"](pipe, 0, 0, {})
        assert pipe._interrupt is True
