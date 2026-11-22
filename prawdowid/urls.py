from django.urls import path
from verify.views import delete_history, index

urlpatterns = [
    path("", index, name="index"),
    path("sesja/usun/", delete_history, name="delete_history"),
]
