# Plan du rapport

Le sujet impose quatre parties. Ce plan les détaille et indique, pour chacune, ce qu'il faut absolument dire et ce qu'il faut éviter d'écrire.

Volume conseillé : 25 à 35 pages hors annexes. Un rapport plus long n'est pas lu.

---

## Avant les quatre parties

**Page de garde.** Institution, intitulé du cours, année, membres du groupe avec les numéros d'étudiant, base retenue, titre du use case.

**Sommaire paginé.**

**Introduction, une page et demie.** Trois choses seulement :
1. Le problème métier en un paragraphe, avec les chiffres de contrainte (débit, latence, volumétrie).
2. Pourquoi le relationnel ne suffit pas ici. Une phrase suffit si elle est précise.
3. L'annonce du plan.

Ne pas écrire d'introduction générale sur le NoSQL, son histoire ou le théorème CAP. Cela se voit immédiatement et cela consomme des pages qui manqueront ensuite.

---

## Partie 1 — Architecture interne

**Objectif :** montrer qu'on a compris comment Redis fonctionne à l'intérieur, pas seulement comment on l'appelle.

### 1.1 Le modèle d'exécution
- Boucle d'événements mono-thread, multiplexage des entrées et sorties.
- Ce que le mono-thread donne gratuitement : sérialisation des opérations, pas de verrous, pas de conditions de course.
- Ce qu'il coûte : une commande lente bloque tout le serveur. Faire le lien avec le réglage `slowlog-log-slower-than` dans `redis/redis-stack.conf`.
- Depuis Redis 6, les entrées et sorties sont multi-thread mais l'exécution des commandes reste mono-thread. Beaucoup de rapports se trompent là-dessus, le préciser est un point gagné.

### 1.2 Structures et encodages internes
- Table de hachage principale, expansion progressive du redimensionnement.
- Encodages compacts : listpack pour les petits Hash et Sorted Set, intset pour les Set d'entiers. Faire le lien avec les seuils configurés.
- Vérifier soi-même avec `OBJECT ENCODING` sur une clé du projet et mettre la capture dans le rapport. Une observation vaut mieux qu'une citation.

### 1.3 Persistance
- RDB : instantané, fork du processus, copie sur écriture. Rapide à recharger, perte possible entre deux instantanés.
- AOF : journal des écritures, trois politiques de `fsync`. Expliquer pourquoi `everysec` a été retenu dans ce projet, chiffres à l'appui.
- Réécriture automatique de l'AOF, seuils configurés.

### 1.4 Réplication et haute disponibilité
- Réplication asynchrone, réplication partielle après coupure.
- Sentinel : surveillance, quorum, bascule automatique. Adapté à une topologie maître et réplicas.
- Cluster : 16 384 slots, redirections `MOVED` et `ASK`, resharding. Adapté au partitionnement horizontal.
- **Dire lequel on retiendrait pour ce use case et pourquoi.** Un rapport qui décrit les deux sans trancher perd des points.

### 1.5 Gestion de la mémoire
- Politiques d'éviction. Justifier `volatile-ttl` : les compteurs de scoring portent tous une expiration, le filtre de Bloom et le référentiel des règles n'en portent pas et ne doivent jamais être évincés.
- Expiration paresseuse et échantillonnage actif.

### 1.6 Extensions
- RedisBloom et RedisTimeSeries, utilisés dans le projet.
- Préciser que ce sont des modules, pas le cœur : cela conditionne l'image Docker et le choix d'hébergement.

**Longueur :** 7 à 9 pages. Au moins un schéma fait par vous, pas repris d'une documentation.

---

## Partie 2 — Modèle de données

**Objectif :** montrer qu'on sait modéliser dans un paradigme sans schéma, ce qui est plus difficile que de modéliser en relationnel.

### 2.1 Le modèle natif
Les types, avec pour chacun sa complexité algorithmique et un cas d'usage tiré du projet : String, Hash, List, Set, Sorted Set, Bitmap, HyperLogLog, Stream, Geospatial.

Ne pas recopier la documentation. Une ligne par type suffit si elle contient la complexité et l'usage retenu.

