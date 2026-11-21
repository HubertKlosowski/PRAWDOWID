"""Prepare BF16 decoder weights once for oneDNN inference on supported CPUs."""

import torch
from torch import nn


class PackedLinear(nn.Module):
    def __init__(self, linear):
        super().__init__()
        self.register_buffer("weight", torch.ops.mkldnn._reorder_linear_weight(linear.weight.detach(), None))
        self.register_buffer("bias", linear.bias)

    def forward(self, inputs):
        return torch.ops.mkldnn._linear_pointwise(inputs, self.weight, self.bias, "none", [], None)


def prepare_cpu_decoder(model):
    if not (
        model.config.model_type == "qwen3_5_text"
        and model.dtype == torch.bfloat16
        and torch.backends.mkldnn.is_available() and torch.backends.mkldnn.enabled
        and hasattr(torch.cpu, "get_capabilities")
        and torch.cpu.get_capabilities().get("avx512_bf16")
        and hasattr(torch.ops.mkldnn, "_reorder_linear_weight")
        and hasattr(torch.ops.mkldnn, "_linear_pointwise")
    ):
        return

    # Leave embeddings and their tied output projection shared. Process one block
    # at a time so references to the original linear weights can be released.
    for block in model.model.layers:
        for path, layer in list(block.named_modules()):
            if type(layer) is nn.Linear and layer.weight.dtype == torch.bfloat16:
                block.set_submodule(path, PackedLinear(layer))
