"""
Persistance froide.

Redis ne conserve que sept jours de comportement. Le regulateur, lui, exige
cinq ans de tracabilite sur les decisions de blocage. C'est exactement la
frontiere entre les deux bases, et c'est le point a defendre en Partie 3 :
on n'a pas choisi Redis contre le relationnel, on a reparti les roles.
"""

from django.db import models


class DecisionArchivee(models.Model):
    tx_id = models.CharField(max_length=32, unique=True, db_index=True)
    msisdn = models.CharField(max_length=20, db_index=True)
    destinataire = models.CharField(max_length=20)
    montant = models.BigIntegerField()
    score = models.IntegerField()
    decision = models.CharField(max_length=10, db_index=True)
    regles = models.CharField(max_length=100, blank=True)
    latence_ms = models.FloatField()
    instance = models.CharField(max_length=20)
    horodatage = models.DateTimeField(db_index=True)
    archivee_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["msisdn", "horodatage"])]
        ordering = ["-horodatage"]

    def __str__(self):
        return f"{self.tx_id} {self.decision} ({self.score})"
