"""
Configuration Django du moteur anti-fraude.

Le point notable pour l'examen est la double persistance : Redis porte tout
ce qui est chaud et doit repondre en quelques millisecondes, PostgreSQL porte
ce qui doit survivre a une panne et etre interrogeable analytiquement.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "cle-de-developpement")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = ["*"]

# Nom de l'instance, injecte par docker compose.
# Il est renvoye dans l'en-tete X-Instance de chaque reponse, ce qui rend la
# repartition de charge visible pendant la demonstration.
NOM_INSTANCE = os.environ.get("NOM_INSTANCE", "local")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "scoring",
]

MIDDLEWARE = [
    # CsrfViewMiddleware est volontairement absent : l'API est appelee par
    # le systeme de paiement, pas par un navigateur. En production elle
    # serait protegee par mTLS ou par une signature de requete.
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "scoring.middleware.InstanceHeaderMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

# --------------------------------------------------------------------------
# Persistance froide
# --------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "momo"),
        "USER": os.environ.get("POSTGRES_USER", "momo"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "momo2026"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Redis
# --------------------------------------------------------------------------
#REDIS_URL = os.environ.get("REDIS_URL", "redis://redis_node1:26379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": [
            # Liste des Sentinels disponibles
            ("redis_node1", 26379),
            ("redis_node2", 26380),
            ("redis_node3", 26381),
        ],
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.SentinelClient",
            "SENTINEL_MASTER": os.environ["REDIS_MASTER_NAME"],
            "PASSWORD": os.environ["REDIS_PASSWORD"],
            "DB": 0,
        }
    }
}

# Les sessions vont dans Redis. C'est le point de depart de l'architecture
# proposee, mais ce n'est pas le sujet du projet : une ligne de configuration.
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_AGE = 3600

# --------------------------------------------------------------------------
# Parametres metier du moteur de scoring
# --------------------------------------------------------------------------
SCORING = {
    # Seuil de declaration reglementaire, en francs CFA.
    # Sert a la detection de structuration (regle R07).
    "SEUIL_DECLARATIF": 500_000,
    # Bornes de decision.
    "SEUIL_REVIEW": 40,
    "SEUIL_BLOCK": 70,
    # Vitesse au dela de laquelle un deplacement est juge impossible (km/h).
    "VITESSE_MAX_KMH": 900,
    # Budget de latence annonce, en millisecondes. Sert au suivi, pas au calcul.
    "BUDGET_LATENCE_MS": 20,
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Porto-Novo"
USE_TZ = True
USE_I18N = False

STATIC_URL = "static/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[%(asctime)s] %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