### 2.2 Comparaison au relationnel
Reprendre le tableau de correspondance du document technique. **La colonne des pertes doit être aussi fournie que celle des gains.** Développer particulièrement :
- L'absence d'index secondaire : tout accès autre que par clé doit être construit à la main.
- L'absence de jointure : la dénormalisation n'est pas une négligence, c'est le modèle.
- L'absence d'intégrité référentielle : la charge remonte dans l'applicatif.

### 2.3 Modélisation du use case
- La convention de nommage et sa justification.
- Le rôle du hash tag pour le mode Cluster. C'est le détail technique qui montre qu'on a pensé à la mise à l'échelle.
- Le tableau complet des clés : nom, type, contenu, durée de vie.
- **Pour chaque choix de structure, l'alternative écartée et la raison.** Sorted Set plutôt que List pour la fenêtre. HyperLogLog plutôt que Set pour les destinataires, avec le calcul mémoire. Bitmap plutôt que Hash pour le profil horaire. Bloom plutôt que Set pour la liste noire. Ces quatre justifications sont le cœur de la partie.

### 2.4 Dimensionnement
Calcul de l'empreinte mémoire pour un million d'abonnés actifs. Mesurer avec `MEMORY USAGE` sur des clés réelles du projet, extrapoler, présenter le résultat. C'est vérifiable et personne ne le fait.

**Longueur :** 8 à 10 pages.

---

## Partie 3 — Modèle de cohérence

**Objectif :** montrer qu'on connaît les limites de l'outil qu'on a choisi. C'est la partie la plus discriminante.

### 3.1 Cohérence sur une instance unique
Le mono-thread donne une sérialisabilité de fait. Toutes les opérations sont linéarisables tant qu'il n'y a qu'un nœud.

### 3.2 Ce que `MULTI/EXEC` garantit, et ce qu'il ne garantit pas
- Garantit : isolation, aucune commande extérieure ne s'intercale.
- Ne garantit pas : l'atomicité au sens de la base relationnelle. Une commande qui échoue à l'exécution n'annule pas les précédentes. **Il n'y a pas de rollback.**
- `WATCH` et le contrôle de concurrence optimiste.
- Pourquoi le projet utilise un script Lua plutôt que `MULTI/EXEC` : le script peut lire, décider et écrire en fonction de ce qu'il a lu, ce que `MULTI` ne permet pas puisque les commandes sont empilées avant d'être exécutées.

### 3.3 Les scripts Lua et les fonctions
- Atomicité, mêmes garanties que `MULTI/EXEC` plus la logique conditionnelle.
- `EVAL` contre `EVALSHA`, cache des scripts, erreur `NOSCRIPT` après redémarrage. Le projet la gère, le montrer.
- Le risque : un script long bloque le serveur entier.
- Les Redis Functions comme évolution des scripts.

### 3.4 Durabilité
- Réplication asynchrone : un accusé de réception ne signifie pas répliqué.
- `WAIT` : ce qu'il fait, ce qu'il ne fait pas. Il compte les réplicas ayant reçu, il ne bloque ni n'annule l'écriture.
- Fenêtre de perte selon la politique de `fsync`.
- **Positionner Redis sur le théorème CAP** : disponibilité et tolérance au partitionnement, cohérence sacrifiée. Et surtout dire pourquoi c'est acceptable ici : perdre une seconde de compteurs de scoring dégrade légèrement la détection pendant quelques minutes, cela ne perd pas d'argent.

### 3.5 Cohérence en mode Cluster
- Pas de transaction multi-slots. D'où le hash tag.
- Une bascule peut perdre des écritures acquittées.
- `MIGRATING` et `IMPORTING` pendant un resharding.

### 3.6 La frontière avec le relationnel
Le tableau qui répartit les rôles : ce qui vit dans Redis, ce qui vit dans PostgreSQL, et le critère de décision. C'est la conclusion de la partie et elle doit être explicite.

**Longueur :** 6 à 8 pages.

---

## Partie 4 — Use case

**Objectif :** prouver que tout ce qui précède sert à quelque chose.

### 4.1 Le métier
Le problème, les acteurs, les schémas de fraude réels. Les huit règles avec leur logique métier, pas seulement leur code.

