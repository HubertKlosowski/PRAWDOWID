from time import perf_counter

from django import forms
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .verification import MAX_MATERIAL_LENGTH, InvalidMaterial, verify


class VerificationForm(forms.Form):
    mode = forms.ChoiceField(choices=[("text", "Tekst"), ("url", "Adres URL")])
    material = forms.CharField(max_length=MAX_MATERIAL_LENGTH, error_messages={
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
        try:
            result = verify(form.cleaned_data["material"], mode=form.cleaned_data["mode"])
        except InvalidMaterial as exc:
            form.add_error("material", str(exc))
        elapsed_seconds = perf_counter() - started_at
    return render(request, "verify/index.html", {
        "form": form,
        "result": result,
        "elapsed_seconds": elapsed_seconds,
    })
