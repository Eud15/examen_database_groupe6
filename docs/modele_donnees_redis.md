# Modèle de données Redis
## Moteur de scoring anti-fraude temps réel sur transactions mobile money

Document de travail technique. Sert de base aux Parties 2 et 4 du rapport.

---

## 1. Contexte fonctionnel

Un opérateur de mobile money traite plusieurs milliers de transactions par seconde aux heures de pointe. Chaque transaction doit recevoir une décision (`ACCEPT`, `REVIEW`, `BLOCK`) avant d'être confirmée à l'abonné.

Contraintes qui structurent tout le projet :

| Contrainte | Valeur | Conséquence technique |
|---|---|---|
| Budget de latence du scoring | 20 ms au 99e centile | Interdit toute agrégation SQL dans le chemin critique |
| Débit en pointe | 3 000 à 5 000 tx/s | Interdit un accès disque par transaction |
| Fenêtres d'analyse | 5 min, 1 h, 24 h, 7 j | Impose des compteurs glissants entretenus en continu |
| Rétention des compteurs chauds | 7 jours | Impose une expiration automatique, sans job de purge |
| Atomicité de la décision | totale | Impose lecture, calcul et écriture en une seule opération |

Ce sont ces cinq lignes qui justifient Redis. Aucune ne peut être satisfaite par MySQL seul.

---

## 2. Convention de nommage

Format retenu : `domaine:entité:identifiant:attribut`

Le séparateur `:` est la convention Redis usuelle et permet un filtrage par `SCAN MATCH`. Les identifiants d'abonné sont des MSISDN normalisés au format E.164 sans le `+`.

Sur Redis Cluster, les clés d'un même abonné doivent résider sur le même slot pour pouvoir être manipulées ensemble dans un script Lua. On utilise donc un hash tag : `vel:snd:{22997000001}`. Seule la portion entre accolades est hachée. Ce détail est central pour la Partie 3.

---

## 3. Espace de clés

### 3.1 Référentiel abonné

| Clé | Type | Contenu | Durée de vie |
|---|---|---|---|
| `sub:{msisdn}` | Hash | `segment`, `kyc_level`, `date_activation`, `plafond_journalier`, `region_origine` | permanente |
| `sub:{msisdn}:lastseen` | String | timestamp epoch ms de la dernière transaction | 30 j |
| `sub:{msisdn}:stats` | Hash | `montant_moyen`, `ecart_type`, `nb_tx_total` | permanente |

Le référentiel maître reste en base relationnelle. Redis n'héberge que la projection nécessaire au scoring, rafraîchie par un worker. C'est un point à assumer dans le rapport : Redis n'est pas la source de vérité de l'identité, il est la source de vérité du comportement récent.

### 3.2 Fenêtres de vélocité

| Clé | Type | Membre | Score |
|---|---|---|---|
| `vel:snd:{msisdn}` | Sorted Set | `{tx_id}\|{montant}` | timestamp epoch ms |
| `vel:rcv:{msisdn}` | Sorted Set | `{tx_id}\|{montant}` | timestamp epoch ms |

Le Sorted Set est le seul type qui permet une fenêtre glissante exacte à coût logarithmique. Le score porte le temps, ce qui rend `ZCOUNT` et `ZREMRANGEBYSCORE` naturels. Le montant est concaténé au membre pour éviter un aller-retour supplémentaire lors de la sommation dans le script Lua.

Justification à préparer pour l'oral : une List donnerait un accès O(N) pour retrouver la borne de la fenêtre, et n'offrirait aucun moyen de purger par plage temporelle.

### 3.3 Séries de montants

| Clé | Type | Contenu |
|---|---|---|
| `ts:amt:{msisdn}` | TimeSeries | montant de chaque transaction émise |

Créée avec rétention et règles d'agrégation :

```
TS.CREATE ts:amt:{22997000001} RETENTION 604800000 DUPLICATE_POLICY LAST LABELS type montant msisdn 22997000001
TS.CREATERULE ts:amt:{22997000001} ts:amt:1h:{22997000001} AGGREGATION sum 3600000
```

