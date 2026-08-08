# Script de la démonstration

## Soutenance de quinze minutes, avec le texte à dire

Ce document est un script de scène. Le texte en italique est ce que vous dites à voix haute. Les blocs encadrés sont les commandes à taper. Les paragraphes ordinaires expliquent pourquoi cette séquence figure dans la démonstration, et ce qu'il faut faire si elle échoue.

Le principe qui gouverne l'ensemble est le suivant : une démonstration en direct rate toujours quelque chose. L'objectif n'est donc pas que rien ne casse, mais qu'aucun incident ne coûte plus de trente secondes.

---

## Ce qu'il faut avoir fait avant d'entrer

La construction des images Docker se fait la veille au soir, jamais devant le jury. Le téléchargement des images de base prend une dizaine de minutes sur une connexion de salle de cours, et ce temps est perdu.

La pile complète doit avoir été lancée au moins une fois en entier, et chacun des sept scénarios rejoué, pour vérifier qu'il produit bien la décision attendue. Un scénario que l'on découvre en direct est un scénario qui échoue.

Une capture vidéo de la démonstration complète doit être enregistrée sur la machine. Elle sert de filet si le portable refuse de se connecter au vidéoprojecteur, ce qui arrive plus souvent qu'on ne le croit.

Le terminal doit être réglé sur un thème clair et une taille d'au moins seize points. Un terminal sombre en onze points est illisible depuis le fond de la salle, et le jury ne dira rien mais ne suivra plus.

Enfin, la pile doit être lancée cinq minutes avant d'entrer, avec le trafic de fond déjà en cours, de sorte que la console ait un historique lorsque vous l'affichez. Une console vide n'impressionne personne.

Vérifiez aussi que le projet ne se trouve pas dans un dossier synchronisé par OneDrive (ou équivalent) : la synchronisation en tâche de fond peut ralentir Docker au point de perturber Sentinel. Sur la machine de la démonstration, gardez le PC branché sur secteur, désactivez la mise en veille, et confirmez que la réplication est saine avant d'entrer :

```
docker compose exec redis_node1 redis-cli -a momo2026 INFO replication
```
`connected_slaves:2` avec les deux répliques en `state=online` — sinon, redémarrez la pile (`docker compose down -v && docker compose up -d`) et attendez qu'elle se stabilise avant de continuer.

```
docker compose up -d
docker compose exec -d web1 python manage.py simulate --charge 40 --duree 1200
```

L'écran est organisé en trois zones visibles simultanément, sans qu'il soit jamais nécessaire de changer de fenêtre. Le navigateur occupe la moitié gauche et affiche la console. La moitié droite est partagée entre deux terminaux, l'un pour les scénarios, l'autre pour l'inspection de Redis.

---

## Première séquence, de zéro à une minute trente

### Poser le problème avant de montrer quoi que ce soit

N'ouvrez pas un terminal tout de suite. Commencez par une phrase et un chiffre, parce que le jury doit comprendre la contrainte avant de voir la solution, faute de quoi tout ce qui suit paraîtra gratuit.

> *Un opérateur de mobile money traite jusqu'à cinq mille transactions par seconde aux heures de pointe. Chacune doit être acceptée ou bloquée avant que l'abonné voie son écran de confirmation, ce qui laisse environ vingt millisecondes.*
>
> *Pour décider, il faut savoir combien de virements ce compte a émis dans les cinq dernières minutes, vers combien de destinataires différents il a envoyé depuis ce matin, et depuis quel endroit il opérait la dernière fois. Ce sont trois agrégations sur des fenêtres glissantes. Sur une table de plusieurs milliards de lignes, cela prend plusieurs centaines de millisecondes, même bien indexée.*
>
> *C'est cet écart, d'un ordre de grandeur, qui nous a conduits à Redis. Voici ce que cela donne en fonctionnement.*

Affichez alors la console, déjà alimentée. Laissez deux secondes de silence pour que le jury la parcoure des yeux.

---

## Deuxième séquence, de une minute trente à trois minutes

### La couche applicative, pour l'écarter tout de suite

Cette séquence sert à évacuer un sujet qui n'est pas le nôtre. L'architecture comporte une répartition de charge, il faut la montrer, mais il faut surtout dire clairement qu'elle est indépendante de la couche donnée, sinon le jury risque de confondre les deux.

> *Avant d'entrer dans Redis, un mot sur la couche applicative. Apache répartit les requêtes sur trois instances Django à tour de rôle. Chaque instance renvoie son nom dans un en-tête, ce qui rend la répartition observable.*

