"""
Consommateur du flux de transactions.

Ce processus travaille hors du chemin critique. Il archive les decisions en
base relationnelle et alimente la file d'investigation. Si le worker tombe,
l'abonne ne s'en rend pas compte : les decisions continuent d'etre rendues.

Ce que ce fichier demontre pour l'examen : la difference entre un Stream et
un Pub/Sub. Avec Pub/Sub, un message emis pendant que le worker est arrete
est perdu. Avec un Stream et un groupe de consommateurs, il reste dans la
liste des messages en attente et sera repris, soit par le meme worker au
redemarrage, soit par un autre via XAUTOCLAIM.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime, timezone

import redis
from django.core.management.base import BaseCommand

from scoring.models import DecisionArchivee
from scoring.redis_client import Cles, client

ARRET_DEMANDE = False


def _demander_arret(signum, frame):
    global ARRET_DEMANDE
    ARRET_DEMANDE = True


class Command(BaseCommand):
    help = "Consomme stream:tx et archive les decisions en base relationnelle."

    def add_arguments(self, parseur):
        parseur.add_argument("--nom", type=str, default="worker1")
        parseur.add_argument("--lot", type=int, default=100)
        parseur.add_argument("--attente", type=int, default=2000,
                             help="Blocage XREADGROUP, en millisecondes.")
        parseur.add_argument("--reprise", type=int, default=30000,
                             help="Age au dela duquel un message en attente "
                                  "est repris a un autre consommateur, en ms.")

    def handle(self, *args, **options):
        signal.signal(signal.SIGTERM, _demander_arret)
        signal.signal(signal.SIGINT, _demander_arret)

        r = client()
        nom = options["nom"]

        try:
            r.xgroup_create(Cles.FLUX_TX, Cles.GROUPE, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        self.stdout.write(self.style.SUCCESS(
            f"Consommateur {nom} demarre sur {Cles.FLUX_TX} "
            f"(groupe {Cles.GROUPE})."
        ))

        traites = 0
        derniere_reprise = time.monotonic()

        while not ARRET_DEMANDE:
            try:
                messages = r.xreadgroup(
                    groupname=Cles.GROUPE,
                    consumername=nom,
                    streams={Cles.FLUX_TX: ">"},
                    count=options["lot"],
                    block=options["attente"],
                )
            except redis.exceptions.ConnectionError:
                self.stderr.write("Redis injoignable, nouvelle tentative dans 2 s.")
                time.sleep(2)
                continue

            if messages:
                for _, entrees in messages:
                    traites += self._traiter(r, entrees)

            # Toutes les dix secondes, on recupere ce qu'un consommateur
            # tombe aurait laisse en attente.
            if time.monotonic() - derniere_reprise > 10:
                derniere_reprise = time.monotonic()
                traites += self._reprendre(r, nom, options["reprise"], options["lot"])

            if traites and traites % 500 == 0:
                self.stdout.write(f"  {traites} decisions archivees.")

        self.stdout.write(self.style.WARNING(
            f"Arret demande. {traites} decisions archivees au total."
        ))

    # ------------------------------------------------------------------
    def _traiter(self, r, entrees) -> int:
        a_creer = []
        identifiants = []

        for identifiant, champs in entrees:
            identifiants.append(identifiant)
            a_creer.append(
                DecisionArchivee(
                    tx_id=champs.get("tx_id", ""),
                    msisdn=champs.get("msisdn", ""),
                    destinataire=champs.get("destinataire", ""),
                    montant=int(champs.get("montant", 0)),
                    score=int(champs.get("score", 0)),
                    decision=champs.get("decision", ""),
                    regles=champs.get("regles", ""),
                    latence_ms=float(champs.get("latence_ms", 0)),
                    instance=champs.get("instance", ""),
                    horodatage=datetime.fromtimestamp(
                        int(champs.get("ts", 0)) / 1000, tz=timezone.utc
                    ),
                )
            )

        if not a_creer:
            return 0

        # L'archivage est fait avant l'acquittement. Si le processus meurt
        # entre les deux, le message sera relu et ignore par le conflit
        # d'unicite sur tx_id. On prefere un doublon evite a une perte.
        DecisionArchivee.objects.bulk_create(a_creer, ignore_conflicts=True)
        r.xack(Cles.FLUX_TX, Cles.GROUPE, *identifiants)
        return len(identifiants)

    def _reprendre(self, r, nom: str, age_ms: int, lot: int) -> int:
        try:
            curseur, entrees, _ = r.xautoclaim(
                name=Cles.FLUX_TX,
                groupname=Cles.GROUPE,
                consumername=nom,
                min_idle_time=age_ms,
                count=lot,
            )
        except redis.exceptions.ResponseError:
            return 0

        if not entrees:
            return 0

        self.stdout.write(self.style.WARNING(
            f"  {len(entrees)} messages orphelins repris par {nom}."
        ))
        return self._traiter(r, entrees)