L'agrégation est calculée par Redis à l'écriture, pas à la lecture. Cela déplace le coût hors du chemin critique.

### 3.4 Cardinalité des destinataires

| Clé | Type | Usage |
|---|---|---|
| `hll:dst:{msisdn}:{yyyymmdd}` | HyperLogLog | destinataires distincts sur la journée |

```
PFADD hll:dst:{22997000001}:20260807 22997000455
PFCOUNT hll:dst:{22997000001}:20260807
PFMERGE hll:dst:{22997000001}:7j hll:dst:{22997000001}:20260801 ... 
```

Un Set exact coûterait environ 60 octets par destinataire. Sur 8 millions d'abonnés actifs, cela représente plusieurs dizaines de gigaoctets. Le HyperLogLog occupe 12 Ko quelle que soit la cardinalité, avec une erreur standard de 0,81 %. Pour détecter un compte qui passe de 3 à 400 destinataires distincts, cette erreur est sans effet.

Cet arbitrage précision contre mémoire est un des meilleurs arguments du rapport.

### 3.5 Profil horaire

| Clé | Type | Usage |
|---|---|---|
| `bmp:hr:{msisdn}` | Bitmap | 168 bits, un par heure de la semaine |

Index du bit : `jour_semaine * 24 + heure`, soit 0 à 167.

```
SETBIT bmp:hr:{22997000001} 51 1
GETBIT bmp:hr:{22997000001} 51
BITCOUNT bmp:hr:{22997000001}
```

21 octets par abonné pour un profil comportemental complet. Une transaction à une heure jamais observée sur douze semaines d'historique est un signal faible mais bon marché.

### 3.6 Position géographique

| Clé | Type | Usage |
|---|---|---|
| `geo:last` | Geospatial (Sorted Set) | dernière position connue de chaque abonné |

```
GEOADD geo:last 2.4183 6.3703 22997000001
GEOPOS geo:last 22997000001
GEODIST geo:last 22997000001 22997000455 km
GEOSEARCH geo:last FROMLONLAT 2.4183 6.3703 BYRADIUS 5 km ASC COUNT 20
```

La règle du voyage impossible compare `GEODIST` entre l'ancienne et la nouvelle position au temps écoulé depuis `lastseen`. Au delà de 900 km/h, la transaction est physiquement incohérente avec la précédente.

Le type Geospatial est en réalité un Sorted Set dont le score est un geohash encodé sur 52 bits. C'est un point à mentionner en Partie 1 : Redis ne multiplie pas les structures internes, il réutilise le Sorted Set.

### 3.7 Liste noire

| Clé | Type | Usage |
|---|---|---|
| `bf:blacklist` | Bloom Filter | MSISDN signalés |

```
BF.RESERVE bf:blacklist 0.001 10000000
BF.ADD bf:blacklist 22997000455
BF.EXISTS bf:blacklist 22997000455
BF.MEXISTS bf:blacklist 22997000455 22997000456
```

Un faux positif envoie la transaction en revue humaine, ce qui est acceptable. Un faux négatif est impossible par construction, ce qui est la propriété recherchée. Le filtre occupe environ 17 Mo pour 10 millions d'entrées à 0,1 % d'erreur, contre plus de 600 Mo pour un Set.

### 3.8 Règles et décisions

| Clé | Type | Contenu | Durée de vie |
|---|---|---|---|
| `rule:{code}` | Hash | `libelle`, `poids`, `seuil`, `actif` | permanente |
| `score:{tx_id}` | Hash | `score`, `decision`, `regles_declenchees`, `latence_us` | 7 j |
| `stats:dec:{yyyymmddhh}` | Hash | compteurs par décision | 90 j |

Les règles sont en base, pas dans le code. Un analyste peut ajuster un poids sans redéploiement, ce qui est la réalité du métier.

### 3.9 Flux d'événements