```
for i in $(seq 1 6); do
  curl -s -D- -o /dev/null http://localhost:8081/v1/sante | grep -i x-instance
done
```

Les trois noms se succèdent. Arrêtez alors une instance en direct.

```
docker compose stop web2
```

Relancez la boucle. Seules deux instances répondent, et la console ne bronche pas. Remettez la troisième en service.

```
docker compose start web2
```

> *Ce que vous venez de voir n'a rien de spécifique à Redis, c'est de la haute disponibilité applicative classique. Je le montre maintenant précisément pour l'écarter : tout ce qui suit porte sur la couche donnée, et les deux sujets sont indépendants.*

Cette transition est importante. Elle vous fait entrer dans le vif du sujet en ayant fermé une porte.

---

## Troisième séquence, de trois minutes à quatre minutes

### L'état de départ d'un abonné, et le détail qui compte

Avant de déclencher quoi que ce soit, montrez à quoi ressemble un abonné vierge. Cela donne un point de comparaison pour tout ce qui suit.

```
curl -s http://localhost:8081/v1/abonnes/22997000051 | python -m json.tool
```

Les compteurs sont à zéro. Ce qui doit retenir l'attention, ce ne sont pas les valeurs mais les noms de clés, et c'est le moment de placer le point technique le plus discret de tout le projet.

> *Regardez le nom de la clé de vélocité : `vel:snd:{22997000051}`. Les accolades ne sont pas décoratives. En mode Cluster, Redis ne calcule le hachage que sur la portion entre accolades pour choisir le nœud qui hébergera la clé. C'est ce qui garantit que les sept clés de cet abonné vivent sur le même serveur.*
>
> *Sans ce détail, le script atomique que je vais montrer dans un instant ne pourrait tout simplement pas s'exécuter en mode Cluster, parce qu'un script ne peut pas porter sur des clés réparties sur plusieurs nœuds.*

Vous venez de montrer que la modélisation a été pensée pour la mise à l'échelle. Enchaînez.

> *Maintenant que le compte est propre, faisons-le se comporter comme un compte volé.*

---

## Quatrième séquence, de quatre minutes à huit minutes trente

### Les scénarios de fraude, une règle après l'autre

C'est le cœur de la démonstration. Chaque scénario suit le même rythme : une phrase qui annonce le schéma de fraude, la commande, puis l'ouverture de la structure Redis sous-jacente. Après chaque scénario, montrez la ligne qui s'allume dans l'échelle des règles de la console.

#### Vélocité, portée par un Sorted Set

> *Premier schéma, le plus simple. Un compte dont le code secret a été obtenu par ingénierie sociale se vide en quelques minutes. La signature est une accélération brutale du rythme d'émission.*

```
docker compose exec web1 python manage.py simulate --scenario velocite
```

Le score monte transaction après transaction, et la règle se déclenche à partir de la huitième. Ouvrez maintenant la structure elle-même dans le second terminal.

```
docker compose exec redis_node1 redis-cli -a momo2026 \
  ZRANGE "vel:snd:{22997000051}" 0 -1 WITHSCORES
```

> *Voici la fenêtre glissante. Le score du Sorted Set porte l'horodatage en millisecondes, et le membre porte l'identifiant de la transaction et son montant. Compter les transactions des cinq dernières minutes revient donc à compter les membres dont le score se situe dans une plage, ce qui se fait en temps logarithmique. Avec une simple liste, il faudrait parcourir depuis le début.*

#### Structuration, portée par la même structure lue autrement

Enchaînez directement, parce que c'est la même structure et que la comparaison est éclairante.

> *Le schéma suivant utilise exactement la même structure, mais lue autrement. La réglementation impose une déclaration au-delà de cinq cent mille francs. Le fraudeur envoie donc quatre fois quatre cent quatre-vingt-dix mille.*

```
docker compose exec web1 python manage.py simulate --scenario structuration
```

> *Chaque transaction prise isolément est parfaitement légale. Aucune ne pourrait être bloquée seule. C'est la fenêtre qui révèle le schéma, et vous voyez le score monter jusqu'à la mise en revue.*

#### Dispersion, portée par un HyperLogLog

> *Troisième schéma. Plutôt qu'un seul transfert repérable, le fraudeur éclate la somme vers un grand nombre de destinataires créés pour l'occasion.*

```
docker compose exec web1 python manage.py simulate --scenario eventail
docker compose exec redis_node1 redis-cli -a momo2026 \
  MEMORY USAGE "hll:dst:{22997000052}:$(date -u +%Y%m%d)"
```

