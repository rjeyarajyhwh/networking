"""
Django settings for the NetWatch NOC dashboard.

Runs on SQLite out of the box so it starts with zero setup. To switch to
PostgreSQL (matching the original tech-stack plan) once you have it
installed, see the DATABASES block below.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "dev-only-secret-key-change-before-any-real-deployment"

# This app is meant to run inside your college LAN, not on the public
# internet. Keep DEBUG on for the project demo; turn it off and set
# ALLOWED_HOSTS properly before leaving it running unattended.
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "monitor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "netwatch.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "netwatch.wsgi.application"

# --- Database -----------------------------------------------------------
# Default: SQLite, no setup required. To switch to PostgreSQL:
#   1. pip install psycopg2-binary
#   2. createdb netwatch   (or use pgAdmin)
#   3. Replace the DATABASES dict below with:
#
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.postgresql",
#         "NAME": "netwatch",
#         "USER": "netwatch",
#         "PASSWORD": "your-password",
#         "HOST": "localhost",
#         "PORT": "5432",
#     }
# }
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Path to the hardware config the collector reads (switches, router, SNMP
# community strings). Edit this file, not this settings.py, to point the
# collector at your real gear.
NETWORK_CONFIG_PATH = BASE_DIR / "network_config.json"
