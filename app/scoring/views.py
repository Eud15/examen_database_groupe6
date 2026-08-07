"""API du moteur et console de supervision."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
import os
import redis
from django.conf import settings
from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .engine import Transaction, scorer
from .redis_client import Cles, client, index_horaire

CHAMPS_OBLIGATOIRES = ("msisdn", "destinataire", "montant")

LIBELLES_REGLES = {
    "R01": "Velocite d'emission anormale",
    "R02": "Montant cumule au dessus du profil",
    "R03": "Explosion des destinataires distincts",
    "R04": "Voyage geographiquement impossible",
    "R05": "Creneau horaire jamais observe",
    "R06": "Destinataire en liste noire",
    "R07": "Structuration sous le seuil declaratif",
    "R08": "Comportement de compte relais",
}


@api_view(["POST"])
def evaluer_transaction(requete):
    """
    Chemin critique. Chaque milliseconde passee ici est une milliseconde
    d'attente pour l'abonne devant son telephone.
    """
    corps = requete.data
    manquants = [c for c in CHAMPS_OBLIGATOIRES if c not in corps]
    if manquants:
        return Response(
            {"erreur": "Champs manquants", "champs": manquants},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        montant = int(corps["montant"])
    except (TypeError, ValueError):
        return Response(
            {"erreur": "Le montant doit etre un entier en francs CFA"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if montant <= 0:
        return Response(
            {"erreur": "Le montant doit etre strictement positif"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # tx_id fourni par l'appelant si le systeme amont en genere un, sinon
    # produit ici. C'est la cle de la garde d'idempotence.
    parametres = {
        "msisdn": str(corps["msisdn"]),
        "destinataire": str(corps["destinataire"]),
        "montant": montant,
        "latitude": corps.get("latitude"),
        "longitude": corps.get("longitude"),
    }
    if corps.get("tx_id"):
        parametres["tx_id"] = str(corps["tx_id"])
    tx = Transaction(**parametres)

    try:
        decision = scorer(tx)
    except redis.exceptions.ConnectionError:
        # Politique de repli assumee : si le moteur est injoignable, on ne
        # laisse pas passer en aveugle, on met en revue. Un faux positif coute
        # une verification, un faux negatif coute de l'argent.
        return Response(
            {
                "tx_id": tx.tx_id,
                "decision": "REVIEW",
                "score": None,
                "regles": ["MOTEUR_INDISPONIBLE"],
                "instance": settings.NOM_INSTANCE,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(
        {
            "tx_id": decision.tx_id,
            "decision": decision.decision,
            "score": decision.score,
            "regles": [
                {"code": c, "libelle": LIBELLES_REGLES.get(c, c)}
                for c in decision.regles
            ],
            "latence_ms": decision.latence_ms,
            "instance": decision.instance,
        }
    )


@api_view(["GET"])
def detail_decision(requete, tx_id: str):
    donnees = client().hgetall(Cles.decision(tx_id))
    if not donnees:
        return Response(
            {"erreur": "Decision inconnue ou expiree"},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(donnees)


@api_view(["GET"])
def profil_abonne(requete, msisdn: str):
    """
    Vue d'inspection. Sert surtout a la demonstration : elle montre en une
    requete les cinq structures Redis mobilisees pour un abonne.
    """
    r = client()
    maintenant = datetime.now(timezone.utc)
    horodatage = int(maintenant.timestamp() * 1000)

    tuyau = r.pipeline(transaction=False)
    tuyau.hgetall(Cles.abonne(msisdn))
    tuyau.zcount(Cles.velocite_emise(msisdn), horodatage - 300_000, horodatage)
    tuyau.zcard(Cles.velocite_emise(msisdn))
    tuyau.zrevrange(Cles.velocite_emise(msisdn), 0, 9, withscores=True)
    tuyau.pfcount(Cles.destinataires(msisdn, maintenant.strftime("%Y%m%d")))
    tuyau.bitcount(Cles.profil_horaire(msisdn))
    tuyau.getbit(Cles.profil_horaire(msisdn), index_horaire(maintenant))
    tuyau.geopos(Cles.GEO_DERNIERE, msisdn)
    tuyau.ttl(Cles.velocite_emise(msisdn))
    (
        profil, envois_5min, total_envois, derniers,
        destinataires_jour, heures_connues, heure_courante_connue,
        position, ttl,
    ) = tuyau.execute()

    return Response(
        {
            "msisdn": msisdn,
            "profil": profil,
            "structures": {
                "sorted_set_velocite": {
                    "cle": Cles.velocite_emise(msisdn),
                    "envois_5_dernieres_minutes": envois_5min,
                    "membres_en_fenetre": total_envois,
                    "expiration_secondes": ttl,
                    "dix_dernieres": [
                        {"membre": m, "horodatage": int(s)} for m, s in derniers
                    ],
                },
                "hyperloglog_destinataires": {
                    "cle": Cles.destinataires(msisdn, maintenant.strftime("%Y%m%d")),
                    "cardinalite_estimee": destinataires_jour,
                },
                "bitmap_profil_horaire": {
                    "cle": Cles.profil_horaire(msisdn),
                    "creneaux_deja_observes": heures_connues,
                    "creneau_courant_connu": bool(heure_courante_connue),
                    "index_courant": index_horaire(maintenant),
                },
                "geo_derniere_position": {
                    "cle": Cles.GEO_DERNIERE,
                    "position": position[0] if position and position[0] else None,
                },
            },
        }
    )


@api_view(["GET"])
def statistiques(requete):
    """Alimente la console. Interrogee une fois par seconde en demonstration."""
    r = client()
    maintenant = datetime.now(timezone.utc)

    tuyau = r.pipeline(transaction=False)
    tuyau.hgetall(Cles.stats_heure(maintenant))
    tuyau.lrange(Cles.latences(), 0, 999)
    tuyau.xrevrange(Cles.FLUX_TX, count=25)
    tuyau.xlen(Cles.FLUX_TX)
    tuyau.zcard(Cles.FILE_DOSSIERS)
    tuyau.info("memory")
    compteurs, latences_brutes, flux, taille_flux, nb_dossiers, memoire = tuyau.execute()

    latences = sorted(float(v) for v in latences_brutes) if latences_brutes else []
    if latences:
        rang_99 = max(0, int(len(latences) * 0.99) - 1)
        rang_50 = max(0, int(len(latences) * 0.50) - 1)
        resume_latence = {
            "mediane_ms": round(latences[rang_50], 2),
            "p99_ms": round(latences[rang_99], 2),
            "max_ms": round(latences[-1], 2),
            "moyenne_ms": round(statistics.mean(latences), 2),
            "echantillon": len(latences),
        }
    else:
        resume_latence = {
            "mediane_ms": None, "p99_ms": None, "max_ms": None,
            "moyenne_ms": None, "echantillon": 0,
        }

    par_regle = {
        code: int(compteurs.get(f"regle:{code}", 0)) for code in LIBELLES_REGLES
    }

    dernieres = []
    for identifiant, champs in flux:
        dernieres.append(
            {
                "id": identifiant,
                "tx_id": champs.get("tx_id"),
                "msisdn": champs.get("msisdn"),
                "montant": int(champs.get("montant", 0)),
                "decision": champs.get("decision"),
                "score": int(champs.get("score", 0)),
                "regles": [c for c in champs.get("regles", "").split(",") if c],
                "latence_ms": float(champs.get("latence_ms", 0)),
                "instance": champs.get("instance"),
            }
        )

    return Response(
        {
            "decisions": {
                "total": int(compteurs.get("TOTAL", 0)),
                "accept": int(compteurs.get("ACCEPT", 0)),
                "review": int(compteurs.get("REVIEW", 0)),
                "block": int(compteurs.get("BLOCK", 0)),
            },
            "latence": resume_latence,
            "budget_ms": settings.SCORING["BUDGET_LATENCE_MS"],
            "regles": par_regle,
            "libelles": LIBELLES_REGLES,
            "flux": {"longueur": taille_flux, "dossiers_ouverts": nb_dossiers},
            "memoire_redis": memoire.get("used_memory_human"),
            "dernieres": dernieres,
            "instance": settings.NOM_INSTANCE,
        }
    )


@api_view(["GET"])
def dossiers(requete):
    """File d'investigation, du plus risque au moins risque."""
    r = client()
    entrees = r.zrevrange(Cles.FILE_DOSSIERS, 0, 19, withscores=True)
    return Response(
        [{"msisdn": m, "risque": int(s)} for m, s in entrees]
    )


@api_view(["GET"])
def sante(requete):
    try:
        debut = datetime.now(timezone.utc)
        client().ping()
        aller_retour = (datetime.now(timezone.utc) - debut).total_seconds() * 1000
        return Response(
            {
                "etat": "ok",
                "instance": settings.NOM_INSTANCE,
                "redis_ping_ms": round(aller_retour, 2),
            }
        )
    except redis.exceptions.ConnectionError as exc:
        return Response(
            {"etat": "degrade", "instance": settings.NOM_INSTANCE, "detail": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def console(requete):
    return render(
        requete,
        "scoring/console.html",
        {"instance": settings.NOM_INSTANCE, "budget": settings.SCORING["BUDGET_LATENCE_MS"]},
    )
