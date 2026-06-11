import torch
from torch import nn, FloatTensor, LongTensor
import numpy as np
from torch.nn.functional import pad
from typing import Dict, List, Union
from transformers import AutoModelForCausalLM, AutoConfig, LogitsProcessor, LogitsProcessorList

from .spec import ModelSpec, ModelInput
from .parse_encoder import MAP_MESH_ENCODER, get_mesh_encoder

from ..tokenizer.spec import TokenizerSpec, DetokenizeOutput
from copy import deepcopy

class LegacyVocabSwitchingLogitsProcessor(LogitsProcessor):
    """Versão original (referência): FSM em Python + sync GPU→CPU por token.

    O(L) por beam por passo → O(L²·beams) no total. Mantida só para testes de
    paridade com a versão vectorizada abaixo.
    """

    def __init__(self, tokenizer: TokenizerSpec, start_tokens: LongTensor):
        self.tokenizer = tokenizer
        self.start_tokens = start_tokens
        assert start_tokens.ndim == 1

    def __call__(self, input_ids: LongTensor, scores: FloatTensor) -> FloatTensor:
        # input_ids shape: (batch_size, seq_len)
        for batch_idx, sequence in enumerate(input_ids):
            mask = torch.full_like(scores[batch_idx], float('-inf'))
            sequence = torch.cat([self.start_tokens, sequence])
            tokens = self.tokenizer.next_posible_token(ids=sequence.detach().cpu().numpy())
            mask[tokens] = 0
            scores[batch_idx] = scores[batch_idx] + mask
        return scores


class VocabSwitchingLogitsProcessor(LogitsProcessor):
    """FSM de gramática do esqueleto vectorizado em GPU (sem sync por passo).

    O estado do FSM tem forma fechada sobre sequências válidas-sob-máscara:
    - d = nº de tokens discretos (coordenadas) no fim da sequência
    - d > 0  → estado por d % 3 (joint_2 / joint_3 / branch_or_part_or_joint)
    - d == 0 → pelo último token: bos → cls_or_part_or_joint; cls →
      part_or_joint; parte com cls presente e nenhum discreto ainda →
      part_or_joint (self-loop pós-cls); caso contrário → expect_joint.

    Sequências com tokens pós-eos (beams terminados) divergem do FSM legado,
    mas os scores desses beams são ignorados pelo ``generate``.
    """

    _STATES = (
        'expect_bos',
        'expect_cls_or_part_or_joint',
        'expect_part_or_joint',
        'expect_joint',
        'expect_joint_2',
        'expect_joint_3',
        'expect_branch_or_part_or_joint',
    )

    def __init__(self, tokenizer: TokenizerSpec, start_tokens: LongTensor):
        self.tokenizer = tokenizer
        self.start_tokens = start_tokens
        assert start_tokens.ndim == 1
        self._mask_table: Union[FloatTensor, None] = None
        self._cls_lut: Union[torch.Tensor, None] = None

    def _build(self, device: torch.device, scores_vocab: int) -> None:
        tok = self.tokenizer
        table = torch.full((len(self._STATES), scores_vocab), float('-inf'), device=device)
        for si, state in enumerate(self._STATES):
            allowed = torch.tensor(tok.possible_tokens_for_state(state), dtype=torch.long, device=device)
            table[si, allowed] = 0.0
        self._mask_table = table

        cls_lut = torch.zeros(scores_vocab, dtype=torch.bool, device=device)
        cls_lut[tok.token_id_cls_none] = True
        for v in tok.cls_token_id.values():
            cls_lut[v] = True
        self._cls_lut = cls_lut

    def __call__(self, input_ids: LongTensor, scores: FloatTensor) -> FloatTensor:
        if self._mask_table is None or self._mask_table.device != scores.device:
            self._build(scores.device, scores.shape[-1])
        tok = self.tokenizer
        B = input_ids.shape[0]
        device = input_ids.device

        start = self.start_tokens.to(device).unsqueeze(0).expand(B, -1)
        full = torch.cat([start, input_ids], dim=1)
        L = full.shape[1]

        clamped = full.clamp(max=self._cls_lut.shape[0] - 1)
        disc = full < tok.num_discrete
        non_disc = ~disc
        pos = torch.arange(1, L + 1, device=device)
        # posição do último token não-discreto (start contém sempre bos)
        last_nd = (non_disc * pos).max(dim=1).values - 1
        d = (L - 1) - last_nd
        last_tok = clamped.gather(1, last_nd.unsqueeze(1)).squeeze(1)

        is_bos = last_tok == tok.token_id_bos
        is_cls = self._cls_lut[last_tok]
        has_disc = disc.any(dim=1)
        has_cls = (self._cls_lut[clamped] & non_disc).any(dim=1)
        # parte logo após cls (sem nenhum discreto ainda) fica no self-loop pós-cls
        part_state = torch.where(
            (~has_disc) & has_cls,
            torch.full_like(last_nd, 2),  # expect_part_or_joint
            torch.full_like(last_nd, 3),  # expect_joint
        )
        cat_state = torch.where(is_bos, 1, torch.where(is_cls, 2, part_state))

        dm = d % 3
        d_state = torch.where(dm == 1, 4, torch.where(dm == 2, 5, 6))
        state = torch.where(d == 0, cat_state, d_state)

        return scores + self._mask_table[state]