### 4.2 Justification du choix technologique
La table des contraintes, une par ligne, avec en face ce qui l'impose. Puis un paragraphe sur chaque alternative écartée :
- Pourquoi pas PostgreSQL seul.
- Pourquoi pas Cassandra : excellente en écriture, mais les lectures avec agrégation sur fenêtre glissante ne sont pas son terrain, et la latence de lecture est d'un ordre de grandeur au dessus.
- Pourquoi pas Neo4j : pertinent pour cartographier un réseau de mules après coup, pas pour décider en vingt millisecondes.
- Pourquoi pas Memcached : ni structures de données, ni persistance, ni atomicité applicative.

Cette section est ce que le sujet appelle explicitement « la justification du choix technologique ». La soigner.

### 4.3 Architecture déployée
Le schéma de l'infrastructure, les composants, les flux. Expliquer la séparation entre le chemin critique et le traitement asynchrone.

### 4.4 Modélisation
Renvoi à la Partie 2, plus le schéma des flux de données d'une transaction, de l'entrée à la décision.

### 4.5 Requêtes critiques en langage natif
C'est explicitement demandé. Présenter, avec pour chacune la sortie réelle obtenue :
- Le script Lua complet, commenté.
- Les commandes de chaque règle isolément.
- Les commandes de flux : `XADD`, `XGROUP CREATE`, `XREADGROUP`, `XACK`, `XPENDING`, `XAUTOCLAIM`.
- Les commandes d'inspection : `OBJECT ENCODING`, `MEMORY USAGE`, `SLOWLOG GET`, `INFO`.

### 4.6 Mesures
Latence médiane et centile 99 sous charge, débit atteint, empreinte mémoire. Des chiffres obtenus sur votre machine, avec la configuration précisée. Des mesures modestes et vraies valent mieux que des chiffres impressionnants et invérifiables.

### 4.7 Limites et suites
Ce qui n'a pas été traité : Cluster réel, chiffrement, apprentissage automatique pour la pondération des règles, dérive des seuils. Le dire vous-mêmes évite qu'on vous le reproche.

**Longueur :** 8 à 10 pages.

---

## Annexes

- `docker-compose.yml`
- `redis/redis-stack.conf` commenté
- `apache/httpd.conf`
- `scoring.lua` intégral
- Copies d'écran de la console et des sessions `redis-cli`
- Dépôt de code, avec le lien

---

## Répartition dans le groupe

| Membre | Rédaction | Défend à l'oral | Prépare aussi |
|---|---|---|---|
| 1 | Partie 1 | Architecture interne, persistance, Sentinel contre Cluster | Le schéma d'infrastructure |
| 2 | Partie 2 | Modélisation, choix de structures, calcul mémoire | Les mesures `MEMORY USAGE` |
| 3 | Partie 3 | Cohérence, absence de rollback, `WAIT`, CAP | La frontière Redis et PostgreSQL |
| 4 | Partie 4 | Use case, script Lua, démonstration | Le scénario de démonstration |

**Une personne relit l'ensemble** pour l'homogénéité du style, la numérotation des figures et la cohérence des chiffres entre les parties. Un rapport où la Partie 1 annonce 5 000 tx/s et la Partie 4 en mesure 400 sans l'expliquer se fait sanctionner.

---

## Les huit questions à préparer

Chaque membre doit savoir répondre à celles qui touchent sa partie.

1. Pourquoi un Sorted Set plutôt qu'une List pour la fenêtre glissante ?
2. Que se passe-t-il si une commande échoue au milieu d'un `MULTI/EXEC` ?
3. Redis est mono-thread, comment tient-il cinq mille transactions par seconde ?
4. Quelle quantité de données pouvez-vous perdre en cas de coupure brutale, et pourquoi est-ce acceptable ?
5. Que deviennent vos scripts Lua si vous passez en mode Cluster ?
6. Le HyperLogLog est approximatif. Comment justifiez-vous une décision de blocage sur une valeur approximative ?
7. Pourquoi avez-vous gardé une base relationnelle ?
8. Comment testez-vous que vos règles ne produisent pas trop de faux positifs ?

La question 8 n'a pas de bonne réponse dans le projet en l'état. Le dire franchement, et proposer la méthode : rejouer un mois de transactions historiques étiquetées, mesurer précision et rappel, ajuster les poids. C'est une meilleure réponse qu'une improvisation.