| Clé | Type | Usage |
|---|---|---|
| `stream:tx` | Stream | toutes les transactions scorées |
| `stream:alerts` | Stream | transactions en `REVIEW` ou `BLOCK` |
| `zset:cases` | Sorted Set | file d'investigation, score = niveau de risque |

```
XADD stream:tx * tx_id T88213 msisdn 22997000001 montant 250000 decision BLOCK score 84
XGROUP CREATE stream:tx enrichissement 0 MKSTREAM
XREADGROUP GROUP enrichissement worker1 COUNT 100 BLOCK 2000 STREAMS stream:tx >
XACK stream:tx enrichissement 1723041293847-0
XAUTOCLAIM stream:tx enrichissement worker2 60000 0 COUNT 50
```

Le consumer group garantit qu'un message est traité une seule fois par groupe, avec une liste des messages en attente qui permet de récupérer le travail d'un worker tombé. C'est ce qui distingue un Stream d'un simple Pub/Sub, où un message perdu l'est définitivement.

### 3.10 Verrous et idempotence

| Clé | Type | Usage |
|---|---|---|
| `lock:sub:{msisdn}` | String | verrou anti concurrence |
| `idem:{tx_id}` | String | garde d'idempotence |

```
SET lock:sub:{22997000001} worker3 NX PX 3000
SET idem:T88213 1 NX EX 86400
```

L'option `NX` combinée à une expiration est le seul moyen sûr de poser un verrou en une opération. Sans expiration, un worker qui meurt bloque l'abonné indéfiniment.

---

## 4. Règles de détection

| Code | Libellé | Structure mobilisée | Poids |
|---|---|---|---|
| R01 | Vélocité d'émission anormale | Sorted Set | 25 |
| R02 | Montant cumulé au dessus du profil | TimeSeries | 20 |
| R03 | Explosion des destinataires distincts | HyperLogLog | 30 |
| R04 | Voyage géographiquement impossible | Geospatial | 35 |
| R05 | Créneau horaire jamais observé | Bitmap | 10 |
| R06 | Destinataire en liste noire | Bloom Filter | 50 |
| R07 | Structuration sous le seuil déclaratif | Sorted Set | 30 |
| R08 | Comportement de compte relais | Sorted Set croisé | 40 |

Seuils de décision : score inférieur à 40 donne `ACCEPT`, de 40 à 70 donne `REVIEW` avec demande d'authentification renforcée, au dessus de 70 donne `BLOCK`.

La règle R07 mérite une explication en soutenance. Le seuil déclaratif oblige à signaler toute transaction supérieure à un certain montant. Un fraudeur découpe donc son opération en plusieurs virements juste en dessous. La détection consiste à repérer, dans la fenêtre glissante, trois transactions ou plus dont le montant se situe entre 90 % et 99 % du seuil.

La règle R08 croise `vel:rcv` et `vel:snd` du même abonné. Un compte qui réémet plus de 85 % de ce qu'il reçoit en moins de dix minutes n'est pas un utilisateur, c'est un point de passage.

---

## 5. Script de scoring atomique

Le coeur du systeme. Toutes les lectures, le calcul et l'ecriture de la decision se font en une seule execution, sans qu'aucune autre commande ne puisse s'intercaler.

Le fichier de reference est `app/scoring/lua/scoring.lua`. Il est reproduit ici en entier, le rapport le reprend en annexe.

### Signature

| Position | Contenu |
|---|---|
| `KEYS[1]` | `vel:snd:{msisdn}` fenetre des emissions |
| `KEYS[2]` | `vel:rcv:{msisdn}` fenetre des receptions |
| `KEYS[3]` | `hll:dst:{msisdn}:{jour}` destinataires distincts |
| `KEYS[4]` | `bmp:hr:{msisdn}` profil horaire |
| `KEYS[5]` | `sub:{msisdn}` profil abonne |
| `KEYS[6]` | `score:{tx_id}` decision produite |
| `KEYS[7]` | `idem:{tx_id}` garde d'idempotence |
| `ARGV[1..5]` | identifiant, montant, horodatage, destinataire, index horaire |
| `ARGV[6..7]` | score et codes deja accumules hors script |
| `ARGV[8]` | seuil declaratif |
| `ARGV[9]` | poids des regles, relus depuis les hash `rule:<code>` |

