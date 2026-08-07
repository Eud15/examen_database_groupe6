"""
Acces Redis : connexion partagee, fabrique de cles, chargement du script Lua.

Deux points de conception a savoir defendre en soutenance.

1. Le hash tag. Toutes les cles d'un meme abonne contiennent son MSISDN entre
   accolades. En mode Cluster, seule cette portion est hachee pour choisir le
   slot. Les sept cles manipulees par le script Lua atterrissent donc sur le
   meme noeud, ce qui est la condition pour qu'un script multi cles puisse
   s'executer. Sans ce detail, le passage en cluster casse tout.

2. EVALSHA plutot que EVAL. Le corps du script ne transite qu'une fois sur le
   reseau. Ensuite on n'envoie que son empreinte SHA1, soit 40 octets au lieu
   de plusieurs kilo-octets a chaque transaction.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import redis
from django.conf import settings

_verrou = threading.Lock()
_connexion: redis.Redis | None = None
_sha_scoring: str | None = None

CHEMIN_LUA = Path(__file__).resolve().parent / "lua" / "scoring.lua"


def client() -> redis.Redis:
    """Connexion Redis unique par processus, avec pool integre."""
    global _connexion
    if _connexion is None:
        with _verrou:
            if _connexion is None:
                _connexion = redis.Redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    socket_timeout=2,
                    socket_connect_timeout=2,
                    health_check_interval=30,
                )
    return _connexion


def sha_scoring() -> str:
    """Empreinte du script de scoring, chargee a la premiere utilisation."""
    global _sha_scoring
    if _sha_scoring is None:
        with _verrou:
            if _sha_scoring is None:
                _sha_scoring = client().script_load(
                    CHEMIN_LUA.read_text(encoding="utf-8")
                )
    return _sha_scoring


def recharger_script() -> str:
    """Force le rechargement, apres un SCRIPT FLUSH ou un redemarrage Redis."""
    global _sha_scoring
    with _verrou:
        _sha_scoring = client().script_load(CHEMIN_LUA.read_text(encoding="utf-8"))
    return _sha_scoring


# --------------------------------------------------------------------------
# Fabrique de cles
# --------------------------------------------------------------------------

class Cles:
    """Un seul endroit ou le schema de nommage est ecrit."""

    BLACKLIST = "bf:blacklist"
    GEO_DERNIERE = "geo:last"
    FLUX_TX = "stream:tx"
    FLUX_ALERTES = "stream:alerts"
    FILE_DOSSIERS = "zset:cases"
    GROUPE = "enrichissement"

    @staticmethod
    def abonne(msisdn: str) -> str:
        return f"sub:{{{msisdn}}}"

    @staticmethod
    def vu_le(msisdn: str) -> str:
        return f"sub:{{{msisdn}}}:lastseen"

    @staticmethod
    def velocite_emise(msisdn: str) -> str:
        return f"vel:snd:{{{msisdn}}}"

    @staticmethod
    def velocite_recue(msisdn: str) -> str:
        return f"vel:rcv:{{{msisdn}}}"

    @staticmethod
    def destinataires(msisdn: str, jour: str | None = None) -> str:
        jour = jour or datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"hll:dst:{{{msisdn}}}:{jour}"

    @staticmethod
    def profil_horaire(msisdn: str) -> str:
        return f"bmp:hr:{{{msisdn}}}"

    @staticmethod
    def serie_montants(msisdn: str) -> str:
        return f"ts:amt:{{{msisdn}}}"

    @staticmethod
    def decision(tx_id: str) -> str:
        return f"score:{tx_id}"

    @staticmethod
    def idempotence(tx_id: str) -> str:
        return f"idem:{tx_id}"

    @staticmethod
    def regle(code: str) -> str:
        return f"rule:{code}"

    @staticmethod
    def stats_heure(moment: datetime | None = None) -> str:
        moment = moment or datetime.now(timezone.utc)
        return f"stats:dec:{moment.strftime('%Y%m%d%H')}"

    @staticmethod
    def latences() -> str:
        return "stats:latence"


def index_horaire(moment: datetime) -> int:
    """
    Position du bit dans le profil hebdomadaire : 0 a 167.
    Lundi 00h vaut 0, dimanche 23h vaut 167.
    """
    return moment.weekday() * 24 + moment.hour
