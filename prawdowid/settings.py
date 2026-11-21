import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(48)
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
ROOT_URLCONF = "prawdowid.urls"
INSTALLED_APPS = ["django.contrib.staticfiles", "verify"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.middleware.common.CommonMiddleware",
              "django.middleware.csrf.CsrfViewMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}]
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": os.getenv("PGDATABASE","prawdowid"),
                         "OPTIONS": {"connect_timeout": 5}}}
LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_TZ = True
STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 131072
