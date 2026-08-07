"""
Initialisation de l'espace Redis.

Idempotente : elle peut tourner au demarrage des trois instances sans effet
de bord. C'est necessaire, docker compose les lance en parallele.
"""

from __future__ import annotations

import random

import redis
from django.core.management.base import BaseCommand

from scoring.redis_client import Cles, client, recharger_script

REGLES = {
    "R01": ("Velocite d'emission anormale", 25, "sorted_set"),
    "R02": ("Montant cumule au dessus du profil", 20, "timeseries"),
    "R03": ("Explosion des destinataires distincts", 30, "hyperloglog"),
    "R04": ("Voyage geographiquement impossible", 35, "geospatial"),
    "R05": ("Creneau horaire jamais observe", 10, "bitmap"),
    "R06": ("Destinataire en liste noire", 50, "bloom"),
    "R07": ("Structuration sous le seuil declaratif", 30, "sorted_set"),
    "R08": ("Comportement de compte relais", 40, "sorted_set"),
}

# Quelques numeros deja signales, pour la demonstration de la regle R06.
LISTE_NOIRE = [
    "22997000901", "22997000902", "22997000903",
    "22996555111", "22996555222",
]

VILLES = {
    "Cotonou": (2.4183, 6.3703),
    "Porto-Novo": (2.6289, 6.4969),
    "Parakou": (2.6300, 9.3400),
    "Natitingou": (1.3800, 10.3040),
}


class Command(BaseCommand):
    help = "Prepare l'espace de cles Redis : regles, liste noire, abonnes de test."

    def add_arguments(self, parseur):
        parseur.add_argument(
            "--abonnes", type=int, default=200,
            help="Nombre d'abonnes de test a creer dans le referentiel.",
        )
        parseur.add_argument(
            "--reinitialiser", action="store_true",
            help="Vide entierement la base avant initialisation.",
        )

    def handle(self, *args, **options):
        r = client()

        if options["reinitialiser"]:
            r.flushdb()
            self.stdout.write(self.style.WARNING("Base videe."))

        # 1. Script de scoring
        sha = recharger_script()
        self.stdout.write(f"Script de scoring charge, empreinte {sha[:12]}...")

        # 2. Referentiel des regles
        tuyau = r.pipeline()
        for code, (libelle, poids, structure) in REGLES.items():
            tuyau.hset(
                Cles.regle(code),
                mapping={
                    "libelle": libelle,
                    "poids": poids,
                    "structure": structure,
                    "actif": 1,
                },
            )
        tuyau.execute()
        self.stdout.write(f"{len(REGLES)} regles enregistrees.")

        # 3. Filtre de Bloom
        try:
            r.execute_command("BF.RESERVE", Cles.BLACKLIST, 0.001, 1_000_000)
            self.stdout.write("Filtre de Bloom cree, 1 million d'entrees a 0,1 %.")
        except redis.exceptions.ResponseError as exc:
            if "exists" in str(exc).lower():
                self.stdout.write("Filtre de Bloom deja present.")
            else:
                self.stderr.write(self.style.ERROR(f"RedisBloom indisponible : {exc}"))

        try:
            r.execute_command("BF.MADD", Cles.BLACKLIST, *LISTE_NOIRE)
            self.stdout.write(f"{len(LISTE_NOIRE)} numeros ajoutes a la liste noire.")
        except redis.exceptions.ResponseError as exc:
            self.stderr.write(self.style.ERROR(f"Ajout liste noire impossible : {exc}"))

        # 4. Referentiel abonnes
        aleatoire = random.Random(20260807)
        tuyau = r.pipeline()
        for i in range(1, options["abonnes"] + 1):
            msisdn = f"229970{i:05d}"
            segment = aleatoire.choice(["particulier", "particulier", "marchand"])
            plafond = 2_000_000 if segment == "marchand" else 500_000
            ville = aleatoire.choice(list(VILLES))
            lon, lat = VILLES[ville]
            tuyau.hset(
                Cles.abonne(msisdn),
                mapping={
                    "segment": segment,
                    "kyc_level": aleatoire.choice([1, 2, 2, 3]),
                    "plafond_journalier": plafond,
                    "region_origine": ville,
                },
            )
            tuyau.geoadd(Cles.GEO_DERNIERE, (lon, lat, msisdn))
        tuyau.execute()
        self.stdout.write(f"{options['abonnes']} abonnes de test crees.")

        # 5. Groupe de consommateurs sur le flux
        try:
            r.xgroup_create(Cles.FLUX_TX, Cles.GROUPE, id="0", mkstream=True)
            self.stdout.write(f"Groupe '{Cles.GROUPE}' cree sur {Cles.FLUX_TX}.")
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                self.stdout.write("Groupe de consommateurs deja present.")
            else:
                raise

        self.stdout.write(self.style.SUCCESS("Initialisation terminee."))
