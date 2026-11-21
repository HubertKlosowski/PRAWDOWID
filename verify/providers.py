"""Local and chat-completions adapters for model inference."""

import json
import os
from functools import lru_cache
from threading import Lock
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


_local_lock = Lock()


@lru_cache(maxsize=1)
def _load_local(name):
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
    return tokenizer, model.to(device).eval()


def _local_completion(name, messages):
    import torch

    with _local_lock, torch.inference_mode():
        tokenizer, model = _load_local(name)
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
    model = os.environ.get("PRAWDOWID_LLM_MODEL", "Qwen/Qwen3.5-4B")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
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
    with build_opener(_NoRedirect()).open(request, timeout=60) as response:
        payload = response.read(131073)
    if len(payload) > 131072:
        raise ValueError("Za duża odpowiedź modelu.")
    return json.loads(json.loads(payload)["choices"][0]["message"]["content"])