Deux points de conception a savoir defendre.

**Les poids ne sont pas ecrits en dur.** Ils vivent dans les hash `rule:<code>` et sont pousses au script a chaque appel, avec un cache de trente secondes cote applicatif. Un analyste peut donc reponderer une regle sans redeployer. On les passe en argument plutot que de les lire depuis le script parce qu'en mode Cluster le referentiel des regles se trouve sur un autre slot que les cles de l'abonne : un `HGET` dessus ferait echouer le script.

**Deux regles restent hors du script.** R04 s'appuie sur `geo:last` et R06 sur `bf:blacklist`, deux cles partagees par tous les abonnes et donc situees sur d'autres slots. Elles sont evaluees dans un pipeline, en un seul aller-retour reseau, et leur contribution est passee au script via `ARGV[6]` et `ARGV[7]`. La decision finale reste calculee en un seul endroit.

### Code

```lua
--[[
  Moteur de scoring anti-fraude mobile money.

  Tout le corps de la decision s'execute ici, en une seule operation atomique.
  Redis etant mono-thread, aucune autre commande ne peut s'intercaler entre la
  lecture des compteurs et l'ecriture de la decision. C'est ce qui rend le
  resultat coherent meme sous plusieurs milliers de transactions par seconde.

  KEYS[1] vel:snd:{msisdn}          Sorted Set  transactions emises
  KEYS[2] vel:rcv:{msisdn}          Sorted Set  transactions recues
  KEYS[3] hll:dst:{msisdn}:{jour}   HyperLogLog destinataires distincts du jour
  KEYS[4] bmp:hr:{msisdn}           Bitmap      profil horaire hebdomadaire
  KEYS[5] sub:{msisdn}              Hash        profil abonne
  KEYS[6] score:{tx_id}             Hash        decision produite
  KEYS[7] idem:{tx_id}              String      garde d'idempotence

  ARGV[1]  tx_id
  ARGV[2]  montant
  ARGV[3]  timestamp en millisecondes
  ARGV[4]  msisdn destinataire
  ARGV[5]  index horaire, 0 a 167
  ARGV[6]  score deja accumule par les regles evaluees hors script (R04, R06)
  ARGV[7]  codes de ces regles, separes par des virgules
  ARGV[8]  seuil declaratif, pour la detection de structuration
  ARGV[9]  poids des regles, format "R01:25,R02:20,..."

  Les poids ne sont pas ecrits en dur : ils vivent dans les hash rule:<code>
  et sont pousses ici a chaque appel. Un analyste peut donc reponderer une
  regle sans redeploiement. On les passe en argument plutot que de les lire
  depuis le script, parce qu'en mode Cluster le referentiel des regles se
  trouve sur un autre slot que les cles de l'abonne.
]]

local now     = tonumber(ARGV[3])
local montant = tonumber(ARGV[2])
local seuil   = tonumber(ARGV[8])

local FENETRE_COURTE = 300000      -- 5 minutes
local FENETRE_RELAIS = 600000      -- 10 minutes
local RETENTION      = 604800000   -- 7 jours

local score  = tonumber(ARGV[6]) or 0
local regles = {}

-- Table des poids, reconstruite a partir de ARGV[9].
local poids = {}
for code, valeur in string.gmatch(ARGV[9] or '', '(R%d+):(%d+)') do
  poids[code] = tonumber(valeur)
end

local function appliquer(code, defaut)
  score = score + (poids[code] or defaut)
  table.insert(regles, code)
end

if ARGV[7] ~= nil and ARGV[7] ~= '' then
  for code in string.gmatch(ARGV[7], '([^,]+)') do
    table.insert(regles, code)
  end
end

-- Garde d'idempotence.
-- Un rejeu de la meme transaction ne doit ni rescorer ni polluer les compteurs.
local premiere_vue = redis.call('SET', KEYS[7], 1, 'NX', 'EX', 86400)
if not premiere_vue then
  local decision = redis.call('HGET', KEYS[6], 'decision')
  if decision then
    return {decision, redis.call('HGET', KEYS[6], 'score'), 'REJEU'}
  end
end

-- Purge de la fenetre glissante.
-- Le TTL ne suffit pas ici : il porte sur la cle entiere, pas sur ses membres.
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - RETENTION)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - RETENTION)

local debut_courte = now - FENETRE_COURTE

--------------------------------------------------------------------------
-- R01  Velocite d'emission anormale
--------------------------------------------------------------------------
local nb_envois = redis.call('ZCOUNT', KEYS[1], debut_courte, now)
if nb_envois >= 8 then
  appliquer('R01', 25)
end

--------------------------------------------------------------------------
-- R02  Montant cumule au dessus du profil
--------------------------------------------------------------------------
local recents = redis.call('ZRANGEBYSCORE', KEYS[1], debut_courte, now)
local cumul = 0
local sous_seuil = 0

for i = 1, #recents do
  local m = tonumber(string.match(recents[i], '|(%d+)$'))
  if m then
    cumul = cumul + m
    if seuil > 0 and m >= seuil * 0.90 and m < seuil then
      sous_seuil = sous_seuil + 1
    end
  end
end

local plafond = tonumber(redis.call('HGET', KEYS[5], 'plafond_journalier') or 0)
if plafond > 0 and (cumul + montant) > plafond * 0.8 then
  appliquer('R02', 20)
end

--------------------------------------------------------------------------
-- R03  Explosion du nombre de destinataires distincts
--------------------------------------------------------------------------
redis.call('PFADD', KEYS[3], ARGV[4])
local nb_destinataires = redis.call('PFCOUNT', KEYS[3])
if nb_destinataires >= 25 then
  appliquer('R03', 30)
end

--------------------------------------------------------------------------
-- R05  Creneau horaire jamais observe pour cet abonne
--------------------------------------------------------------------------
local index_horaire = tonumber(ARGV[5])
if redis.call('GETBIT', KEYS[4], index_horaire) == 0 then
  appliquer('R05', 10)
end
redis.call('SETBIT', KEYS[4], index_horaire, 1)

--------------------------------------------------------------------------
-- R07  Structuration sous le seuil declaratif
--   Le fraudeur decoupe une grosse operation en virements juste en dessous
--   du montant qui declenche une declaration reglementaire.
--------------------------------------------------------------------------
if seuil > 0 and montant >= seuil * 0.90 and montant < seuil then
  sous_seuil = sous_seuil + 1
end
if sous_seuil >= 3 then
  appliquer('R07', 30)
end

--------------------------------------------------------------------------
-- R08  Comportement de compte relais
--   Un compte qui reemet presque tout ce qu'il recoit, en quelques minutes,
--   n'est pas un utilisateur mais un point de passage.
--------------------------------------------------------------------------
local recus = redis.call('ZRANGEBYSCORE', KEYS[2], now - FENETRE_RELAIS, now)
local entrant = 0
for i = 1, #recus do
  local m = tonumber(string.match(recus[i], '|(%d+)$'))
  if m then entrant = entrant + m end
end
if entrant > 0 then
  local ratio = (cumul + montant) / entrant
  if ratio >= 0.85 and #recus >= 2 then
    appliquer('R08', 40)
  end
end

--------------------------------------------------------------------------
-- Decision
--------------------------------------------------------------------------
local decision = 'ACCEPT'
if score >= 70 then
  decision = 'BLOCK'
elseif score >= 40 then
  decision = 'REVIEW'
end

-- Une transaction bloquee n'alimente pas les compteurs de comportement
-- legitime, sinon le fraudeur deplacerait lui meme ses propres seuils.
if decision ~= 'BLOCK' then
  redis.call('ZADD', KEYS[1], now, ARGV[1] .. '|' .. ARGV[2])
  redis.call('EXPIRE', KEYS[1], 604800)
end

local codes = table.concat(regles, ',')

redis.call('HSET', KEYS[6],
  'tx_id', ARGV[1],
  'score', score,
  'decision', decision,
  'regles', codes,
  'montant', ARGV[2],
  'destinataire', ARGV[4],
  'nb_envois_5min', nb_envois,
  'cumul_5min', cumul,
  'destinataires_jour', nb_destinataires,
  'ts', now)
redis.call('EXPIRE', KEYS[6], 604800)

redis.call('PEXPIRE', KEYS[3], 2592000000)
redis.call('PERSIST', KEYS[4])

return {decision, tostring(score), codes}
```

