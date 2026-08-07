"""
Generateur de trafic pour la demonstration.

Deux usages distincts.

  --scenario <nom>   rejoue un schema de fraude precis, une transaction a la
                     fois, en affichant la decision. C'est ce qu'on montre au
                     jury pour prouver que chaque regle fonctionne.

  --charge <n>       envoie n transactions par seconde de trafic legitime.
                     C'est ce qui tourne en fond pendant la soutenance pour
                     que la console ne soit pas vide et que la latence mesuree
                     ait un sens.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from scoring.engine import Transaction, scorer

COTONOU = (6.3703, 2.4183)
DAKAR = (14.7167, -17.4677)
PARAKOU = (9.3400, 2.6300)

COULEURS = {
    "ACCEPT": "\033[92m",
    "REVIEW": "\033[93m",
    "BLOCK": "\033[91m",
}
NEUTRE = "\033[0m"


class Command(BaseCommand):
    help = "Rejoue des schemas de fraude ou genere du trafic de fond."

    def add_arguments(self, parseur):
        parseur.add_argument("--scenario", type=str, default=None)
        parseur.add_argument("--charge", type=int, default=0,
                             help="Transactions par seconde.")
        parseur.add_argument("--duree", type=int, default=60,
                             help="Duree de la charge, en secondes.")
        parseur.add_argument("--msisdn", type=str, default=None,
                             help="Force l'abonne cible du scenario.")

    def handle(self, *args, **options):
        if options["scenario"]:
            self._jouer_scenario(options["scenario"], options["msisdn"])
        elif options["charge"] > 0:
            self._generer_charge(options["charge"], options["duree"])
        else:
            raise CommandError(
                "Precisez --scenario <nom> ou --charge <tx/s>. "
                f"Scenarios disponibles : {', '.join(SCENARIOS)}."
            )

    # ------------------------------------------------------------------
    def _afficher(self, etiquette: str, decision) -> None:
        couleur = COULEURS.get(decision.decision, "")
        codes = ",".join(decision.regles) or "aucune"
        self.stdout.write(
            f"  {etiquette:<34} "
            f"{couleur}{decision.decision:<7}{NEUTRE} "
            f"score {decision.score:>3}  "
            f"regles {codes:<20} "
            f"{decision.latence_ms:>6.2f} ms"
        )

    def _jouer_scenario(self, nom: str, msisdn_force: str | None) -> None:
        if nom not in SCENARIOS:
            raise CommandError(
                f"Scenario inconnu. Disponibles : {', '.join(SCENARIOS)}."
            )
        titre, fonction = SCENARIOS[nom]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(titre))
        self.stdout.write("")
        fonction(self, msisdn_force)
        self.stdout.write("")

    def _generer_charge(self, par_seconde: int, duree: int) -> None:
        aleatoire = random.Random()
        self.stdout.write(
            f"Trafic legitime : {par_seconde} tx/s pendant {duree} s."
        )
        fin = time.monotonic() + duree
        envoyees = 0
        latences = []
        intervalle = 1.0 / par_seconde

        while time.monotonic() < fin:
            debut = time.monotonic()
            emetteur = f"229970{aleatoire.randint(1, 200):05d}"
            destinataire = f"229970{aleatoire.randint(1, 200):05d}"
            if destinataire == emetteur:
                continue
            tx = Transaction(
                msisdn=emetteur,
                destinataire=destinataire,
                montant=aleatoire.choice([1000, 2500, 5000, 10000, 25000, 50000]),
                latitude=COTONOU[0] + aleatoire.uniform(-0.05, 0.05),
                longitude=COTONOU[1] + aleatoire.uniform(-0.05, 0.05),
            )
            decision = scorer(tx)
            latences.append(decision.latence_ms)
            envoyees += 1

            if envoyees % 200 == 0:
                ordonnees = sorted(latences[-1000:])
                p99 = ordonnees[int(len(ordonnees) * 0.99) - 1]
                self.stdout.write(
                    f"  {envoyees:>6} transactions   p99 {p99:>6.2f} ms"
                )

            reste = intervalle - (time.monotonic() - debut)
            if reste > 0:
                time.sleep(reste)

        ordonnees = sorted(latences)
        self.stdout.write(self.style.SUCCESS(
            f"{envoyees} transactions. "
            f"mediane {ordonnees[len(ordonnees) // 2]:.2f} ms, "
            f"p99 {ordonnees[int(len(ordonnees) * 0.99) - 1]:.2f} ms, "
            f"budget {settings.SCORING['BUDGET_LATENCE_MS']} ms."
        ))


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------

def _normal(commande, msisdn_force):
    """Trafic legitime, sert de reference avant les autres scenarios."""
    msisdn = msisdn_force or "22997000042"
    for i in range(3):
        tx = Transaction(
            msisdn=msisdn,
            destinataire=f"229970{100 + i:05d}",
            montant=5000,
            latitude=COTONOU[0], longitude=COTONOU[1],
        )
        commande._afficher(f"virement courant {i + 1}", scorer(tx))
        time.sleep(0.2)


def _velocite(commande, msisdn_force):
    """R01. Dix virements en moins d'une minute."""
    msisdn = msisdn_force or "22997000051"
    for i in range(10):
        tx = Transaction(
            msisdn=msisdn,
            destinataire=f"229970{110 + i:05d}",
            montant=15000,
            latitude=COTONOU[0], longitude=COTONOU[1],
        )
        commande._afficher(f"virement rapide {i + 1:>2}/10", scorer(tx))


