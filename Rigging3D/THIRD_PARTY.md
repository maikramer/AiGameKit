# Software de terceiros

## SkinTokens

`src/rigging3d/skintokens/` contém código do
[SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) (MIT), sucessor do
UniRig — modelo autoregressivo unificado (skeleton + skinning numa única
sequência de tokens).

- Paper: [SkinTokens: A Learned Compact Representation for Unified Autoregressive Rigging](https://arxiv.org/abs/2602.04805)
- Pesos: [VAST-AI/SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) (baixados
  automaticamente em runtime, não incluídos no git — ver
  `rigging3d/skintokens_runner.py::ensure_checkpoints`)
- Licença: `src/rigging3d/skintokens/LICENSE`

Incluídos apenas ficheiros de **inferência**; treino, `demo.py` (Gradio) e
`bpy_server.py` (servidor HTTP standalone, substituído por um adapter in-process
neste pacote) mantidos como referência (`demo_reference.py`,
`bpy_server_reference.py`) mas fora do caminho de import de produção.

Patches (ver `docs/RIGGING3D_SKINTOKENS_MIGRATION_PLAN.md` no root do monorepo
para o detalhe completo):

- `model/tokenrig.py` — removido import morto de `flash_attn_func`;
  `attn_implementation`/`dtype` alinhados com a API do `transformers` 5.x.
- `model/skin_vae_model.py` — fallback SDPA (com suporte a GQA) no lugar do
  import obrigatório de `flash_attn`/`flash_attn_interface`.
- `server/spec.py` — `_attn_implementation="sdpa"`, `dtype` (não `torch_dtype`).
- `model/skin_vae/autoencoders/skin_fsq_cvae_model.py` — acesso a `cond_channels`
  via parâmetro local em vez do atributo `self.cond_channels` (deprecated pelo
  `diffusers.ConfigMixin`).
- `model/michelangelo/models/modules/transformer_blocks.py`,
  `model/skin_vae/autoencoders/miche_transformer_blocks.py` — modernizado
  `torch.backends.cuda.sdp_kernel` (deprecated) para
  `torch.nn.attention.sdpa_kernel`.

Nenhum desses patches muda o comportamento numérico do modelo — só removem a
dependência obrigatória de `flash-attn` (nunca instalada neste pacote) e
silenciam deprecations do `transformers`/`torch` 5.x/2.11+.
