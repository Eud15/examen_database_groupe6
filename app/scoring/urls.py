from django.urls import path

from . import views

urlpatterns = [
    path("v1/transactions", views.evaluer_transaction, name="evaluer"),
    path("v1/transactions/<str:tx_id>", views.detail_decision, name="detail"),
    path("v1/abonnes/<str:msisdn>", views.profil_abonne, name="profil"),
    path("v1/stats", views.statistiques, name="stats"),
    path("v1/dossiers", views.dossiers, name="dossiers"),
    path("v1/sante", views.sante, name="sante"),
    path("console/", views.console, name="console"),
]