### Chargement et appel

```
SCRIPT LOAD "$(cat scoring.lua)"
EVALSHA <sha1> 7 \
  vel:snd:{22997000001} vel:rcv:{22997000001} \
  hll:dst:{22997000001}:20260807 bmp:hr:{22997000001} \
  sub:{22997000001} score:T88213 idem:T88213 \
  T88213 250000 1754582400000 22997000455 51 0 "" 500000 \
  "R01:25,R02:20,R03:30,R04:35,R05:10,R06:50,R07:30,R08:40"
```

`EVALSHA` plutot que `EVAL` : le corps du script ne transite qu'une fois sur le reseau, ensuite seule son empreinte SHA1 de quarante octets est envoyee. Apres un redemarrage de Redis, le cache de scripts est vide et l'appel renvoie `NOSCRIPT` : le moteur intercepte cette erreur et recharge automatiquement.

---

## 6. Ce que Redis ne fait pas

Section indispensable pour la crédibilité du rapport.

**Pas de rollback transactionnel.** `MULTI/EXEC` garantit que les commandes ne sont pas entrelacées, pas qu'elles réussissent ou échouent ensemble. Une commande qui échoue à l'exécution n'annule pas les précédentes. Le script Lua offre la même garantie d'isolation, avec en plus la possibilité de décider en cours de route.

