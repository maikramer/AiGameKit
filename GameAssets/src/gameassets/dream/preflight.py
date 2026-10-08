"""Pré-checks do ``gameassets dream`` — avisos claros ANTES de começar o trabalho.

Sem estes avisos, o utilizador descobre a meio do run que o céu foi saltado
(``skymap2d not found``) ou que o plano saiu keyword-based porque não há chave
LLM. Aqui essas condições são detetadas no arranque, com a correção e a flag
``--no-*`` correspondente em cada linha. Nunca bloqueia — o dream degrada como
sempre degradou, mas agora com conhecimento prévio.
"""

from __future__ import annotations

import os

from ..doctor import ollama_reachable, tool_bin


def llm_issues(provider: str, api_key: str | None) -> list[str]:
    if provider == "openai":
        if not (api_key or os.environ.get("OPENAI_API_KEY")):
            return [
                "LLM: sem OPENAI_API_KEY — o plano vai sair keyword-based (fallback genérico). "
                "Define OPENAI_API_KEY, ou usa um ollama local: --llm-provider ollama"
            ]
    elif provider == "ollama":
        if not ollama_reachable():
            return [
                "LLM: ollama não responde (OLLAMA_HOST) — o plano vai sair fallback. "
                "Confirma que o ollama está a correr, ou usa OPENAI_API_KEY"
            ]
    elif provider == "huggingface" and not os.environ.get("HF_TOKEN"):
        return ["LLM: sem HF_TOKEN para o provider huggingface — o plano vai sair fallback"]
    return []


def _tool_issues(*, with_sky: bool, with_audio: bool, terrain: bool | None) -> list[str]:
    issues: list[str] = []
    if with_sky and tool_bin("skymap2d") is None:
        issues.append("sky: skymap2d em falta — o céu vai ser saltado. ./install.sh skymap2d (ou --no-sky)")
    if with_audio and tool_bin("text2sound") is None:
        issues.append("áudio: text2sound em falta — os sons vão ser saltados. ./install.sh text2sound (ou --no-audio)")
    # terrain=None é "auto": o plano LLM pode ativar — avisar já quando a tool falta.
    if terrain is not False and tool_bin("terrain3d") is None:
        issues.append(
            "terreno: terrain3d em falta — se o plano ativar terreno, o passo salta. "
            "./install.sh terrain3d (ou --no-terrain)"
        )
    return issues


def dream_preflight_issues(
    *,
    provider: str,
    api_key: str | None = None,
    with_sky: bool = True,
    with_audio: bool = True,
    terrain: bool | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Linhas de aviso para o arranque do dream (vazio = tudo pronto).

    Em ``dry_run`` não há geração — os checks de tools opcionais não aplicam.
    """
    issues = llm_issues(provider, api_key)
    if not dry_run:
        issues += _tool_issues(with_sky=with_sky, with_audio=with_audio, terrain=terrain)
    return issues