> *Trente destinataires distincts comptés. Regardez la mémoire consommée : elle est fixe. Un ensemble exact conserverait chaque numéro, environ soixante octets par entrée. Sur huit millions d'abonnés actifs, la différence se compte en dizaines de gigaoctets.*
>
> *Le HyperLogLog est approximatif, avec une erreur standard de zéro virgule huit pour cent. Cela peut sembler gênant pour fonder un blocage, mais la règle ne cherche pas une valeur précise : elle détecte un changement de régime. Un compte qui passe de trois destinataires à quatre cents est détecté, que le compte exact soit trois cent quatre-vingt-seize ou quatre cent quatre.*

#### Incohérence géographique, portée par le type Geospatial

> *Quatrième schéma, plus visuel. Deux transactions du même abonné séparées de deux minutes, l'une à Cotonou, l'autre à Dakar.*

```
docker compose exec web1 python manage.py simulate --scenario voyage
```

> *Seize cents kilomètres en deux minutes. Aucun déplacement réel ne relie ces deux points.*
>
> *Une remarque au passage sur l'implémentation. Le type Geospatial de Redis n'est pas une structure nouvelle : c'est un Sorted Set dont le score encode la position sur cinquante-deux bits. Redis ne multiplie pas ses structures internes, il réutilise celles qu'il a déjà, et c'est une constante de sa conception.*

#### Liste noire, portée par un filtre de Bloom

> *Cinquième vérification, la plus simple en apparence. Le destinataire figure-t-il parmi plusieurs millions de numéros déjà signalés.*

```
docker compose exec web1 python manage.py simulate --scenario listenoire
docker compose exec redis_node1 redis-cli -a momo2026 MEMORY USAGE bf:blacklist
```

> *Dix-sept mégaoctets pour dix millions d'entrées, contre plus de six cents pour un ensemble exact. Le filtre de Bloom peut se tromper, mais pas dans n'importe quel sens : il peut affirmer à tort qu'un numéro est présent, jamais qu'il est absent.*
>
> *Un faux positif envoie une transaction légitime en revue humaine, ce qui coûte une vérification. Un faux négatif, qui laisserait passer un numéro réellement signalé, est impossible par construction. C'est exactement la propriété qu'on veut sur une liste noire, et c'est ce qui rend l'approximation non seulement acceptable mais préférable.*

#### Compte relais, porté par deux Sorted Set croisés

> *Dernier schéma, et le plus intéressant, parce qu'il croise deux structures.*

```
docker compose exec web1 python manage.py simulate --scenario relais
```

> *Ce compte reçoit de trois sources différentes, puis réémet quatre-vingt-quinze pour cent de ce qu'il a reçu en moins de dix minutes. Ce n'est pas un utilisateur, c'est un point de passage. Le métier appelle cela une mule, et c'est le maillon par lequel l'argent volé sort du système.*

Marquez une pause ici et faites la transition vers la partie technique.

> *Vous avez vu six schémas et six structures Redis différentes. Reste la question qui les tient ensemble : comment garantir que ces six lectures et l'écriture de la décision forment bien une seule opération.*

---

## Cinquième séquence, de huit minutes trente à dix minutes

### Le script atomique et son revers

Affichez le fichier `scoring.lua` à l'écran. Ne le lisez pas, montrez-en la structure.

> *Tout ce que vous venez de voir se passe ici, dans un seul script exécuté par Redis. Il lit les compteurs, évalue les règles, calcule un score, en déduit une décision, l'écrit, et n'alimente les compteurs de comportement que si la transaction n'a pas été bloquée.*
>
> *Redis exécute les commandes sur un seul fil. Pendant que ce script tourne, aucune autre commande ne peut s'intercaler. Les six règles lisent donc des compteurs cohérents entre eux, et la décision est écrite dans le même souffle.*

Anticipez ici la question qui vient toujours, sur la différence avec une transaction classique.

> *On me demande souvent pourquoi ne pas utiliser MULTI et EXEC. La raison est simple : MULTI empile les commandes avant d'en connaître le moindre résultat, il ne peut donc contenir aucune condition. Or notre logique doit décider en fonction de ce qu'elle lit. Le script, lui, peut brancher.*
>
> *Et il faut ajouter que MULTI ne fait pas ce qu'on croit. Il garantit l'isolation, pas l'atomicité au sens relationnel : si une commande du bloc échoue, les précédentes ne sont pas annulées. Il n'y a pas de retour arrière dans Redis.*

Montrez immédiatement la contrepartie, parce que c'est ce qui distingue une présentation honnête d'un argumentaire.

