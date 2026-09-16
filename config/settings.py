"""
Django settings for config project.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# override=True: sin esto, load_dotenv() NO pisa variables de entorno que ya
# existan a nivel de sistema/sesion -- en esta maquina, por ejemplo, ya habia
# un TELEGRAM_BOT_TOKEN de otro proyecto (openclaw) que se colaba en lugar
# del de este .env, y las alertas se mandaban al bot equivocado en silencio
# (confirmado 2026-08-19: 400 Bad Request contra un bot que no era el
# nuestro). El .env de este proyecto siempre debe ganar.
load_dotenv(BASE_DIR / ".env", override=True)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# Mismo patron que eros-prime-gate: produccion falla de forma segura, desarrollo
# conserva una clave local para que manage.py funcione tras copiar .env.example.
DEBUG = env_bool("DJANGO_DEBUG", default=False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY es obligatoria en produccion.")
if not DEBUG and len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY debe tener al menos 50 caracteres.")

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    default="localhost,127.0.0.1" if DEBUG else "",
)
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS es obligatorio en produccion.")
if not DEBUG and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS no puede contener '*' en produccion.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Token de servicio de Link -- el mismo que usa eros-prime-gate para llamar a la
# API interna real de Link (api.thinknow.link). Ver intake/link_client.py.
LINK_API_TOKEN = os.environ.get("LINK_API_TOKEN", "")

# JWT temporal (vence 2026-09-02, ver .env) -- solo para
# /surveysupplier-allocations-optimized/, que exige JWT y no acepta el
# token de servicio de arriba. Si esta vacio o vencido, ese endpoint se
# trata como "sin datos", nunca como error fatal (ver intake/link_client.py).
LINK_JWT_TOKEN = os.environ.get("LINK_JWT_TOKEN", "")

# Base URL de la API interna real de Link.
LINK_API_BASE_URL = os.environ.get(
    "LINK_API_BASE_URL", "https://api.thinknow.link/api/v1"
).rstrip("/")

# API key que debe mandar quien consuma /api/decisions/ en el header
# 'X-API-Key'. Viene de .env -- nunca hardcodeada. Ver dashboard/permissions.py.
API_KEY = os.environ.get("API_KEY", "")

# Edad maxima aceptada del heartbeat de un worker antes de reportarlo como
# degradado en /health/worker/ (ver config/health.py). Cada worker refresca
# su heartbeat una vez por pasada; 600s deja margen para el poll-interval mas
# largo (run_earnings_tracker usa 300s por defecto).
WORKER_HEALTH_MAX_AGE_SECONDS = int(os.environ.get("WORKER_HEALTH_MAX_AGE_SECONDS", "600"))

# Alertas de performance (IR bajo, terminacion alta) -- mismos canales y
# mismos destinatarios que eros-prime-gate (confirmado por el usuario:
# reusar Telegram/email existentes, no crear canales nuevos separados).
# Email via django-ses, autenticado con el rol IAM de la instancia EC2 en
# produccion; en local hace falta `aws configure` con permiso ses:SendEmail.
EMAIL_BACKEND = "django_ses.SESBackend"
AWS_SES_REGION_NAME = os.environ.get("AWS_SES_REGION_NAME", "")
AWS_SES_REGION_ENDPOINT = f"email.{AWS_SES_REGION_NAME}.amazonaws.com" if AWS_SES_REGION_NAME else ""
AWS_SES_AUTO_THROTTLE = 0
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "")
PERFORMANCE_ALERT_EMAILS = os.environ.get("PERFORMANCE_ALERT_EMAILS", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# CORS -- el dashboard embebible de Link 2.0 hace fetch() a esta API. En
# produccion se sirve del mismo origen (sin CORS); esto habilita el caso
# cross-origin (iframe en Link, pruebas locales abriendo el .html directo).
# Mismo patron que tnr-link (django-cors-headers).
CORS_ALLOW_ALL_ORIGINS = DEBUG  # solo en desarrollo
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")  # produccion: dominios de Link
CORS_ALLOW_HEADERS = ("accept", "authorization", "content-type", "origin", "x-api-key")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "simple_history",
    "django_ses",
    "corsheaders",
    "intake",
    "rules_engine",
    "orchestrator",
    "dashboard",
    "audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # corsheaders debe ir lo mas arriba posible y antes de CommonMiddleware.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# Mismo patron que eros-prime-gate: SQLite en desarrollo (DJANGO_DEBUG=True, sin
# instalar Postgres localmente), PostgreSQL RDS en produccion via variables de
# entorno separadas -- nunca DATABASE_URL, nunca credenciales hardcodeadas.
if DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # WAL (no el rollback journal por defecto de sqlite) -- confirmado
            # 2026-08-26 que sin esto, run_allocator + run_earnings_tracker
            # escribiendo sin parar bloquean cualquier otra conexion (el panel
            # de negocio, otro manage.py) por varios segundos o indefinidamente.
            # WAL permite lectores concurrentes mientras alguien escribe.
            "OPTIONS": {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"},
        }
    }
else:
    rds_config = {
        "ENGINE": os.environ.get("DB_ENGINE", "").strip(),
        "HOST": os.environ.get("DB_HOSTNAME", "").strip(),
        "NAME": os.environ.get("DB_NAME", "").strip(),
        "USER": os.environ.get("DB_USERNAME", "").strip(),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "PORT": os.environ.get("DB_PORT", "5432").strip(),
    }
    variable_names = {
        "ENGINE": "DB_ENGINE",
        "HOST": "DB_HOSTNAME",
        "NAME": "DB_NAME",
        "USER": "DB_USERNAME",
        "PASSWORD": "DB_PASSWORD",
        "PORT": "DB_PORT",
    }
    missing_database_values = [variable_names[key] for key, value in rds_config.items() if not value]
    if missing_database_values:
        raise ImproperlyConfigured("Faltan variables de PostgreSQL RDS: " + ", ".join(missing_database_values))
    if rds_config["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("DB_ENGINE debe ser django.db.backends.postgresql en produccion.")
    if rds_config["HOST"].lower() in {"localhost", "127.0.0.1", "::1"}:
        raise ImproperlyConfigured("DB_HOSTNAME debe ser el endpoint privado de PostgreSQL RDS, no localhost.")

    database_sslmode = os.environ.get("DB_SSLMODE", "require").strip()
    if database_sslmode not in {"require", "verify-ca", "verify-full"}:
        raise ImproperlyConfigured("DB_SSLMODE debe ser require, verify-ca o verify-full para PostgreSQL RDS.")

    DATABASES = {
        "default": {
            **rds_config,
            "CONN_MAX_AGE": int(os.environ.get("DATABASE_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"sslmode": database_sslmode},
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JS del admin y del Swagger UI). En produccion los sirve
# Nginx desde STATIC_ROOT tras `collectstatic`; ver deploy/nginx/eros-allocator.conf.
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Nginx es el unico proceso publico y termina TLS antes de reenviar a Gunicorn,
# que escucha exclusivamente en 127.0.0.1. Mismo patron que eros-prime-gate.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", default=not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Mantener HSTS en 0 hasta validar HTTPS end-to-end; luego, p. ej. 31536000.
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)

# Logs en formato JSON compacto a stdout -> journald -> rsyslog separa por
# servicio en /var/log/eros-allocator/. No se envia nada a CloudWatch.
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "config.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EROS Allocator -- Survey Launch Automation API",
    "DESCRIPTION": (
        "Rule engine + orchestrator that pre-configures CPI, profit floor, "
        "buyer/provider exclusions, and ranks eligible surveys by business "
        "attractiveness for Link's head manager."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
