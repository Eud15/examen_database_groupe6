"""
Orchestration du scoring.

Le decoupage entre ce qui est evalue ici et ce qui est evalue dans le script
Lua n'est pas arbitraire. Deux regles ne peuvent pas entrer dans le script :

  R04, voyage impossible, s'appuie sur geo:last, une cle partagee par tous les
       abonnes et donc situee sur un autre slot en mode Cluster.
  R06, liste noire, s'appuie sur bf:blacklist, meme probleme, et sur une
       commande de module que le script standard ne peut pas invoquer.

Elles sont evaluees dans un pipeline, en un seul aller-retour reseau, puis
leur contribution est passee au script sous forme de score deja accumule.
La decision finale reste donc calculee en un seul endroit.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

import redis
from django.conf import settings

from .redis_client import Cles, client, index_horaire, recharger_script, sha_scoring

logger = logging.getLogger(__name__)


@dataclass
class Transaction:
    msisdn: str
    destinataire: str
    montant: int
    latitude: float | None = None
    longitude: float | None = None
    tx_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12].upper())
    moment: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Decision:
    tx_id: str
    decision: str
    score: int
    regles: list[str]
    latence_ms: float
    instance: str


CODES_REGLES = ("R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08")
DUREE_CACHE_POIDS = 30.0

_poids_cache: dict[str, int] = {}
_poids_expire_a: float = 0.0


def poids_regles(forcer: bool = False) -> dict[str, int]:
    """
    Poids courants, relus depuis Redis toutes les 30 secondes au plus.

    Un analyste qui reponde une regle voit l'effet en moins d'une minute,
    sans redemarrage. Le cache evite huit lectures supplementaires par
    transaction, ce qui compte au debit vise.
    """
    global _poids_cache, _poids_expire_a
    maintenant = time.monotonic()
    if forcer or maintenant > _poids_expire_a or not _poids_cache:
        try:
            tuyau = client().pipeline(transaction=False)
            for code in CODES_REGLES:
                tuyau.hmget(Cles.regle(code), "poids", "actif")
            resultats = tuyau.execute()
            _poids_cache = {
                code: int(poids)
                for code, (poids, actif) in zip(CODES_REGLES, resultats)
                if poids is not None and actif != "0"
            }
            _poids_expire_a = maintenant + DUREE_CACHE_POIDS
        except redis.exceptions.RedisError as exc:
            logger.warning("Poids des regles non relus, valeurs precedentes : %s", exc)
    return _poids_cache


def _regles_hors_script(tx: Transaction, tuyau: redis.client.Pipeline) -> None:
    """Empile les lectures necessaires a R04 et R06 dans le pipeline."""
    tuyau.execute_command("BF.EXISTS", Cles.BLACKLIST, tx.destinataire)
    tuyau.geopos(Cles.GEO_DERNIERE, tx.msisdn)
    tuyau.get(Cles.vu_le(tx.msisdn))


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique. Formule de haversine."""
    rayon = 6371.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * rayon * asin(sqrt(a))


def _evaluer_geo(tx: Transaction, position_avant, vu_le) -> tuple[int, str | None]:
    """
    Regle R04. Compare la distance parcourue au temps ecoule.
    Au dela de la vitesse maximale retenue, le deplacement est incoherent.

    La position precedente vient de GEOPOS, deja lue dans le pipeline. Le
    calcul de distance est fait ici plutot que par GEODIST parce que GEODIST
    exige que les deux points soient deja stockes : la position entrante ne
    l'est pas encore, et l'y ecrire avant la decision fausserait la regle en
    plus de coutant un aller-retour supplementaire dans le chemin critique.
    """
    if tx.latitude is None or tx.longitude is None:
        return 0, None
    if not position_avant or not position_avant[0] or not vu_le:
        return 0, None

    lon_avant, lat_avant = position_avant[0]
    ecoule_ms = int(tx.moment.timestamp() * 1000) - int(vu_le)
    if ecoule_ms <= 0:
        return 0, None

    distance = _distance_km(
        float(lat_avant), float(lon_avant), float(tx.latitude), float(tx.longitude)
    )
    vitesse = distance / (ecoule_ms / 3_600_000)
    if vitesse > settings.SCORING["VITESSE_MAX_KMH"]:
        return 0, "R04"
    return 0, None


