from time import perf_counter

from django import forms
from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .audit import COOKIE_NAME, delete_session, new_session_key, retention_period, valid_session_key
from .security import enforce_limit, limit_message
from .verification import MAX_MATERIAL_LENGTH, InvalidMaterial, material_mode, verify


class VerificationForm(forms.Form):
    material = forms.CharField(max_length=MAX_MATERIAL_LENGTH, error_messages={
        "required": "Wpisz treść lub adres strony.",
        "max_length": "Materiał może mieć najwyżej 12 000 znaków.",
    })


@never_cache
@require_http_methods(["GET", "POST"])
def index(request):
    elapsed_seconds = None
    started_at = perf_counter()
    form = VerificationForm(request.POST if request.method == "POST" else None)
    result = None
    session_key = request.COOKIES.get(COOKIE_NAME)
    if not valid_session_key(session_key):
        session_key = None
    new_session = False
    retry_after = None
    status = 200
    if request.method == "POST" and form.is_valid():
        material = form.cleaned_data["material"]
        if session_key is None and (settings.PRAWDOWID_AUDIT_ENABLED or settings.PRAWDOWID_RATE_LIMIT_ENABLED):
            session_key = new_session_key()
            new_session = True
        try:
            retry_after = enforce_limit("web", session_key, client_ip=request.META.get("REMOTE_ADDR"))
        except DatabaseError:
            status = 503
            form.add_error(None, "Sprawdzanie jest chwilowo niedostępne. Spróbuj ponownie później.")
        else:
            if retry_after is not None:
                status = 429
                form.add_error(None, limit_message(retry_after))
            else:
                try:
                    result = verify(material, mode=material_mode(material), session_key=session_key, channel="web")
                except InvalidMaterial as exc:
                    form.add_error("material", str(exc))
        elapsed_seconds = perf_counter() - started_at
    response = render(request, "verify/index.html", {
        "form": form,
        "result": result,
        "elapsed_seconds": elapsed_seconds,
        "has_history_session": bool(session_key),
        "history_deleted": request.GET.get("historia") == "usunieta",
    }, status=status)
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    if new_session:
        response.set_cookie(COOKIE_NAME, session_key, httponly=True, secure=request.is_secure(),
                            samesite="Lax", max_age=int(retention_period().total_seconds()))
    return response


@never_cache
@require_POST
def delete_history(request):
    try:
        delete_session(request.COOKIES.get(COOKIE_NAME), "web")
    except DatabaseError:
        return HttpResponse("Nie udało się usunąć historii. Spróbuj ponownie później.", status=503)
    response = redirect("/?historia=usunieta")
    response.delete_cookie(COOKIE_NAME, samesite="Lax")
    return response
