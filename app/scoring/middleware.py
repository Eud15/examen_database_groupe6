"""
Ajoute le nom de l'instance a chaque reponse.

Sans cela, la repartition de charge est invisible. Avec, il suffit d'ouvrir
les outils de developpement du navigateur pendant la soutenance pour montrer
que trois conteneurs se relaient reellement.
"""

from django.conf import settings


class InstanceHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reponse = self.get_response(request)
        reponse["X-Instance"] = settings.NOM_INSTANCE
        return reponse
