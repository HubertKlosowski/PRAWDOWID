"""Local and chat-completions adapters for model inference."""

import json
import os
from functools import lru_cache
from threading import Lock
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


_local_lock = Lock()
DEFAULT_LLM_MODEL = "Qwen/Qwen3.5-4B"


@lru_cache(maxsize=1)
def load_local_model(name):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name, local_files_only=True)
    device = os.getenv("PRAWDOWID_LLM_DEVICE")
    if not device:
        device = "cpu"
        if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] >= 10 * 1024**3:
            device = "cuda"
    model = AutoModelForCausalLM.from_pretrained(
        name, local_files_only=True, dtype="auto"
    )
    model = model.to(device)
    if model.device.type == "cpu" and os.getenv("PRAWDOWID_CPU_PREPACK", "1") == "1":
        from .cpu_inference import prepare_cpu_decoder

        prepare_cpu_decoder(model)
    return tokenizer, model.eval()


def _local_completion(name, messages):
    import torch

    with _local_lock, torch.inference_mode():
        tokenizer, model = load_local_model(name)
        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
            return_tensors="pt",
            return_dict=True,
        ).to(model.device)
        tokens = model.generate(**inputs, do_sample=False, max_new_tokens=1800)
        output = tokens[0, inputs["input_ids"].shape[1] :]
        return tokenizer.decode(output, skip_special_tokens=True)


def request_completion(system_prompt, data):
    url = os.environ.get("PRAWDOWID_LLM_URL", "")
    model = os.environ.get("PRAWDOWID_LLM_MODEL", DEFAULT_LLM_MODEL)
    messages = [
        {"role": "system", "content": system_prompt},
        # Keep literal Qwen chat markers in untrusted values from becoming tokens.
        {"role": "user", "content": json.dumps(data, ensure_ascii=False).replace("<", r"\u003c")},
    ]
    if url:
        return _remote_completion(url, model, messages)
    return json.loads(_local_completion(model, messages))


def _remote_completion(url, model, messages):
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("Niepoprawna konfiguracja endpointu.")
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 2200}
    headers = {"Content-Type": "application/json"}
    if key := os.environ.get("PRAWDOWID_LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(url, data=json.dumps(body).encode(), headers=headers)
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        payload = response.read(131073)
    if len(payload) > 131072:
        raise ValueError("Za duża odpowiedź modelu.")
    return json.loads(json.loads(payload)["choices"][0]["message"]["content"])