def _eventail(commande, msisdn_force):
    """R03. Un compte qui arrose trente destinataires differents."""
    msisdn = msisdn_force or "22997000052"
    for i in range(30):
        tx = Transaction(
            msisdn=msisdn,
            destinataire=f"22996{700000 + i:06d}",
            montant=3000,
            latitude=COTONOU[0], longitude=COTONOU[1],
        )
        decision = scorer(tx)
        if i < 2 or i >= 22:
            commande._afficher(f"destinataire {i + 1:>2}/30", decision)
        elif i == 2:
            commande.stdout.write("  ...")


def _voyage(commande, msisdn_force):
    """R04. Cotonou puis Dakar deux minutes plus tard, soit 1 600 km."""
    msisdn = msisdn_force or "22997000053"

    depart = Transaction(
        msisdn=msisdn, destinataire="22997000120", montant=8000,
        latitude=COTONOU[0], longitude=COTONOU[1],
    )
    commande._afficher("retrait a Cotonou", scorer(depart))

    commande.stdout.write("  (deux minutes plus tard)")
    time.sleep(1)

    arrivee = Transaction(
        msisdn=msisdn, destinataire="22997000121", montant=8000,
        latitude=DAKAR[0], longitude=DAKAR[1],
        moment=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    commande._afficher("retrait a Dakar, 1 600 km", scorer(arrivee))


def _liste_noire(commande, msisdn_force):
    """R06. Le destinataire figure dans le filtre de Bloom."""
    msisdn = msisdn_force or "22997000054"
    tx = Transaction(
        msisdn=msisdn, destinataire="22997000901", montant=20000,
        latitude=COTONOU[0], longitude=COTONOU[1],
    )
    commande._afficher("envoi vers numero signale", scorer(tx))


def _structuration(commande, msisdn_force):
    """
    R07. Le seuil declaratif est a 500 000. Le fraudeur envoie quatre fois
    490 000 plutot qu'une fois 1 960 000.
    """
    msisdn = msisdn_force or "22997000055"
    for i in range(4):
        tx = Transaction(
            msisdn=msisdn,
            destinataire=f"229970{130 + i:05d}",
            montant=490_000,
            latitude=COTONOU[0], longitude=COTONOU[1],
        )
        commande._afficher(f"virement {i + 1}/4 de 490 000 F", scorer(tx))


def _relais(commande, msisdn_force):
    """
    R08. Un compte recoit de trois sources puis reemet presque tout
    en quelques minutes. C'est une mule, pas un utilisateur.
    """
    mule = msisdn_force or "22997000056"

    for i in range(3):
        alimentation = Transaction(
            msisdn=f"229970{140 + i:05d}",
            destinataire=mule,
            montant=100_000,
            latitude=COTONOU[0], longitude=COTONOU[1],
        )
        commande._afficher(f"la mule recoit 100 000 F ({i + 1}/3)", scorer(alimentation))

    for i in range(3):
        sortie = Transaction(
            msisdn=mule,
            destinataire=f"229970{150 + i:05d}",
            montant=95_000,
            latitude=COTONOU[0], longitude=COTONOU[1],
        )
        commande._afficher(f"la mule reemet 95 000 F ({i + 1}/3)", scorer(sortie))


def _idempotence(commande, msisdn_force):
    """Rejeu de la meme transaction : la decision doit etre identique."""
    msisdn = msisdn_force or "22997000057"
    tx = Transaction(
        msisdn=msisdn, destinataire="22997000160", montant=12000,
        latitude=COTONOU[0], longitude=COTONOU[1],
    )
    commande._afficher("premiere soumission", scorer(tx))
    commande._afficher("rejeu du meme tx_id", scorer(tx))
    commande._afficher("second rejeu", scorer(tx))


SCENARIOS = {
    "normal": ("Trafic legitime, aucune regle ne doit se declencher", _normal),
    "velocite": ("R01  Velocite d'emission anormale", _velocite),
    "eventail": ("R03  Explosion des destinataires distincts", _eventail),
    "voyage": ("R04  Voyage geographiquement impossible", _voyage),
    "listenoire": ("R06  Destinataire en liste noire", _liste_noire),
    "structuration": ("R07  Structuration sous le seuil declaratif", _structuration),
    "relais": ("R08  Comportement de compte relais", _relais),
    "idempotence": ("Garde d'idempotence sur rejeu", _idempotence),
}
