from types import SimpleNamespace
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .management.commands import serve_demo
from .providers import DEFAULT_LLM_MODEL


class DemoServingTests(SimpleTestCase):
    @patch("verify.management.commands.serve_demo.RunserverCommand.inner_run")
    @patch("verify.management.commands.serve_demo.load_local_model")
    @patch("verify.management.commands.serve_demo.load_model")
    def test_preloads_only_configured_local_models(self, embedding, local_llm, serve):
        for url in ("", "https://example.org/chat"):
            with self.subTest(url=url), patch.dict("os.environ", PRAWDOWID_LLM_URL=url,
                                                    PRAWDOWID_LLM_MODEL=DEFAULT_LLM_MODEL):
                serve_demo.Command().inner_run()
                if url:
                    local_llm.assert_not_called()
                else:
                    local_llm.assert_called_once_with(DEFAULT_LLM_MODEL)
                embedding.assert_called_once()
                serve.assert_called_once()
                embedding.reset_mock()
                local_llm.reset_mock()
                serve.reset_mock()

    @patch("verify.management.commands.serve_demo.load_model", side_effect=OSError("missing weights"))
    def test_embedding_startup_failure_names_the_dependency(self, embedding):
        with self.assertRaisesMessage(CommandError, "modelu embeddingowego"):
            serve_demo.Command().preload_models()

    @patch("verify.management.commands.serve_demo.load_model")
    @patch("verify.management.commands.serve_demo.load_local_model", side_effect=RuntimeError("CUDA out of memory"))
    @patch.dict("os.environ", PRAWDOWID_LLM_URL="")
    def test_local_llm_startup_failure_names_the_dependency(self, llm, embedding):
        with self.assertRaisesMessage(CommandError, "lokalnego modelu oceniającego"):
            serve_demo.Command().preload_models()

    def test_packed_decoder_accepts_prompt_and_single_token_shapes(self):
        import torch
        from .cpu_inference import PackedLinear, prepare_cpu_decoder

        layer = torch.nn.Linear(128, 64, dtype=torch.bfloat16)
        block = torch.nn.Sequential(layer)
        model = SimpleNamespace(config=SimpleNamespace(model_type="qwen3_5_text"), dtype=torch.bfloat16,
                                model=SimpleNamespace(layers=[block]))
        prepare_cpu_decoder(model)
        if not isinstance(block[0], PackedLinear):
            self.skipTest("CPU lub PyTorch bez obsługi tej ścieżki oneDNN.")
        with torch.inference_mode():
            for length in (1, 19):
                x = torch.randn(1, length, 128, dtype=torch.bfloat16)
                torch.testing.assert_close(block(x), layer(x), atol=0.016, rtol=0.02)
