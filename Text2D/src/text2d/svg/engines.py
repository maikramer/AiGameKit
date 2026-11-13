"""Registo de motores SVG e resolução do ``auto``."""

from __future__ import annotations

from typing import Any

from .options import SvgEngineUnavailable, SvgOptions, resolve_engine
from .tracer import VtracerEngine, trace_image


def vtracer_available() -> bool:
    try:
        import vtracer  # noqa: F401
    except ImportError:
        return False
    return True


def starvector_available(*, require_cached: bool = False) -> bool:
    """True se o StarVector pode correr neste ambiente.

    ``require_cached`` é usado pelo ``auto`` para não disparar um download de
    ~5 GB sem o utilizador pedir; um ``--svg-engine starvector`` explícito não
    passa por aqui e pode descarregar.
    """

    try:
        import torch

        if not torch.cuda.is_available():
            return False
        from aigamekit_shared.model_download import is_model_cached

        from .starvector import DEFAULT_MODEL_ID

        return not require_cached or is_model_cached(DEFAULT_MODEL_ID)
    except Exception:
        return False


def engine_available(name: str) -> bool:
    if name == "vtracer":
        return vtracer_available()
    if name == "starvector":
        return starvector_available(require_cached=False)
    return False


def get_engine(
    name: str,
    *,
    model_id: str | None = None,
    device: str = "auto",
    max_tokens: int = 4000,
    verbose: bool = False,
    cache_dir: str | None = None,
) -> Any:
    """Cria o motor pedido (sem carregar pesos; chama ``warmup``/``load``)."""

    if name == "vtracer":
        return VtracerEngine()
    if name == "starvector":
        from .starvector import StarVectorEngine

        return StarVectorEngine(
            model_id=model_id,
            device=device,
            max_tokens=max_tokens,
            verbose=verbose,
            cache_dir=cache_dir,
        )
    raise ValueError(f"motor SVG desconhecido: {name!r}")


def resolve_and_get_engine(
    opts: SvgOptions,
    *,
    category: str | None = None,
    verbose: bool = False,
) -> tuple[str, Any]:
    """Resolve ``auto`` e devolve ``(nome_resolvido, motor)``."""

    resolved = resolve_engine(opts.engine, category=category)
    if resolved == "starvector" and opts.engine == "auto" and not starvector_available(require_cached=True):
        resolved = "vtracer"
    if resolved == "vtracer" and not vtracer_available():
        if opts.engine == "starvector":
            raise SvgEngineUnavailable("StarVector indisponível neste ambiente")
        raise SvgEngineUnavailable(
            "nem VTracer nem StarVector estão disponíveis — instala `vtracer` "
            "ou corre `text2d doctor`."
        )
    return resolved, get_engine(
        resolved,
        model_id=opts.model_id,
        device=opts.device,
        max_tokens=opts.max_tokens,
        verbose=verbose,
    )


def render_svg(
    image: Any,
    opts: SvgOptions,
    *,
    engine: Any | None = None,
    category: str | None = None,
    verbose: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Vectoriza com o motor indicado e devolve ``(raw_svg, meta)``."""

    resolved = opts.engine
    if resolved == "auto":
        resolved = getattr(engine, "name", None) if engine is not None else resolve_engine(
            opts.engine, category=category
        )
    if engine is None:
        resolved, engine = resolve_and_get_engine(
            opts, category=category, verbose=verbose
        )

    if resolved == "vtracer":
        raw = trace_image(image, opts)
        return raw, {"engine": "vtracer", "params": engine_params(opts)}
    if resolved == "starvector":
        if getattr(engine, "model", None) is None:
            engine.warmup()
        raw = engine.vectorize(image, opts)
        return raw, {
            "engine": "starvector",
            "model_id": getattr(engine, "model_id", None),
            "device": getattr(engine, "device", None),
            "dtype": str(getattr(engine, "dtype", "")),
        }
    raise ValueError(f"motor SVG desconhecido: {resolved!r}")


def engine_params(opts: SvgOptions) -> dict[str, Any]:
    from .tracer import trace_params

    return trace_params(opts)
