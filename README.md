#  Moteur de scoring anti-fraude Mobile Money

> **Projet d'examen : Bases de données NoSQL**  
> Master Intelligence Artificielle — Dakar Institute of Technology  
> Année académique **2025–2026**

**Base de données retenue : Redis**

---

##  Contexte

Chaque transaction Mobile Money doit être **acceptée**, **mise en revue** ou **bloquée** avant que l'abonné ne voie son écran de confirmation.

Pour prendre cette décision, le moteur doit analyser en temps réel le comportement récent du compte :

- combien de virements ont été effectués durant les 5 dernières minutes ;
- vers combien de destinataires différents ;
- pour quels montants ;
- depuis quelle localisation ;
- à quelles heures ;
- et si certains comportements correspondent à des schémas de fraude connus.

Ces calculs reposent principalement sur des **fenêtres temporelles glissantes** et doivent respecter un budget de latence très faible.

---

##  Contraintes du système

| Contrainte | Valeur cible | Conséquence technique |
|---|---:|---|
| **Latence de décision** | 20 ms au 99e centile | Pas d'agrégation SQL dans le chemin critique |
| **Débit en pointe** | 3 000 à 5 000 tx/s | Pas d'accès disque par transaction |
| **Fenêtres d'analyse** | 5 min, 1 h, 24 h, 7 j | Pas de recalcul complet à la lecture |
| **Rétention des compteurs** | 7 jours | Expiration automatique nécessaire |
| **Atomicité** | Totale | Pas de lecture puis écriture séparées |

Redis est utilisé comme **base métier temps réel** afin de maintenir ces informations directement sous des structures adaptées aux différents calculs.

---

##  Les 8 règles de détection

| Code | Règle de fraude | Structure Redis |
|:---:|---|---|
| **R01** | Vélocité d'émission anormale | Sorted Set |
| **R02** | Montant cumulé au-dessus du profil | TimeSeries |
| **R03** | Explosion des destinataires distincts | HyperLogLog |
| **R04** | Voyage géographiquement impossible | Geospatial |
| **R05** | Créneau horaire jamais observé | Bitmap |
| **R06** | Destinataire en liste noire | Bloom Filter |
| **R07** | Structuration sous le seuil déclaratif | Sorted Set |
| **R08** | Comportement de compte relais | Sorted Set croisé |

### Structures complémentaires

Le moteur utilise également :

- **Redis Streams** + groupes de consommateurs pour l'archivage ;
- **Lua** pour garantir l'atomicité de la décision ;
- `SET NX EX` pour assurer l'idempotence ;
- les **TTL Redis** pour l'expiration automatique des données.

---

#  Démarrage rapide

## 1. Préparer l'environnement

```bash
cp .env.example .env
```

## 2. Construire et démarrer les services

```bash
docker compose up --build
```

Une fois les conteneurs démarrés :

| Service | Adresse |
|---|---|
|  Console de supervision | http://localhost:8080/console/ |
|  État du répartiteur Apache | http://localhost:8080/balancer-manager |

---

#  Commandes utiles

## Initialiser Redis

L'initialisation est normalement effectuée automatiquement au démarrage.

```bash
docker compose exec web1 python manage.py init_redis --reinitialiser
```

---

## Simuler les scénarios de fraude

### Vélocité anormale

```bash
docker compose exec web1 python manage.py simulate --scenario velocite
```

### Structuration des transactions

```bash
docker compose exec web1 python manage.py simulate --scenario structuration
```

### Explosion des destinataires

```bash
docker compose exec web1 python manage.py simulate --scenario eventail
```

### Voyage impossible

```bash
docker compose exec web1 python manage.py simulate --scenario voyage
```

### Destinataire en liste noire

```bash
docker compose exec web1 python manage.py simulate --scenario listenoire
```

### Compte relais

```bash
docker compose exec web1 python manage.py simulate --scenario relais
```

### Idempotence

```bash
docker compose exec web1 python manage.py simulate --scenario idempotence
```

---

##  Générer du trafic

Exemple : **200 transactions pendant 120 secondes**.

```bash
docker compose exec web1 python manage.py simulate --charge 200 --duree 120
```

---

#  Inspecter les données Redis d'un abonné

```bash
curl -s http://localhost:8080/v1/abonnes/22997000051 | python -m json.tool
```

---

#  Soumettre une transaction

```bash
curl -s -X POST http://localhost:8080/v1/transactions \
  -H 'Content-Type: application/json' \
  -d '{
    "msisdn": "22997000042",
    "destinataire": "22997000099",
    "montant": 25000,
    "latitude": 6.3703,
    "longitude": 2.4183
  }'
```

---

#  Structure du projet

```text
.
├── docker-compose.yml
│
├── apache/
│   └── httpd.conf
│
├── redis/
│   └── redis-stack.conf
│
├── app/
│   └── scoring/
│       ├── lua/
│       │   └── scoring.lua
│       ├── engine.py
│       ├── views.py
│       └── management/
│
├── docs/
│   └── modele_donnees_redis.md
│
├── demo/
│   └── DEMONSTRATION.md
│
└── PLAN_RAPPORT.md
```

### Rôle des principaux fichiers

| Fichier | Rôle |
|---|---|
| `docker-compose.yml` | Déploiement de la pile complète |
| `apache/httpd.conf` | Reverse proxy et répartition Round Robin |
| `redis/redis-stack.conf` | Configuration de Redis Stack |
| `app/scoring/lua/scoring.lua` | Script atomique au cœur du moteur |
| `app/scoring/engine.py` | Orchestration du scoring et règles complémentaires |
| `app/scoring/views.py` | API et console de supervision |
| `app/scoring/management/` | Initialisation et simulations |
| `docs/modele_donnees_redis.md` | Modèle de données Redis détaillé |
| `PLAN_RAPPORT.md` | Structure du rapport |
| `demo/DEMONSTRATION.md` | Déroulé de la démonstration |

---

#  Limites de la démonstration

Cette implémentation constitue un **prototype académique**. Certaines fonctionnalités prévues pour une architecture de production ne sont volontairement pas déployées.

**Redis Cluster**

Le projet ne déploie pas de véritable Redis Cluster. La conception anticipe cependant le partitionnement grâce aux **hash tags Redis**. La démonstration fonctionne sur une instance unique.

**Authentification de l'API**

L'API de démonstration n'est pas authentifiée. En production, elle pourrait être protégée notamment par **mTLS** ou par **signature des requêtes**.

**Apprentissage automatique**

Le moteur repose actuellement sur des règles déterministes. Les poids sont définis manuellement et peuvent être modifiés à chaud dans les hashes :

```text
rule:<code>
```

**Évaluation de la détection**

Le prototype ne mesure pas encore la précision et le rappel. Une évaluation complète nécessiterait un historique de transactions étiquetées afin de comparer les décisions du moteur aux fraudes réellement observées.

> Ces limites sont **documentées volontairement** dans le rapport plutôt que dissimulées.

---

##  Objectif du prototype

L'objectif n'est pas de reproduire l'intégralité d'une plateforme Mobile Money en production, mais de démontrer comment les structures de données Redis peuvent être combinées pour construire un **moteur de scoring anti-fraude à faible latence**, capable d'exploiter le comportement récent d'un compte en temps réel.