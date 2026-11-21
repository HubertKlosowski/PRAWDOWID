from django.urls import path
from verify.views import index

urlpatterns = [path("", index, name="index")]
