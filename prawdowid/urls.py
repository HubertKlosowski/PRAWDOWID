from django.urls import path
from verify.views import delete_history, download_export, index

urlpatterns = [
    path("", index, name="index"),
    path("sesja/odpowiedz.json", download_export, {"latest_only": True}, name="export_answer"),
    path("sesja/historia.json", download_export, {"latest_only": False}, name="export_history"),
    path("sesja/usun/", delete_history, name="delete_history"),
]
