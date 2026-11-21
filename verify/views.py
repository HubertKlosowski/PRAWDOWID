import logging
from time import perf_counter

from django import forms
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .analysis import assess, unavailable
from .fetch import fetch
from .retrieval import retrieve

logger = logging.getLogger(__name__)


class VerificationForm(forms.Form):
    mode = forms.ChoiceField(choices=[("text", "Tekst"), ("url", "Adres URL")])
    material = forms.CharField(max_length=12000, error_messages={
        "required": "Wpisz treść lub adres strony.",
        "max_length": "Materiał może mieć najwyżej 12 000 znaków.",
    })


@require_http_methods(["GET", "POST"])
def index(request):
    elapsed_seconds = None
    started_at = perf_counter()
    form = VerificationForm(request.POST if request.method == "POST" else None)
    result = None
    if request.method == "POST" and form.is_valid():
        text = form.cleaned_data["material"]
        if form.cleaned_data["mode"] == "url":
            try:
                _, _, text = fetch(text)
                if len(text) > 12000:
                    raise ValueError("Artykuł przekracza limit. Wklej fragment do sprawdzenia.")
            except ValueError as exc:
                form.add_error("material", str(exc))
        if not form.errors:
            try:
                result = assess(text, retrieve(text))
            except Exception as exc:
                logger.warning("Verification service unavailable (%s)", type(exc).__name__)
                result = unavailable("Wyszukiwanie źródeł jest obecnie niedostępne. Spróbuj ponownie później.")
        elapsed_seconds = perf_counter() - started_at
    return render(request, "verify/index.html", {
        "form": form,
        "result": result,
        "elapsed_seconds": elapsed_seconds,
    })