```
docker compose exec redis_node1 redis-cli -a momo2026 SLOWLOG GET 5
```

> *Le revers du fil unique, c'est qu'un script lent bloque tout le serveur. Un script qui prendrait cent millisecondes immobiliserait toutes les requêtes en attente pendant cent millisecondes. C'est pourquoi notre configuration enregistre toute commande dépassant dix millisecondes : sur ce type de serveur, c'est le premier endroit où regarder quand la latence dérape.*

---

## Sixième séquence, de dix minutes à onze minutes trente

### La tenue en charge

> *Passons maintenant à la charge, puisque c'est la contrainte de départ.*

```
docker compose exec web1 python manage.py simulate --charge 400 --duree 45
```

Laissez la console parler pendant une vingtaine de secondes. Ne commentez pas en continu, la jauge de latence est plus convaincante que vos phrases.

> *Quatre cents transactions par seconde envoyées depuis un seul conteneur, sur un ordinateur portable. Le centile 99 reste sous le repère des vingt millisecondes.*
>
> *Je précise que ce n'est pas la limite de Redis, c'est la limite de notre générateur : nous saturons le processus Python qui envoie, pas la base qui répond. En production, le facteur limitant serait le nombre d'instances applicatives, pas le serveur Redis.*

Cette précision vous protège d'une question désagréable et montre que vous savez ce que vous mesurez.

---

## Septième séquence, de onze minutes trente à treize minutes

### Ce qui se passe quand ça casse

C'est la séquence qui distingue un projet d'étudiant d'un projet d'ingénieur, et c'est celle que les autres groupes n'auront pas. Cassez volontairement, l'un après l'autre, trois éléments.

> *Une architecture ne se juge pas quand tout va bien. Je vais donc casser trois choses.*

#### La perte du processus d'archivage

```
docker compose stop worker
docker compose exec web1 python manage.py simulate --scenario velocite
docker compose exec redis_node1 redis-cli -a momo2026 XPENDING stream:tx enrichissement
docker compose start worker
```

> *Le processus d'archivage était arrêté. Les décisions ont continué d'être rendues, l'abonné n'a rien vu, parce que l'archivage est hors du chemin critique.*
>
> *Les messages sont restés dans la liste d'attente du groupe de consommateurs, et vous les voyez repris au redémarrage. C'est la différence concrète entre un Stream et un mécanisme de publication et abonnement : avec ce dernier, ces messages auraient été perdus définitivement.*

#### La perte de Redis lui-même

```
docker compose stop redis_node1
curl -s -X POST http://localhost:8081/v1/transactions \
  -H 'Content-Type: application/json' \
  -d '{"msisdn":"22997000042","destinataire":"22997000099","montant":5000}'
docker compose start redis_node1
```

> *La réponse est REVIEW, pas ACCEPT. C'est une politique que nous avons choisie explicitement : quand le moteur est aveugle, on ne laisse pas passer, on met en revue. Un faux positif coûte une vérification, un faux négatif coûte de l'argent.*

Au redémarrage, une erreur apparaît une fois dans les journaux. Prenez les devants plutôt que de la laisser découvrir.

> *Vous voyez passer une erreur NOSCRIPT dans les journaux. Elle est normale : au redémarrage, le cache des scripts de Redis est vide. Notre moteur intercepte cette erreur, recharge le script et rejoue l'appel, ce qui rend le redémarrage transparent pour l'appelant.*

#### Le rejeu d'une transaction

```
docker compose exec web1 python manage.py simulate --scenario idempotence
```

> *Même identifiant soumis trois fois, même décision rendue, et les compteurs n'ont été alimentés qu'une seule fois. Ce n'est pas un cas théorique : en mobile money, les passerelles réémettent dès qu'un accusé de réception tarde, et une transaction rejouée qui alimenterait deux fois les compteurs fausserait toute la détection.*

---

## Huitième séquence, de treize minutes à quinze minutes

### Terminer par ce que Redis ne sait pas faire

C'est contre-intuitif de finir sur les limites, et c'est précisément pour cela que ça marche. Un jury retient la dernière chose qu'il entend, et entendre une équipe énoncer elle-même les faiblesses de son choix inspire davantage confiance qu'un argumentaire sans faille.

```
docker compose exec postgres psql -U momo -d momo -c \
  "SELECT decision, count(*), round(avg(latence_ms)::numeric,2) AS latence_moyenne
   FROM scoring_decisionarchivee GROUP BY decision;"
```

