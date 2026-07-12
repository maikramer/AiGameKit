"""Adapters de backends — normalizam a API heterogénea das tools num contrato canónico.

Cada módulo aqui exporta uma classe ``Adapter`` (sem argumentos no construtor)
que implementa o contrato definido em ``base.BackendAdapter``:

    load(**kwargs)   → model object (pipeline/gerador carregado)
    generate(model, request: dict) → dict (resposta com "status")
    unload(model)    → None (liberta VRAM)

O adapter encapsula a forma específica como cada tool carrega/usa/liberta o
seu modelo (warmup()/load()/_load_*/context-managed). O BackendManager chama
sempre o mesmo contrato, independentemente da tool.
"""
