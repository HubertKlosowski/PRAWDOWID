import os
from functools import lru_cache
from threading import Lock

from .models import EMBEDDING_SIZE

MODEL = "Qwen/Qwen3-Embedding-4B"
_MODEL_LOCK = Lock()


@lru_cache(maxsize=1)
def load_model():
    import torch
    from transformers import AutoModel, AutoTokenizer

    path = os.getenv("QWEN_EMBEDDING_PATH", MODEL)
    device = os.getenv("QWEN_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(path, padding_side="left")
    model = AutoModel.from_pretrained(path, dtype="auto").to(device).eval()
    if model.config.hidden_size != EMBEDDING_SIZE:
        raise ValueError("Wymiar modelu jest niezgodny z indeksem.")
    return tokenizer, model


def embed(texts, *, query=False):
    import torch

    with _MODEL_LOCK:
        tokenizer, model = load_model()
    vectors = []
    for start in range(0, len(texts), 4):
        batch = texts[start:start + 4]
        if query:
            instruction = "Retrieve official passages relevant to verifying the following claim."
            batch = [f"Instruct: {instruction}\nQuery: {text}" for text in batch]
        inputs = tokenizer(batch, padding=True, truncation=True, max_length=4096,
                           return_tensors="pt").to(model.device)
        with torch.inference_mode():
            hidden = model(**inputs).last_hidden_state[:, -1].float()
            if not torch.isfinite(hidden).all() or (hidden.norm(dim=1) == 0).any():
                raise ValueError("Model zwrócił nieprawidłowy wektor.")
            vectors.extend(torch.nn.functional.normalize(hidden, dim=1).cpu().tolist())
    return vectors