> *Cette requête, Redis ne sait pas la faire. Il n'y a pas de GROUP BY, pas d'agrégation à la lecture, pas d'index secondaire. Toute statistique doit avoir été précalculée à l'écriture, ce qui suppose de savoir à l'avance quelle question sera posée. Or un analyste de la fraude pose par définition des questions nouvelles.*
>
> *Et le régulateur exige cinq ans de traçabilité sur les décisions de blocage, alors que nos compteurs Redis expirent au bout de sept jours.*

Faites alors la conclusion, qui est la thèse de tout le projet.

> *Nous n'avons donc pas choisi Redis contre le relationnel. Nous avons réparti les rôles. Redis porte le comportement récent, où la latence prime et où perdre une seconde de compteurs après une panne est sans conséquence. PostgreSQL porte l'audit et l'analyse, où la durabilité et l'expressivité priment et où la latence n'a aucune importance.*
>
> *Ce projet ne démontre pas que Redis est meilleur qu'une base relationnelle. Il démontre où il est le seul possible, et où il ne l'est pas.*

---

## Annexe facultative — la haute disponibilité

Cette séquence n'entre pas dans les quinze minutes chronométrées : ne la jouez en direct que si le jury pose explicitement la question, ou s'il reste du temps en fin de session. Le mécanisme de bascule dépend d'une horloge interne régulière côté Sentinel, ce qui le rend sensible aux ralentissements de la machine hôte (portable sur batterie, autre logiciel gourmand en tâche de fond) — testez-la sur la machine de la salle avant d'en faire une démonstration en direct, et gardez à défaut une capture vidéo de secours.

> *Le master est aujourd'hui répliqué sur deux autres nœuds, chacun surveillé par une instance Sentinel. Si le master tombe, deux des trois Sentinels doivent s'accorder pour déclencher une bascule automatique.*

```
docker compose exec redis_node1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
docker compose stop redis_node1
```

Attendez quinze à vingt secondes — le temps que Sentinel détecte la panne (cinq secondes) puis mène l'élection.

```
docker compose exec redis_node2 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
```

> *L'adresse a changé : une réplique a été promue. L'application ne s'est jamais arrêtée, parce qu'elle ne se connecte jamais à une adresse fixe — elle interroge Sentinel à chaque fois pour savoir qui est le master courant.*

```
docker compose exec web1 python manage.py simulate --charge 10 --duree 10
```

Ceci confirme que le trafic continue d'être traité sans redémarrage d'aucune instance applicative. Remettez ensuite l'ancien master en service, il rejoint automatiquement le groupe comme réplique.

```
docker compose start redis_node1
```

---

## Si quelque chose tourne mal

| Ce qui arrive | Ce que vous faites |
|---|---|
| La console reste vide | `docker compose exec web1 python manage.py init_redis --reinitialiser` puis relancez le trafic de fond |
| Un scénario ne déclenche pas sa règle | Passez au suivant sans commenter, revenez-y plus tard avec `--msisdn 229970001xx` sur un abonné neuf |
| Redis refuse de redémarrer | `docker compose down -v && docker compose up -d`, ce qui prend une trentaine de secondes pendant lesquelles vous commentez le fichier de configuration |
| Le vidéoprojecteur lâche | Basculez sur la capture vidéo préparée, et continuez le commentaire par-dessus |
| Une commande renvoie une erreur inattendue | Dites simplement ce que vous attendiez, passez à la suite, et proposez d'y revenir à la fin |
| Une question dont personne n'a la réponse | « Nous ne l'avons pas testé, voici comment nous le vérifierions » vaut infiniment mieux qu'une improvisation |

---

## La répétition

Faites la démonstration en entier trois fois, chronomètre en main. La troisième doit tenir en quatorze minutes, pour absorber les imprévus du jour.

Faites la troisième répétition avec un membre du groupe qui interrompt au hasard et pose des questions. C'est le seul moyen de vérifier que vous pouvez reprendre le fil après une interruption, ce qui arrivera.

Répartissez la parole clairement : celui qui parle ne tape pas, celui qui tape ne parle pas. Deux personnes suffisent à la démonstration. Les deux autres se tiennent prêtes pour les questions, chacune sur la partie du rapport qu'elle a rédigée.

Enfin, préparez les huit questions listées à la fin du plan du rapport. Elles couvrent l'essentiel de ce qu'un correcteur peut demander, et la huitième, celle sur les faux positifs, n'a pas de bonne réponse dans le projet en l'état. Répondez-y franchement en proposant la méthode d'évaluation plutôt qu'en improvisant un chiffre.