**Pas de durabilité forte par défaut.** La réplication est asynchrone. `WAIT numreplicas timeout` indique combien de réplicas ont accusé réception, mais ne bloque pas l'écriture et ne l'annule pas en cas d'échec. Une bascule de master peut donc perdre les dernières écritures.

**Pas d'opération multi clés en Cluster hors slot commun.** D'où les hash tags. Une jointure entre deux abonnés est impossible sans passage par le client.

**Pas de requête analytique.** Aucun équivalent de `GROUP BY`. Toute agrégation doit être précalculée à l'écriture. C'est un renversement complet de la logique relationnelle, où l'on stocke brut et l'on agrège à la lecture.

C'est précisément pour ces raisons que l'architecture reste polyglotte : le relationnel conserve l'historique froid, l'audit réglementaire et les dossiers de fraude clos, où la cohérence forte et la capacité analytique priment sur la latence.

---

## 7. Correspondance avec le modèle relationnel

| Concept relationnel | Équivalent Redis | Perte ou gain |
|---|---|---|
| Table | Espace de clés par convention de nommage | Perte du schéma imposé |
| Ligne | Hash | Gain de l'accès direct par clé |
| Clé primaire | La clé elle-même | Gain, plus d'index à maintenir |
| Index secondaire | Sorted Set construit manuellement | Perte, à la charge du développeur |
| Jointure | Aucune, dénormalisation ou aller-retour client | Perte |
| `GROUP BY` sur fenêtre | Compteur entretenu à l'écriture | Gain massif en latence, perte de souplesse |
| `DELETE` planifié | TTL natif | Gain, plus de job de purge |
| Contrainte d'unicité | `SET NX` | Équivalent |
| Contrainte d'intégrité référentielle | Aucune | Perte totale, à gérer applicativement |

La colonne de droite est plus importante que les deux autres. Un rapport qui ne liste que les gains n'est pas crédible.