def scorer(tx: Transaction) -> Decision:
    """Point d'entree unique. Renvoie la decision et la latence mesuree."""
    debut = time.perf_counter()
    r = client()

    tuyau = r.pipeline(transaction=False)
    _regles_hors_script(tx, tuyau)
    en_liste_noire, position_avant, vu_le = tuyau.execute()

    poids = poids_regles()
    score_prealable = 0
    regles_prealables: list[str] = []

    if en_liste_noire and "R06" in poids:
        score_prealable += poids["R06"]
        regles_prealables.append("R06")

    if "R04" in poids:
        _, code_geo = _evaluer_geo(tx, position_avant, vu_le)
        if code_geo:
            score_prealable += poids["R04"]
            regles_prealables.append(code_geo)

    horodatage = int(tx.moment.timestamp() * 1000)
    cles = [
        Cles.velocite_emise(tx.msisdn),
        Cles.velocite_recue(tx.msisdn),
        Cles.destinataires(tx.msisdn, tx.moment.strftime("%Y%m%d")),
        Cles.profil_horaire(tx.msisdn),
        Cles.abonne(tx.msisdn),
        Cles.decision(tx.tx_id),
        Cles.idempotence(tx.tx_id),
    ]
    arguments = [
        tx.tx_id,
        str(tx.montant),
        str(horodatage),
        tx.destinataire,
        str(index_horaire(tx.moment)),
        str(score_prealable),
        ",".join(regles_prealables),
        str(settings.SCORING["SEUIL_DECLARATIF"]),
        ",".join(f"{code}:{valeur}" for code, valeur in poids.items()),
    ]

    try:
        brut = r.evalsha(sha_scoring(), len(cles), *cles, *arguments)
    except redis.exceptions.NoScriptError:
        # Redis a redemarre ou le cache de scripts a ete vide.
        brut = r.evalsha(recharger_script(), len(cles), *cles, *arguments)

    decision, score, codes = brut[0], int(brut[1]), brut[2]
    regles = [c for c in codes.split(",") if c]
    latence_ms = (time.perf_counter() - debut) * 1000

    _apres_decision(tx, decision, score, regles, latence_ms, horodatage)

    return Decision(
        tx_id=tx.tx_id,
        decision=decision,
        score=score,
        regles=regles,
        latence_ms=round(latence_ms, 3),
        instance=settings.NOM_INSTANCE,
    )


def _apres_decision(
    tx: Transaction,
    decision: str,
    score: int,
    regles: list[str],
    latence_ms: float,
    horodatage: int,
) -> None:
    """
    Ecritures qui ne conditionnent pas la reponse : position, series, flux,
    compteurs de supervision. Groupees en un seul pipeline pour ne payer
    qu'un aller-retour reseau.
    """
    r = client()
    tuyau = r.pipeline(transaction=False)

    if decision != "BLOCK":
        tuyau.zadd(
            Cles.velocite_recue(tx.destinataire),
            {f"{tx.tx_id}|{tx.montant}": horodatage},
        )
        tuyau.expire(Cles.velocite_recue(tx.destinataire), 604800)
        if tx.latitude is not None and tx.longitude is not None:
            tuyau.geoadd(Cles.GEO_DERNIERE, (tx.longitude, tx.latitude, tx.msisdn))
        tuyau.set(Cles.vu_le(tx.msisdn), horodatage, ex=2592000)
        tuyau.execute_command(
            "TS.ADD", Cles.serie_montants(tx.msisdn), horodatage, tx.montant,
            "RETENTION", 604800000, "DUPLICATE_POLICY", "LAST",
            "LABELS", "type", "montant", "msisdn", tx.msisdn,
        )

    tuyau.xadd(
        Cles.FLUX_TX,
        {
            "tx_id": tx.tx_id,
            "msisdn": tx.msisdn,
            "destinataire": tx.destinataire,
            "montant": tx.montant,
            "decision": decision,
            "score": score,
            "regles": ",".join(regles),
            "latence_ms": f"{latence_ms:.3f}",
            "instance": settings.NOM_INSTANCE,
            "ts": horodatage,
        },
        maxlen=100_000,
        approximate=True,
    )

    if decision != "ACCEPT":
        tuyau.xadd(
            Cles.FLUX_ALERTES,
            {
                "tx_id": tx.tx_id,
                "msisdn": tx.msisdn,
                "score": score,
                "regles": ",".join(regles),
                "decision": decision,
            },
            maxlen=10_000,
            approximate=True,
        )
        # La file d'investigation est triee par risque : l'analyste depile
        # toujours le dossier le plus grave en premier.
        tuyau.zadd(Cles.FILE_DOSSIERS, {tx.msisdn: score}, gt=True)

    cle_stats = Cles.stats_heure(tx.moment)
    tuyau.hincrby(cle_stats, decision, 1)
    tuyau.hincrby(cle_stats, "TOTAL", 1)
    for code in regles:
        tuyau.hincrby(cle_stats, f"regle:{code}", 1)
    tuyau.expire(cle_stats, 7_776_000)

    # Fenetre glissante des mille dernieres latences, pour le centile 99.
    tuyau.lpush(Cles.latences(), f"{latence_ms:.3f}")
    tuyau.ltrim(Cles.latences(), 0, 999)

    try:
        tuyau.execute()
    except redis.exceptions.ResponseError as exc:
        # TS.ADD echoue si RedisTimeSeries n'est pas charge. Le scoring reste
        # valide, seule la serie de montants est perdue.
        logger.warning("Ecriture post decision partielle : %s", exc)