class UniRigAR(ModelSpec):
    
    def process_fn(self, batch: List[ModelInput]) -> List[Dict]:
        if batch[0].joints is None: # predict
            return [{} for _ in range(len(batch))]
        max_length = 0
        for b in batch:
            max_length = max(max_length, b.tokens.shape[0])
        res = [{
            'input_ids': np.pad(b.tokens, ((0, max_length-b.tokens.shape[0])), 'constant', constant_values=b.pad),
            'attention_mask': np.pad(torch.ones(b.tokens.shape[0]), ((0, max_length - b.tokens.shape[0])), 'constant', constant_values=0.),
        } for b in batch]
        return res
    
    def __init__(self, llm, mesh_encoder, **kwargs):
        super().__init__()
        self.tokenizer: TokenizerSpec = kwargs.get('tokenizer')
        self.vocab_size = self.tokenizer.vocab_size
        
        _d = llm.copy()
        _d['vocab_size'] = self.tokenizer.vocab_size
        if _d.get("_attn_implementation") == "flash_attention_2":
            _d["_attn_implementation"] = "sdpa"
        llm_config = AutoConfig.from_pretrained(**_d)
        llm_config.torch_dtype = torch.bfloat16
        # Force enable pre_norm
        llm_config.pre_norm = True
        self.transformer = AutoModelForCausalLM.from_config(config=llm_config)
        
        self.hidden_size = llm.hidden_size
        
        self.mesh_encoder = get_mesh_encoder(**mesh_encoder)
        
        if (
            isinstance(self.mesh_encoder, MAP_MESH_ENCODER.michelangelo) or
            isinstance(self.mesh_encoder, MAP_MESH_ENCODER.michelangelo_encoder)
        ):
            self.output_proj = nn.Linear(self.mesh_encoder.width, self.hidden_size)
        else:
            raise NotImplementedError()
            
    def encode_mesh_cond(self, vertices: FloatTensor, normals: FloatTensor) -> FloatTensor:
        assert not torch.isnan(vertices).any()
        assert not torch.isnan(normals).any()
        if (
            isinstance(self.mesh_encoder, MAP_MESH_ENCODER.michelangelo) or
            isinstance(self.mesh_encoder, MAP_MESH_ENCODER.michelangelo_encoder)
        ):
            if (len(vertices.shape) == 3):
                shape_embed, latents, token_num, pre_pc = self.mesh_encoder.encode_latents(pc=vertices, feats=normals)
            else:
                shape_embed, latents, token_num, pre_pc = self.mesh_encoder.encode_latents(pc=vertices.unsqueeze(0), feats=normals.unsqueeze(0))
            latents = self.output_proj(latents)
            return latents
        else:
            raise NotImplementedError()
    
    def training_step(self, batch: Dict) -> Dict[str, FloatTensor]:
        cond = self.encode_mesh_cond(vertices=batch['vertices'], normals=batch['normals']).to(dtype=self.transformer.dtype)
        B = cond.shape[0]
        input_ids: LongTensor = batch['input_ids']
        inputs_embeds = self.transformer.get_input_embeddings()(input_ids).to(dtype=self.transformer.dtype)
        
        inputs_embeds = torch.concat([cond, inputs_embeds], dim=1)
        
        attention_mask = batch['attention_mask']
        # add attention to condition
        attention_mask = pad(attention_mask, (cond.shape[1], 0, 0, 0), value=1.)
        output = self.transformer(
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
            use_cache=False,
        )
        
        # (B, L, vocab_size)
        logit = output.logits[:, cond.shape[1]:].reshape(B, -1, self.vocab_size)
        # compute loss with shift one-token right
        device = logit.device # (B, n, num_discrete)
        logit = logit[:, :-1] # (B, n)
        num_discrete = self.tokenizer.num_discrete
        s = torch.nn.functional.softmax(logit, dim=-1)
        
        label = input_ids[:, 1:].clone() # (B, n)
        mask = label < num_discrete
        dis = torch.arange(num_discrete, device=device).view(1, 1, -1) # (B, n, num_discrete)
        dis = (dis - label.unsqueeze(2).repeat(1, 1, num_discrete)).type(torch.float32) / num_discrete
        dis_loss = (s[:, :, :num_discrete] * torch.abs(dis))[mask].sum() / 50 # ignore padding loss
        
        label[attention_mask[:, cond.shape[1] + 1:]==0] = -100
        
        assert not torch.isnan(logit).any(), logit
        ce_loss = nn.functional.cross_entropy(logit.permute(0, 2, 1), label)
        return {
            'ce_loss': ce_loss,
            'dis_loss': dis_loss,
        }
    
    def forward(self, data: Dict):
        return self.training_step(data=data)
    
    @torch.no_grad()
    def generate(
        self,
        vertices: FloatTensor,
        normals: FloatTensor,
        cls: Union[str, None]=None,
        **kwargs,
    ) -> DetokenizeOutput:
        '''
        Do not support batch!
        '''
        cond = self.encode_mesh_cond(vertices=vertices, normals=normals).to(dtype=self.transformer.dtype)
        
        start_tokens = [self.tokenizer.bos]
        
        if cls is not None:
            start_tokens.append(self.tokenizer.cls_name_to_token(cls=cls))
        start_tokens = torch.tensor(start_tokens).to(cond.device)
        start_embed = self.transformer.get_input_embeddings()(
            start_tokens.unsqueeze(0)
        ).to(dtype=self.transformer.dtype)
        cond = torch.cat([cond, start_embed], dim=1)
        
        processor = VocabSwitchingLogitsProcessor(
            tokenizer=self.tokenizer,
            start_tokens=start_tokens,
        )
        results = self.transformer.generate(
            inputs_embeds=cond,
            bos_token_id=self.tokenizer.bos,
            eos_token_id=self.tokenizer.eos,
            pad_token_id=self.tokenizer.pad,
            logits_processor=LogitsProcessorList([processor]),
            **kwargs,
        )
        output_ids = results[0, :]
        for token in reversed(start_tokens):
            output_ids = pad(output_ids, (1, 0), value=token)
        output_ids = output_ids.detach().cpu().numpy()
        
        res = self.tokenizer.detokenize(ids=output_ids)
        return res
    
    def predict_step(self, batch: Dict, no_cls: bool=False):
        vertices: FloatTensor   = batch['vertices']
        normals : FloatTensor   = batch['normals']
        paths   : List[str]     = batch['path']
        cls = batch['cls']
        generate_kwargs = deepcopy(batch['generate_kwargs'])

        no_cls = generate_kwargs.get('no_cls', False)
        use_dir_cls = generate_kwargs.get('use_dir_cls', False)
        assign_cls = generate_kwargs.get('assign_cls', None)

        generate_kwargs.pop('no_cls', None)
        generate_kwargs.pop('use_dir_cls', None)
        generate_kwargs.pop('assign_cls', None)

        if vertices.dim() == 2:
            vertices = vertices.unsqueeze(0)
            normals  = normals.unsqueeze(0)
        outputs = []
        for i in range(vertices.shape[0]):
            if no_cls:
                _cls = None
            elif assign_cls is not None:
                _cls = assign_cls
            elif use_dir_cls:
                _cls = paths[i].removeprefix('./').split('/')[0]
            else:
                _cls = cls[i]
            res = self.generate(vertices=vertices[i], normals=normals[i], cls=_cls, **generate_kwargs)
            outputs.append(res)
        return outputs