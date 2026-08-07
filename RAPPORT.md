# Redis, base de données clé-valeur en mémoire

## Application à un moteur de scoring anti-fraude pour le mobile money

**Dakar Institute of Technology**
Master Intelligence Artificielle, promotion 2025 / 2026
Cours de Bases de données NoSQL

Membres du groupe :
Nom Prénom (numéro d'étudiant)
Nom Prénom (numéro d'étudiant)
Nom Prénom (numéro d'étudiant)
Nom Prénom (numéro d'étudiant)

Août 2026

---

## Sommaire

Introduction

Partie 1. Architecture interne de Redis
1.1 Un serveur qui ne fait qu'une chose à la fois
1.2 Les structures internes et leurs encodages
1.3 Deux mécanismes de persistance pour deux besoins
1.4 De la copie simple au partitionnement
1.5 La mémoire comme ressource finie
1.6 Les modules

Partie 2. Le modèle de données
2.1 Les types natifs
2.2 Ce que l'on gagne et ce que l'on perd par rapport au relationnel
2.3 Modélisation du cas d'usage
2.4 Dimensionnement

Partie 3. Le modèle de cohérence
3.1 La cohérence sur une instance unique
3.2 Ce que MULTI et EXEC garantissent réellement
3.3 Les scripts Lua
3.4 La durabilité et ses limites
3.5 La cohérence en mode Cluster
3.6 Où s'arrête Redis, où commence le relationnel

Partie 4. Le cas d'usage
4.1 Le problème métier
4.2 Justification du choix technologique
4.3 L'architecture déployée
4.4 Le parcours d'une transaction
4.5 Les requêtes critiques en langage natif
4.6 Mesures
4.7 Ce que le projet ne traite pas

Conclusion

Annexes

---

## Introduction

Le mobile money a changé la manière dont circule l'argent en Afrique de l'Ouest. Un abonné qui n'a jamais eu de compte bancaire envoie aujourd'hui de l'argent à sa famille depuis un téléphone à touches, paie sa facture d'électricité et reçoit son salaire sur le même numéro. Les volumes qui transitent par ces plateformes se comptent en milliers de milliards de francs CFA par an, et les opérateurs qui les exploitent traitent aux heures de pointe plusieurs milliers d'opérations par seconde.

Cette réussite a créé son propre problème. Là où circule beaucoup d'argent apparaissent des fraudeurs, et les schémas qu'ils emploient sont désormais bien identifiés. Un compte volé se vide en quelques minutes vers une série de destinataires inconnus. Un fraudeur qui doit déplacer une grosse somme la découpe en plusieurs virements pour rester sous le seuil qui déclencherait une déclaration réglementaire. Des comptes recrutés pour l'occasion, que le métier appelle des mules, reçoivent de l'argent de plusieurs sources et le réémettent aussitôt pour brouiller la piste. Ces comportements ont un point commun : aucune transaction prise isolément n'est anormale. C'est leur enchaînement dans le temps qui les trahit.

Détecter cet enchaînement suppose de savoir, au moment précis où une transaction se présente, combien de virements ce compte a émis dans les cinq dernières minutes, vers combien de destinataires différents il a envoyé de l'argent depuis ce matin, depuis quelle position géographique il opérait la dernière fois, et si l'heure à laquelle il agit correspond à ses habitudes. Autrement dit, il faut interroger quatre agrégations sur des fenêtres temporelles glissantes.

C'est là que la contrainte devient technique. La décision ne peut pas être prise après coup, parce qu'une fois l'argent parti il ne revient pas. Elle doit intervenir dans le chemin critique du paiement, avant que l'abonné voie son écran de confirmation, ce qui laisse un budget de l'ordre de vingt millisecondes. Sur une base relationnelle contenant plusieurs milliards de transactions historiques, ces quatre agrégations demandent plusieurs centaines de millisecondes, même correctement indexées. L'écart est d'un ordre de grandeur, et aucun réglage ne le comble.

Ce rapport présente Redis comme réponse à cette contrainte. Nous avons choisi cette base parmi les quatre proposées parce qu'elle est la seule dont le modèle de données offre nativement les structures dont la détection a besoin, et parce que son architecture en mémoire tient le budget de latence par construction plutôt que par optimisation.

Nous procédons en quatre temps. La première partie décrit l'architecture interne de Redis, c'est-à-dire ce qui se passe dans le serveur lorsqu'une commande arrive. La deuxième présente son modèle de données, le confronte au modèle relationnel et en tire la modélisation retenue pour notre cas d'usage. La troisième examine les garanties de cohérence offertes, et surtout celles qui ne le sont pas, car c'est de cet examen que dépend la validité de l'architecture. La quatrième expose le cas d'usage lui-même, la justification du choix technologique, les requêtes critiques et les mesures obtenues.

Une précision sur la démarche. Nous avons construit un prototype fonctionnel, déployé sous Docker, dont le code accompagne ce rapport. Les affirmations techniques qui suivent ont été vérifiées sur ce prototype chaque fois que c'était possible, et nous le signalons lorsque ce n'est pas le cas.

---

## Partie 1. Architecture interne de Redis

Avant de modéliser quoi que ce soit, il faut comprendre ce qui se passe dans le serveur. Beaucoup de choix que nous avons faits en Partie 2 et beaucoup de limites que nous acceptons en Partie 3 découlent directement de décisions d'architecture prises par les concepteurs de Redis. Cette première partie les décrit.

### 1.1 Un serveur qui ne fait qu'une chose à la fois

La caractéristique la plus surprenante de Redis, et celle dont tout le reste découle, est qu'il exécute les commandes sur un seul fil d'exécution. Là où PostgreSQL lance un processus par connexion et où Cassandra répartit le travail sur un pool de fils, Redis traite les commandes une par une, dans l'ordre où elles arrivent.

Ce fonctionnement repose sur une boucle d'événements. Le serveur surveille simultanément toutes les connexions ouvertes au moyen d'un mécanisme de multiplexage fourni par le système d'exploitation, epoll sous Linux et kqueue sous BSD. Lorsqu'une ou plusieurs connexions ont des données à lire, le système le signale, la boucle lit les commandes, les exécute l'une après l'autre, écrit les réponses, puis se remet en attente. Il n'y a jamais deux commandes en cours d'exécution au même instant.

Cette décision paraît contre-intuitive à l'heure des processeurs à seize cœurs. Elle se comprend dès que l'on remarque que Redis ne fait presque jamais d'entrées et sorties disque pendant qu'il sert une requête. Toutes les données sont en mémoire vive. Le coût d'une commande se réduit donc à quelques accès mémoire et à un peu de calcul, ce qui se compte en microsecondes. Dans ces conditions, le goulet d'étranglement n'est pas le processeur mais la carte réseau, et paralléliser l'exécution n'apporterait presque rien tout en imposant des verrous.

Le mono-thread offre en revanche quelque chose de considérable, et c'est précisément ce que nous exploitons dans ce projet. Toutes les opérations sont sérialisées par construction. Il n'existe aucune condition de course entre deux clients, aucun verrou à poser, aucun risque de lecture d'un état intermédiaire. Une commande voit toujours un état cohérent, celui laissé par la commande précédente.

La contrepartie est tout aussi importante et nous y reviendrons. Puisqu'il n'y a qu'un fil, une commande lente bloque toutes les autres. Une seule opération qui prend cent millisecondes, par exemple un KEYS sur une base contenant des millions de clés, immobilise le serveur pendant cent millisecondes, et toutes les requêtes en attente subissent ce retard. C'est la raison pour laquelle notre fichier de configuration règle le journal des commandes lentes à dix millisecondes, comme le montre l'annexe B : sur ce type de serveur, une commande lente est le premier symptôme à surveiller.

Une précision s'impose, car elle est fréquemment mal comprise. Depuis la version 6, Redis peut utiliser plusieurs fils pour lire les requêtes sur le réseau et écrire les réponses. L'exécution des commandes, elle, reste strictement mono-thread. Dire que Redis est devenu multi-thread est donc inexact, et les garanties décrites ci-dessus restent entières.

### 1.2 Les structures internes et leurs encodages

Au niveau le plus haut, une base Redis est une table de hachage géante qui associe des clés à des objets. Chaque objet porte une indication de type, ce qui permet au serveur de refuser une opération inadaptée : demander un incrément sur une clé qui contient une liste renvoie une erreur.

Cette table de hachage principale doit grandir avec le nombre de clés, et son redimensionnement pose exactement le problème qu'on redoute sur un serveur mono-thread. Reconstruire d'un coup une table de plusieurs millions d'entrées bloquerait le serveur pendant un temps inacceptable. Redis procède donc par redimensionnement progressif. Il alloue une seconde table, puis déplace quelques entrées à chaque opération jusqu'à ce que l'ancienne soit vide. Pendant la transition, les lectures consultent les deux tables. Le coût est réparti au lieu d'être concentré, ce qui est une constante de la conception de Redis.

Un second niveau mérite attention, car il a des conséquences directes sur la mémoire consommée par notre application. Un même type visible par l'utilisateur peut correspondre à plusieurs représentations internes, choisies selon la taille de l'objet. Un Hash de quelques champs courts n'est pas stocké comme une table de hachage mais comme une liste compacte, appelée listpack, où les champs et les valeurs se suivent en mémoire contiguë. Un Sorted Set de peu d'éléments suit le même principe, alors qu'au delà d'un certain seuil il bascule vers une combinaison de table de hachage et de liste à niveaux. Un Set qui ne contient que des entiers est stocké comme un tableau trié d'entiers.

Le raisonnement est le suivant. Sur un petit objet, un parcours linéaire de quelques dizaines d'éléments est aussi rapide qu'un accès indexé, alors que la représentation compacte consomme plusieurs fois moins de mémoire. Les seuils de bascule sont configurables, et nous les avons laissés à leurs valeurs par défaut, cent vingt-huit éléments pour les Hash et les Sorted Set.

Nous avons vérifié ce comportement sur notre prototype. La commande OBJECT ENCODING appliquée à la clé de vélocité d'un abonné qui vient d'émettre trois transactions retourne listpack. Après une trentaine de transactions, la même commande retourne skiplist. Cette observation est reproduite en annexe C, et elle explique pourquoi les mesures de mémoire présentées en section 2.4 ne sont pas linéaires par rapport au nombre de transactions.

### 1.3 Deux mécanismes de persistance pour deux besoins

Redis conserve les données en mémoire, ce qui pose immédiatement la question de ce qui survit à un arrêt du serveur. Deux mécanismes coexistent, et ils ne répondent pas à la même préoccupation.

Le premier, appelé RDB, produit un instantané de la base à intervalles réguliers. Pour l'obtenir sans interrompre le service, Redis se duplique par un appel système fork, et c'est le processus fils qui écrit l'instantané pendant que le père continue de répondre. La mémoire n'est pas recopiée immédiatement : le système d'exploitation la partage entre les deux processus et n'en duplique une page que lorsque l'un des deux la modifie. Ce mécanisme, appelé copie sur écriture, rend l'opération peu coûteuse tant que le trafic d'écriture reste modéré. Sur une base très active, en revanche, la plupart des pages finissent par être dupliquées et la consommation mémoire peut approcher le double pendant la sauvegarde, ce qui est un point de dimensionnement à ne pas négliger.

L'instantané RDB est compact et se recharge très vite, ce qui en fait un bon outil de sauvegarde et de reprise. Il a un défaut évident : tout ce qui a été écrit depuis le dernier instantané est perdu.

Le second mécanisme, appelé AOF, répond à cette faiblesse. Plutôt qu'un état, il enregistre un journal de toutes les commandes d'écriture reçues. Rejouer ce journal reconstitue la base. La question devient alors celle de la fréquence à laquelle ce journal est réellement écrit sur le disque, et Redis propose trois politiques. Avec la politique always, chaque écriture est synchronisée sur le disque avant que le client reçoive sa réponse, ce qui offre une durabilité maximale mais fait chuter le débit à quelques milliers d'opérations par seconde au mieux. Avec la politique no, la synchronisation est laissée au système d'exploitation, qui peut attendre trente secondes. La politique intermédiaire, everysec, synchronise une fois par seconde.

Nous avons retenu everysec, et ce choix est un arbitrage que nous assumons. La politique always est incompatible avec notre budget de vingt millisecondes, puisque la synchronisation disque à elle seule le consommerait. La politique no exposerait à une perte de trente secondes de compteurs. Avec everysec, la perte maximale est d'une seconde d'écritures, ce qui représente au pire quelques milliers de transactions dans nos compteurs de comportement. Nous montrons en section 3.4 pourquoi cette perte est tolérable dans notre cas d'usage précis, alors qu'elle ne le serait pas pour un journal comptable.

Un dernier point sur l'AOF. Un journal qui ne fait que grandir devient ingérable, d'autant qu'il conserve des commandes devenues inutiles, par exemple mille incréments successifs sur un compteur qui ne vaut plus que sa valeur finale. Redis le réécrit donc périodiquement sous une forme minimale, dès qu'il a doublé de taille et qu'il dépasse un seuil plancher. Ces deux paramètres figurent dans notre configuration.

### 1.4 De la copie simple au partitionnement

Une instance unique reste un point de défaillance unique. Redis propose deux réponses complémentaires, qu'il faut bien distinguer car elles ne résolvent pas le même problème.

La réplication est le mécanisme de base. Une instance désignée comme réplica se connecte à une instance maîtresse et en reçoit une copie complète, puis le flux continu des écritures. Cette réplication est asynchrone, ce qui signifie que le maître répond au client sans attendre que les réplicas aient reçu quoi que ce soit. Nous verrons en Partie 3 que cette caractéristique a des conséquences importantes sur les garanties offertes. Depuis la version 4, une coupure réseau brève ne force plus une resynchronisation complète : le maître conserve un tampon des dernières écritures et peut n'envoyer que ce qui manque.

La réplication seule ne suffit pas, car si le maître tombe, personne ne promeut automatiquement un réplica. C'est le rôle de Sentinel, un processus de surveillance déployé en plusieurs exemplaires. Les Sentinels observent le maître, et lorsqu'un nombre suffisant d'entre eux le déclarent injoignable, ils élisent un réplica et le promeuvent, puis reconfigurent les autres réplicas et informent les clients. Le seuil requis, appelé quorum, doit être choisi de manière à éviter qu'une simple partition réseau ne provoque une bascule inutile.

Sentinel résout la disponibilité mais pas la capacité, puisque toutes les données restent sur une seule machine. Redis Cluster répond à ce second problème en partitionnant l'espace de clés. Chaque clé est associée à l'un de seize mille trois cent quatre-vingt-quatre emplacements, appelés slots, calculés par une fonction de hachage sur le nom de la clé. Les slots sont répartis entre les nœuds. Un client qui interroge le mauvais nœud reçoit une redirection, et pendant un déplacement de slot il peut recevoir une redirection temporaire différente.

Ce partitionnement introduit une contrainte qui a directement façonné notre modélisation. Une commande portant sur plusieurs clés n'est acceptée que si toutes ces clés résident dans le même slot. Or notre script de décision manipule sept clés du même abonné. Pour qu'elles soient garanties sur le même nœud, nous utilisons le mécanisme des hash tags : lorsqu'une portion du nom de clé est entourée d'accolades, seule cette portion est prise en compte par la fonction de hachage. Toutes nos clés d'abonné sont donc de la forme vel:snd:{22997000001}, où le numéro entre accolades force le regroupement. Ce détail, invisible dans le code applicatif, conditionne la capacité du système à passer à l'échelle.

Pour notre cas d'usage, nous retiendrions Sentinel en première étape et Cluster ensuite. Le raisonnement est le suivant. Notre empreinte mémoire, estimée en section 2.4, tient largement sur une seule machine pour plusieurs millions d'abonnés. Le besoin immédiat est donc la disponibilité, pas la capacité, et Sentinel y répond avec beaucoup moins de complexité opérationnelle. Le passage à Cluster deviendrait nécessaire si la volumétrie ou le débit dépassaient ce qu'une instance encaisse, et notre modélisation est déjà prête pour cette transition.

### 1.5 La mémoire comme ressource finie

Puisque tout tient en mémoire, la question de la saturation est centrale. Redis permet de fixer une limite, et de choisir ce qu'il fait lorsqu'elle est atteinte. Les politiques possibles vont du refus pur et simple des écritures à l'éviction des clés les moins récemment utilisées, en passant par plusieurs variantes qui limitent ou non l'éviction aux clés porteuses d'une expiration.

Nous avons retenu volatile-ttl, qui évince en priorité les clés dont l'expiration est la plus proche, et uniquement parmi celles qui en portent une. Ce choix découle directement de notre modélisation. Nos compteurs de comportement, fenêtres de vélocité, cardinalités de destinataires, décisions archivées, portent tous une expiration, parce qu'ils n'ont d'intérêt que sur un horizon de quelques jours. En revanche, le filtre de Bloom qui contient la liste noire et les hash qui portent le référentiel des règles n'ont aucune expiration, et leur éviction rendrait le moteur inopérant. La politique volatile-ttl garantit qu'ils ne seront jamais choisis.

Un mot sur la manière dont les expirations sont réellement appliquées, car elle explique un écart parfois observé entre le nombre de clés annoncé et le nombre de clés effectivement présentes. Redis combine deux approches. Une clé expirée est supprimée si un client tente d'y accéder, ce qui est le comportement paresseux. En parallèle, une tâche de fond échantillonne régulièrement des clés porteuses d'expiration et supprime celles qui sont périmées, en s'arrêtant lorsque la proportion de clés expirées dans l'échantillon devient faible. Ce mécanisme actif est probabiliste et non exhaustif, ce qui signifie qu'une clé expirée peut occuper de la mémoire quelques instants avant d'être réellement libérée.

### 1.6 Les modules

Deux des huit règles de notre moteur reposent sur des fonctionnalités qui ne font pas partie du cœur de Redis mais de modules officiels. RedisBloom fournit les filtres probabilistes, dont le filtre de Bloom que nous utilisons pour la liste noire. RedisTimeSeries fournit un type de série temporelle avec agrégation automatique, que nous utilisons pour l'historique des montants.

Cette distinction n'est pas académique. Elle conditionne le choix de l'image Docker, puisque l'image redis officielle ne contient pas ces modules et qu'il faut recourir à redis-stack-server. Elle conditionne aussi le choix d'un hébergement infogéré, tous les fournisseurs ne proposant pas ces modules. Notre prototype gère d'ailleurs leur absence : si RedisTimeSeries n'est pas chargé, l'écriture de la série échoue mais la décision reste rendue, comme le montre le traitement d'erreur du fichier engine.py.

Cette description de l'architecture interne nous donne tout ce dont nous avons besoin pour aborder la modélisation. Nous savons que les opérations sont sérialisées, que la mémoire est la ressource critique, que le partitionnement impose de regrouper les clés liées, et que la durabilité est un compromis réglable. La deuxième partie applique ces connaissances.

---

## Partie 2. Le modèle de données

Modéliser dans Redis est plus difficile que modéliser en relationnel, et pour une raison qui surprend souvent : rien ne guide le concepteur. Il n'y a pas de schéma à déclarer, pas de contrainte que le serveur vérifiera, pas de forme normale vers laquelle tendre. Le modèle est entièrement porté par des conventions que l'équipe se donne et qu'elle doit tenir seule. Cette partie présente d'abord les matériaux disponibles, puis les compare à ceux du relationnel, et enfin expose la construction retenue.

### 2.1 Les types natifs

Redis ne stocke pas des enregistrements mais des structures de données. C'est la différence essentielle avec les autres bases clé-valeur, et c'est ce qui rend possible notre cas d'usage.

Le type String contient une séquence d'octets, jusqu'à cinq cent douze mégaoctets. Il supporte des opérations atomiques d'incrément lorsqu'il contient un nombre, et il porte les opérations de bits que nous exploitons pour le profil horaire. Nous l'utilisons également pour les verrous et pour la garde d'idempotence, à travers la commande SET assortie de l'option NX qui n'écrit que si la clé n'existe pas.

Le type Hash associe des champs à des valeurs à l'intérieur d'une même clé. Il correspond assez naturellement à une ligne de table relationnelle, et nous l'utilisons pour le profil des abonnés, pour le référentiel des règles et pour la décision produite sur chaque transaction. L'accès à un champ est en temps constant.

Le type List est une liste doublement chaînée, avec insertion et suppression en temps constant aux deux extrémités, mais accès linéaire au milieu. Nous ne l'utilisons que pour conserver les mille dernières latences mesurées, où seul l'ajout en tête et la troncature nous intéressent.

Le type Set contient des membres uniques non ordonnés, avec test d'appartenance en temps constant et opérations ensemblistes d'union, d'intersection et de différence. Nous ne l'utilisons pas, et la section 2.3 explique pourquoi.

Le type Sorted Set associe à chaque membre un score numérique et maintient les membres triés par ce score. Il permet de compter les membres dans une plage de scores, d'en extraire la liste et de supprimer une plage entière, le tout en temps logarithmique. C'est la structure la plus utilisée de notre modèle.

Le type Stream est un journal ordonné et persistant d'entrées horodatées, avec un mécanisme de groupes de consommateurs. Nous l'utilisons pour transmettre les décisions au processus d'archivage.

Restent trois types qui ne sont pas des conteneurs classiques mais des structures probabilistes ou spécialisées. Le Bitmap n'est pas un type distinct mais une manière d'adresser les bits d'une String, et il permet de stocker un vecteur booléen très compact. Le HyperLogLog estime le nombre d'éléments distincts d'un ensemble en occupant une taille fixe de douze kilooctets, avec une erreur standard de zéro virgule huit pour cent. Le type Geospatial stocke des positions et permet des recherches par rayon, et il est en réalité implémenté comme un Sorted Set dont le score est une position encodée sur cinquante-deux bits, ce qui illustre bien l'économie de moyens de Redis.

### 2.2 Ce que l'on gagne et ce que l'on perd par rapport au relationnel

La comparaison mérite d'être faite honnêtement, colonne des pertes comprise, car un rapport qui n'énumère que les avantages de la technologie qu'il défend n'est pas crédible.

| Concept relationnel | Équivalent dans Redis | Nature du changement |
|---|---|---|
| Table | Espace de clés délimité par une convention de nommage | Perte du schéma imposé |
| Ligne | Hash | Gain de l'accès direct par clé |
| Clé primaire | Le nom de la clé lui même | Gain, aucun index à maintenir |
| Index secondaire | Sorted Set ou Set construit à la main | Perte, entièrement à la charge du développeur |
| Jointure | Aucune, dénormalisation ou allers-retours applicatifs | Perte |
| Agrégation sur fenêtre | Compteur entretenu à l'écriture | Gain majeur en latence, perte de souplesse |
| Suppression planifiée | Expiration native | Gain, aucune tâche de purge à écrire |
| Contrainte d'unicité | SET avec l'option NX | Équivalent |
| Intégrité référentielle | Aucune | Perte totale, à gérer dans l'applicatif |
| Type de données déclaré | Type porté par la valeur, non contraint | Perte |

Trois de ces lignes méritent un développement, car elles changent la manière de concevoir.

L'absence d'index secondaire est la plus déroutante. En relationnel, on stocke les données puis on ajoute un index quand une requête nouvelle apparaît. Dans Redis, tout accès qui ne se fait pas par le nom de la clé doit avoir été prévu et construit. Nos Sorted Set de vélocité sont exactement cela : un index sur le temps, que nous entretenons nous-mêmes à chaque écriture. La conséquence pratique est qu'une question à laquelle le modèle n'a pas été préparé est une question sans réponse, ou bien une question dont la réponse exige un parcours complet.

L'absence de jointure impose la dénormalisation, non comme une négligence tolérée mais comme le modèle attendu. Nous ne cherchons jamais à recomposer une information à partir de plusieurs clés au moment de la lecture, parce que cela coûterait des allers-retours réseau dans le chemin critique. Nous dupliquons plutôt l'information au moment de l'écriture. C'est un renversement complet du raisonnement relationnel, où l'on optimise la structure de stockage et où l'on accepte de payer à la lecture.

L'absence d'intégrité référentielle signifie enfin que rien n'empêche d'écrire une décision qui référence un abonné inexistant, ou de supprimer un profil dont les compteurs subsistent. Cette responsabilité remonte intégralement dans le code applicatif, et elle doit y être traitée explicitement.

### 2.3 Modélisation du cas d'usage

Notre modélisation repose sur une convention de nommage unique, appliquée sans exception : domaine, entité, identifiant, attribut, séparés par des deux-points. Ce séparateur est la convention établie dans l'écosystème Redis et il permet un filtrage par préfixe. Les numéros d'abonné sont normalisés au format international sans le signe plus, et systématiquement entourés d'accolades pour les raisons exposées en section 1.4.

L'ensemble des clés est regroupé dans le tableau suivant.

| Clé | Type | Contenu | Expiration |
|---|---|---|---|
| sub:{msisdn} | Hash | segment, niveau de connaissance client, plafond journalier, région d'origine | aucune |
| sub:{msisdn}:lastseen | String | horodatage de la dernière transaction | trente jours |
| vel:snd:{msisdn} | Sorted Set | transactions émises, score égal à l'horodatage | sept jours |
| vel:rcv:{msisdn} | Sorted Set | transactions reçues, score égal à l'horodatage | sept jours |
| ts:amt:{msisdn} | TimeSeries | montants émis, agrégés à l'heure | sept jours |
| hll:dst:{msisdn}:{jour} | HyperLogLog | destinataires distincts du jour | trente jours |
| bmp:hr:{msisdn} | Bitmap | cent soixante-huit bits, un par heure de la semaine | aucune |
| geo:last | Geospatial | dernière position connue de chaque abonné | aucune |
| bf:blacklist | Bloom Filter | numéros signalés | aucune |
| rule:{code} | Hash | libellé, poids, structure, activation | aucune |
| score:{tx_id} | Hash | décision produite et son détail | sept jours |
| idem:{tx_id} | String | garde contre les rejeux | vingt-quatre heures |
| stream:tx | Stream | flux des décisions | tronqué à cent mille |
| zset:cases | Sorted Set | file d'investigation, score égal au risque | aucune |

Le tableau ne dit pas pourquoi chaque structure a été retenue, et c'est pourtant le cœur de la modélisation. Nous justifions donc les quatre choix les moins évidents, en indiquant à chaque fois l'alternative écartée.

**Pourquoi un Sorted Set et non une List pour la fenêtre de vélocité.** Nous devons répondre à la question suivante : combien de transactions cet abonné a-t-il émises entre l'instant présent et cinq minutes plus tôt. Avec une List, il faudrait parcourir les éléments depuis la tête jusqu'à trouver le premier qui sorte de la fenêtre, soit un coût linéaire, et il n'existerait aucun moyen de purger les éléments trop anciens sans les parcourir également. Avec un Sorted Set dont le score porte l'horodatage, la commande ZCOUNT répond en temps logarithmique, et la commande ZREMRANGEBYSCORE purge une plage entière au même coût. Le choix n'est donc pas une préférence mais une nécessité.

Nous ajoutons que le membre du Sorted Set n'est pas seulement l'identifiant de la transaction mais la concaténation de cet identifiant et du montant, séparés par une barre verticale. Cette astuce évite un aller-retour supplémentaire lorsque le script doit sommer les montants de la fenêtre, puisque l'information voyage avec le membre.

**Pourquoi un HyperLogLog et non un Set pour les destinataires distincts.** La question est de connaître le nombre de destinataires différents auxquels un abonné a envoyé de l'argent aujourd'hui. Un Set y répondrait exactement, mais il conserverait chaque numéro, soit environ soixante octets par entrée en tenant compte de la structure. Sur une base de huit millions d'abonnés actifs, dont beaucoup de commerçants avec plusieurs centaines de contreparties quotidiennes, l'empreinte se compterait en dizaines de gigaoctets pour une seule journée. Le HyperLogLog occupe douze kilooctets par abonné et par jour quelle que soit la cardinalité, avec une erreur standard de zéro virgule huit pour cent.

Cette approximation est-elle acceptable pour fonder une décision de blocage ? Elle l'est, parce que la règle ne compare pas à une valeur précise mais détecte un changement de régime. Un compte qui passe de trois destinataires habituels à quatre cents est détecté indépendamment de savoir s'il y en a exactement trois cent quatre-vingt-seize ou quatre cent quatre. L'approximation ne serait inacceptable que si le seuil était fin, ce qui n'est pas le cas.

**Pourquoi un Bitmap et non un Hash pour le profil horaire.** Nous voulons savoir si un abonné a déjà opéré à cette heure-ci de cette journée de la semaine. L'information est booléenne et l'espace est fini : cent soixante-huit créneaux, sept jours multipliés par vingt-quatre heures. Un Bitmap de cent soixante-huit bits occupe vingt et un octets, contre plusieurs centaines pour un Hash équivalent. L'index du bit se calcule directement à partir de la date, ce qui rend l'accès immédiat.

**Pourquoi un filtre de Bloom et non un Set pour la liste noire.** La question est de savoir si un numéro figure parmi plusieurs millions de numéros signalés. Un Set donnerait une réponse exacte pour environ six cents mégaoctets sur dix millions d'entrées. Un filtre de Bloom paramétré à un taux d'erreur d'un pour mille occupe environ dix-sept mégaoctets.

L'arbitrage repose ici sur la nature de l'erreur. Un filtre de Bloom peut affirmer à tort qu'un élément est présent, mais il ne peut jamais affirmer à tort qu'il est absent. Un faux positif envoie donc une transaction légitime en revue humaine, ce qui coûte une vérification. Un faux négatif, qui laisserait passer un numéro réellement signalé, est impossible par construction. C'est exactement la propriété que l'on souhaite sur une liste noire, et c'est ce qui rend l'approximation non seulement acceptable mais préférable.

Il reste à mentionner un choix qui ne concerne pas une structure mais le flux. Nous transmettons les décisions au processus d'archivage par un Stream et non par le mécanisme de publication et abonnement. La raison est qu'un message publié pendant que le consommateur est arrêté est définitivement perdu, alors qu'une entrée de Stream reste dans la liste des messages en attente du groupe et sera reprise au redémarrage, ou réclamée par un autre consommateur. Nous démontrons ce comportement en soutenance.

### 2.4 Dimensionnement

Une modélisation qui ne s'accompagne d'aucun chiffre reste une intention. Nous avons donc mesuré l'empreinte réelle de nos structures sur le prototype, au moyen de la commande MEMORY USAGE, puis extrapolé.

| Structure | Mesure par abonné | Pour un million d'abonnés actifs |
|---|---|---|
| Profil sub | à compléter | |
| Fenêtre de vélocité, cent transactions | à compléter | |
| HyperLogLog quotidien | à compléter | |
| Bitmap horaire | à compléter | |
| Position géographique | à compléter | |
| Total par abonné | à compléter | |

Les valeurs sont à relever avec les commandes reproduites en annexe C, après avoir généré un trafic représentatif. Nous recommandons de mesurer sur un abonné ayant émis une centaine de transactions, ce qui correspond à un profil de commerçant actif, plutôt que sur un abonné neuf dont les structures seraient encore dans leur encodage compact et donneraient une estimation optimiste.

Nous avons maintenant un modèle. Reste à savoir quelles garanties Redis apporte lorsque plusieurs de ces structures doivent être lues et écrites ensemble, et c'est l'objet de la partie suivante.

---

## Partie 3. Le modèle de cohérence

Cette partie est celle qui décide de la validité de notre architecture. Choisir Redis parce qu'il est rapide serait insuffisant si les garanties qu'il offre ne couvraient pas les besoins de la décision anti-fraude. Nous examinons donc successivement ce qu'il garantit sur une instance unique, ce que ses mécanismes transactionnels apportent réellement, ce qu'il advient de la durabilité, et ce que le passage au partitionnement change. Nous terminons en traçant la frontière entre ce qui doit vivre dans Redis et ce qui doit rester en base relationnelle.

### 3.1 La cohérence sur une instance unique

Sur un serveur seul, la situation est remarquablement simple, et cette simplicité découle directement du mono-thread décrit en section 1.1. Puisque les commandes s'exécutent l'une après l'autre sans recouvrement possible, elles sont sérialisées de fait. Chaque commande observe l'état complet laissé par la précédente, et aucun client ne peut jamais lire un état intermédiaire.

Cela signifie qu'une opération individuelle, même complexe comme une insertion dans un Sorted Set ou un ajout dans un HyperLogLog, est atomique sans qu'il soit nécessaire de le demander. Il n'existe pas dans Redis d'équivalent aux niveaux d'isolation configurables des bases relationnelles, parce que la question ne se pose pas : le niveau est toujours le plus fort.

Cette garantie est réelle mais limitée à une commande. Dès que la logique métier exige d'en enchaîner plusieurs, elle ne suffit plus, et c'est là que les mécanismes suivants interviennent.

### 3.2 Ce que MULTI et EXEC garantissent réellement

Redis propose un mécanisme souvent présenté comme transactionnel. Le client ouvre un bloc avec MULTI, empile plusieurs commandes qui ne sont pas exécutées mais mises en file, puis déclenche l'ensemble avec EXEC. Le serveur exécute alors toutes les commandes du bloc sans qu'aucune commande extérieure ne puisse s'intercaler.

Cette formulation est exacte, mais elle ne dit pas ce que beaucoup de lecteurs y projettent. Un bloc MULTI et EXEC garantit l'isolation. Il ne garantit pas l'atomicité au sens où l'entend une base relationnelle. Si une commande du bloc échoue au moment de son exécution, par exemple parce qu'elle applique un incrément à une clé qui contient une liste, les commandes précédentes ne sont pas annulées et les suivantes s'exécutent normalement. Il n'existe aucun mécanisme de retour arrière dans Redis.

Ce n'est pas un oubli mais une position assumée des concepteurs, qui considèrent qu'une erreur de ce type est un défaut de programmation à corriger et non un cas d'exécution à gérer, et que le coût d'un mécanisme de retour arrière ne se justifierait pas.

Redis complète ce mécanisme par une commande WATCH, qui permet de surveiller une ou plusieurs clés avant d'ouvrir le bloc. Si l'une d'elles est modifiée par un autre client avant l'exécution, le bloc entier est abandonné et le client doit recommencer. Il s'agit d'un contrôle de concurrence optimiste, comparable à un verrou optimiste applicatif.

Nous n'avons retenu ni l'un ni l'autre pour notre décision, et la raison tient en une phrase : nos règles doivent décider en fonction de ce qu'elles lisent. Un bloc MULTI empile les commandes avant de connaître le moindre résultat, il ne peut donc pas contenir de condition. Notre logique, elle, doit lire le nombre de transactions récentes, puis décider si ce nombre déclenche une règle, puis lire le cumul des montants, et ainsi de suite jusqu'à une décision finale qui conditionne l'écriture. Aucune de ces étapes ne peut être empilée à l'aveugle.

### 3.3 Les scripts Lua

C'est cette limite qui nous conduit aux scripts. Redis embarque un interpréteur Lua et exécute un script soumis par le client comme s'il s'agissait d'une commande unique. Les garanties d'isolation sont donc les mêmes qu'avec un bloc MULTI, mais le script peut lire, tester, brancher et écrire en fonction de ce qu'il a observé.

Notre moteur exploite cette possibilité entièrement. Le fichier scoring.lua, reproduit en annexe A, lit les compteurs de l'abonné, évalue six règles, calcule un score, en déduit une décision, écrit cette décision et n'alimente les compteurs de comportement que si la transaction n'a pas été bloquée. L'ensemble constitue une seule opération du point de vue de Redis. Aucune transaction concurrente du même abonné ne peut lire un état intermédiaire ni corrompre le calcul.

Deux points d'ingénierie méritent d'être signalés. Le premier concerne la manière d'appeler le script. La commande EVAL transmet le corps du script à chaque appel, ce qui représenterait plusieurs kilooctets par transaction. La commande EVALSHA ne transmet que l'empreinte du script, soit quarante octets, le corps ayant été chargé une seule fois. Nous utilisons donc EVALSHA. Le revers est qu'après un redémarrage de Redis, le cache des scripts est vide et l'appel échoue avec une erreur NOSCRIPT. Notre moteur intercepte cette erreur, recharge le script et rejoue l'appel, ce qui rend le redémarrage transparent.

Le second point est la contrepartie du mono-thread, et il est important. Pendant qu'un script s'exécute, le serveur entier est bloqué. Un script mal écrit, ou qui parcourrait une structure très volumineuse, immobiliserait toutes les autres requêtes. Nous avons donc gardé le nôtre borné : il ne parcourt que les membres d'une fenêtre de cinq minutes, dont la taille est naturellement limitée par le comportement humain. Le journal des commandes lentes est réglé pour signaler tout dépassement de dix millisecondes, ce qui nous alerterait immédiatement si cette hypothèse était prise en défaut.

Nous signalons enfin que Redis 7 a introduit les Redis Functions, qui permettent d'enregistrer durablement des bibliothèques de fonctions plutôt que de charger des scripts anonymes. Ce mécanisme résout le problème du NOSCRIPT à la racine, et constituerait une évolution naturelle de notre prototype.

### 3.4 La durabilité et ses limites

Nous avons vu en section 1.3 que la persistance était réglable et que nous avions retenu une synchronisation à la seconde. Il faut maintenant en tirer les conséquences.

Lorsque le client reçoit sa réponse, l'écriture est en mémoire et dans le tampon du journal, mais elle n'est pas nécessairement sur le disque. En cas de coupure brutale de l'alimentation, jusqu'à une seconde d'écritures peut être perdue.

La situation est comparable du côté de la réplication. Celle-ci étant asynchrone, le maître répond au client sans attendre les réplicas. Si le maître disparaît immédiatement après avoir répondu, ses dernières écritures peuvent n'avoir atteint aucun réplica, et le réplica promu ne les connaîtra pas. Redis fournit une commande WAIT qui indique combien de réplicas ont accusé réception d'un point donné du flux, mais il faut être précis sur ce qu'elle fait. Elle ne bloque pas l'écriture, elle ne l'annule pas si le compte n'est pas atteint, et elle ne rend donc pas la réplication synchrone. Elle informe, et c'est à l'applicatif d'en tirer une conclusion.

Situé sur le triangle formé par la cohérence, la disponibilité et la tolérance au partitionnement, Redis privilégie clairement la disponibilité et la tolérance au partitionnement, et accepte de sacrifier la cohérence forte.

La question qui compte est donc de savoir si ce sacrifice est acceptable pour notre application, et notre réponse est qu'il l'est, à condition de bien voir ce que l'on perd. Ce qui vit dans Redis, ce sont des compteurs de comportement récent. Perdre une seconde de ces compteurs signifie qu'un abonné ayant émis trois virements dans les instants précédant la panne en apparaîtra momentanément avec deux. La détection est légèrement dégradée pendant quelques minutes, jusqu'à ce que les fenêtres glissantes se reconstituent naturellement. Aucune somme d'argent n'est perdue, aucune écriture comptable n'est manquante, aucune obligation réglementaire n'est violée.

Cette réponse ne vaudrait plus si nous confiions à Redis le journal des mouvements de comptes. Elle est ce qui justifie l'architecture à deux bases que nous détaillons en section 3.6.

### 3.5 La cohérence en mode Cluster

Le partitionnement introduit trois limites supplémentaires qu'il faut connaître avant de s'y engager.

La première est celle que nous avons déjà rencontrée : aucune commande, aucun bloc MULTI et aucun script ne peut porter sur des clés situées dans des slots différents. Notre modélisation y répond par les hash tags, mais cette réponse a un coût conceptuel. Elle signifie que toute opération croisant deux abonnés est impossible côté serveur et doit être décomposée côté client. C'est précisément pour cette raison que deux de nos règles, celle de la liste noire et celle du voyage impossible, sont évaluées hors du script : elles s'appuient sur des clés partagées par tous les abonnés, donc nécessairement situées ailleurs. Nous les regroupons dans un pipeline afin de n'en payer qu'un seul aller-retour réseau, et nous transmettons leur contribution au score en argument du script, de sorte que la décision reste calculée en un seul endroit.

La deuxième limite prolonge celle de la section précédente. Une bascule de maître en mode Cluster peut faire perdre des écritures déjà acquittées, pour la même raison d'asynchronisme. Le mode Cluster ne renforce donc pas la durabilité, il ajoute de la capacité.

La troisième concerne les périodes de rééquilibrage. Pendant qu'un slot est déplacé d'un nœud vers un autre, le nœud d'origine peut renvoyer une redirection temporaire pour les clés déjà migrées. Les bibliothèques clientes récentes gèrent cette redirection de façon transparente, mais un client rudimentaire ne le ferait pas, et le comportement doit être vérifié avant toute mise en production.

### 3.6 Où s'arrête Redis, où commence le relationnel

Tout ce qui précède conduit à une conclusion qui structure l'ensemble du projet : nous n'avons pas choisi Redis contre le relationnel, nous avons réparti les rôles entre les deux.

| Ce qui vit dans Redis | Ce qui vit dans PostgreSQL |
|---|---|
| Compteurs de comportement des sept derniers jours | Journal complet des décisions, conservé cinq ans |
| Décision d'une transaction, conservée une semaine | Dossiers de fraude instruits et clos |
| Référentiel des règles et de leurs poids | Historique des versions de règles |
| Liste noire opérationnelle | Liste noire de référence et sa traçabilité |
| File d'investigation en cours | Analyses statistiques, taux de faux positifs |

Le critère qui départage est double. Redis reçoit ce qui doit être lu en quelques millisecondes et qui perd sa valeur au bout de quelques jours. PostgreSQL reçoit ce qui doit survivre à toute panne et ce qui doit pouvoir être interrogé de manière imprévue.

Ce second point mérite d'être souligné, car il est souvent réduit à la seule question de la durabilité. Redis n'offre aucune capacité analytique. Il n'a pas d'équivalent d'un GROUP BY, pas d'agrégation à la lecture, pas d'index secondaire. Toute statistique doit avoir été précalculée à l'écriture, ce qui suppose de savoir à l'avance quelle question sera posée. Or un analyste de la fraude pose par définition des questions nouvelles. La requête qui compte les décisions par type et calcule la latence moyenne, que nous exécutons pendant la démonstration, est triviale en SQL et impossible dans Redis. Elle justifie à elle seule la présence de la seconde base.

Nous disposons désormais de tous les éléments pour présenter le cas d'usage lui-même.

---

## Partie 4. Le cas d'usage

### 4.1 Le problème métier

Une plateforme de mobile money met en relation des abonnés qui s'envoient de l'argent, des commerçants qui encaissent des paiements et des points de distribution qui assurent les dépôts et les retraits. Le service fonctionne sur des téléphones simples, par menu interactif, et la confirmation attendue par l'abonné arrive en quelques secondes.

Les schémas de fraude que nous cherchons à détecter sont au nombre de cinq principaux.

Le premier est la prise de contrôle d'un compte. Le fraudeur obtient le code secret par ingénierie sociale, puis vide le compte en quelques minutes vers des destinataires qu'il contrôle. La signature est une accélération brutale du rythme d'émission.

Le deuxième est la dispersion. Plutôt qu'un seul transfert repérable, le fraudeur éclate la somme vers un grand nombre de destinataires différents, souvent créés pour l'occasion. La signature est une explosion du nombre de contreparties distinctes.

Le troisième est la structuration. La réglementation impose une déclaration au delà d'un certain montant, fixé dans notre modèle à cinq cent mille francs. Le fraudeur découpe donc son opération en plusieurs virements légèrement inférieurs à ce seuil. Chacun pris isolément est parfaitement légal, et seule leur répétition dans une courte fenêtre révèle l'intention.

Le quatrième est le compte relais, appelé mule dans le métier. Un compte recruté reçoit de plusieurs sources et réémet presque tout en quelques minutes. Sa signature est un rapport entre sortant et entrant proche de un, sur une fenêtre très courte.

Le cinquième est l'incohérence géographique. Deux transactions du même abonné, séparées de quelques minutes, réalisées à des endroits qu'aucun déplacement réel ne relie. La signature est une vitesse implicite absurde.

À ces cinq schémas nous ajoutons deux signaux plus faibles, l'activité à une heure jamais observée pour ce compte et l'envoi vers un numéro déjà signalé, ainsi qu'un contrôle de cohérence par rapport au plafond de l'abonné. L'ensemble forme les huit règles du moteur.

| Code | Règle | Structure Redis mobilisée | Poids |
|---|---|---|---|
| R01 | Vélocité d'émission anormale | Sorted Set | 25 |
| R02 | Montant cumulé au dessus du profil | Sorted Set et TimeSeries | 20 |
| R03 | Explosion des destinataires distincts | HyperLogLog | 30 |
| R04 | Voyage géographiquement impossible | Geospatial | 35 |
| R05 | Créneau horaire jamais observé | Bitmap | 10 |
| R06 | Destinataire en liste noire | Bloom Filter | 50 |
| R07 | Structuration sous le seuil déclaratif | Sorted Set | 30 |
| R08 | Comportement de compte relais | Deux Sorted Set croisés | 40 |

Le score total détermine la décision. En dessous de quarante, la transaction est acceptée. Entre quarante et soixante-neuf, elle est mise en revue, ce qui se traduit concrètement par une authentification renforcée demandée à l'abonné. À partir de soixante-dix, elle est bloquée et un dossier est ouvert.

Nous soulignons que les poids ne figurent pas dans le code. Ils sont stockés dans les hash rule:{code} et relus périodiquement par le moteur, ce qui permet à un analyste de repondérer une règle sans redéploiement. Ce point paraît mineur mais il correspond à la réalité du métier, où les seuils s'ajustent en fonction des campagnes de fraude observées.

### 4.2 Justification du choix technologique

La justification tient dans le tableau suivant, où chaque contrainte est mise en face de ce qu'elle interdit.

| Contrainte | Valeur retenue | Ce qu'elle rend impossible |
|---|---|---|
| Latence de décision | vingt millisecondes au 99e centile | toute agrégation calculée à la lecture |
| Débit en pointe | trois à cinq mille transactions par seconde | un accès disque par transaction |
| Fenêtres d'analyse | cinq minutes, une heure, vingt-quatre heures, sept jours | un recalcul complet à chaque requête |
| Rétention des compteurs | sept jours | une tâche de purge planifiée |
| Atomicité de la décision | totale | une lecture suivie d'une écriture séparée |

Aucune de ces cinq lignes n'est satisfaite par une base relationnelle seule. Il reste à expliquer pourquoi les autres bases proposées ne conviennent pas davantage.

**Pourquoi pas PostgreSQL seul.** Les quatre agrégations nécessaires à la décision portent sur une table de plusieurs milliards de lignes. Même avec un partitionnement par date et des index adaptés, le coût se compte en centaines de millisecondes, et il croît avec l'historique. Une vue matérialisée rafraîchie périodiquement ne résout pas le problème, puisque la détection porte précisément sur les dernières minutes.

**Pourquoi pas Apache Cassandra.** Cassandra est excellente en écriture et se distribue remarquablement, ce qui en ferait un bon choix pour le journal des transactions. Mais notre besoin est un besoin de lecture avec agrégation sur fenêtre glissante, et c'est précisément ce que son modèle décourage. Une requête doit correspondre à la clé de partition prévue, les agrégations sont limitées, et la latence de lecture typique se situe un ordre de grandeur au dessus de notre budget.

**Pourquoi pas Neo4j.** Le graphe est une excellente représentation d'un réseau de mules, et nous considérons qu'il aurait toute sa place dans un second temps, pour l'investigation après coup. Il ne convient pas au chemin critique : parcourir un graphe pour décider en vingt millisecondes, sous plusieurs milliers de requêtes par seconde, n'est pas ce pour quoi il est conçu.

**Pourquoi pas Apache HBase.** HBase s'adresse au stockage massif sur système de fichiers distribué, avec des latences de lecture qui se comptent en dizaines de millisecondes dans le meilleur des cas. Le profil est celui d'un entrepôt, pas d'un moteur de décision synchrone.

**Pourquoi pas Memcached.** La comparaison mérite d'être faite car Memcached est le concurrent naturel de Redis sur le terrain du cache. Il ne convient pas ici parce qu'il ne propose que des chaînes opaques, sans Sorted Set, sans HyperLogLog, sans opérations sur les bits et sans exécution atomique de logique conditionnelle. Nous devrions alors lire, calculer côté applicatif et réécrire, ce qui ouvrirait une fenêtre de concurrence et multiplierait les allers-retours réseau.

Redis est donc retenu non parce qu'il serait supérieur en général, mais parce qu'il est le seul des candidats dont le modèle de données porte nativement les structures dont nos huit règles ont besoin, avec les garanties d'atomicité requises et dans le budget de latence imposé.

### 4.3 L'architecture déployée

Le prototype se compose de sept conteneurs.

Un serveur Apache reçoit le trafic et le répartit entre trois instances applicatives selon un tour de rôle strict. Chaque instance exécute une application Django servie par Gunicorn, et renvoie dans un en-tête le nom de l'instance qui a traité la requête, ce qui rend la répartition observable.

Redis Stack porte l'ensemble des structures décrites en Partie 2. L'image retenue embarque les modules RedisBloom et RedisTimeSeries dont deux de nos règles dépendent.

PostgreSQL reçoit l'archivage des décisions, conformément à la répartition établie en section 3.6.

Un processus séparé consomme le flux des décisions et alimente PostgreSQL. Il travaille hors du chemin critique, ce qui signifie que son arrêt n'affecte pas la décision rendue aux abonnés.

Cette séparation entre le chemin critique et le traitement différé est le principe organisateur de l'architecture. Tout ce qui conditionne la réponse à l'abonné est fait de manière synchrone dans Redis. Tout ce qui peut attendre, archivage, enrichissement, statistiques, passe par le flux.

### 4.4 Le parcours d'une transaction

Il est utile de suivre une transaction de bout en bout.

Elle arrive sur l'une des trois instances Django, sous forme d'une requête contenant l'émetteur, le destinataire, le montant et éventuellement la position. Le moteur ouvre d'abord un pipeline vers Redis pour évaluer les deux règles qui ne peuvent pas entrer dans le script : l'appartenance du destinataire à la liste noire, et la dernière position connue de l'émetteur assortie de son horodatage. Ces trois lectures voyagent en un seul aller-retour réseau.

À partir de la position précédente et de la position courante, le moteur calcule la distance et la compare au temps écoulé. Au delà de neuf cents kilomètres par heure, la règle du voyage impossible se déclenche. Ce calcul est fait dans l'application plutôt que par la commande GEODIST, pour une raison précise : cette commande exige que les deux points soient déjà enregistrés, or la position courante ne l'est pas encore, et l'enregistrer avant la décision fausserait la règle en plus de coûter un aller-retour supplémentaire.

Le moteur appelle ensuite le script de scoring, en lui transmettant les sept clés de l'abonné, les informations de la transaction, le score déjà accumulé par les deux règles précédentes et la table des poids courants. Le script s'exécute atomiquement, évalue les six règles restantes, calcule le score total, en déduit la décision, l'enregistre et alimente les compteurs si la transaction n'est pas bloquée.

La réponse est renvoyée à l'appelant. Tout ce qui suit est différé : enregistrement de la nouvelle position, alimentation de la série temporelle des montants, publication de l'entrée dans le flux, incrément des compteurs de supervision. Ces opérations sont regroupées dans un second pipeline, elles aussi en un seul aller-retour.

Le prototype comporte enfin une garde d'idempotence que nous jugeons indispensable. En mobile money, les passerelles de paiement rejouent une transaction dès qu'un accusé de réception tarde, et une transaction rejouée qui alimenterait deux fois les compteurs fausserait la détection. Le script pose donc une clé avec l'option NX au tout début, et si cette clé existe déjà il retourne la décision précédente sans rien modifier.

### 4.5 Les requêtes critiques en langage natif

Nous présentons ici les commandes qui portent chaque règle. Le script complet figure en annexe A.

La fenêtre de vélocité repose sur trois commandes. L'ajout enregistre la transaction avec l'horodatage en score. Le comptage interroge une plage de scores. La purge supprime une plage entière.

```
ZADD vel:snd:{22997000001} 1754582400000 "T88213|250000"
ZCOUNT vel:snd:{22997000001} 1754582100000 1754582400000
ZRANGEBYSCORE vel:snd:{22997000001} 1754582100000 1754582400000
ZREMRANGEBYSCORE vel:snd:{22997000001} -inf 1753977600000
```

Le comptage des destinataires distincts repose sur deux commandes, l'ajout et l'estimation, auxquelles s'ajoute la fusion lorsqu'on veut agréger plusieurs journées.

```
PFADD hll:dst:{22997000001}:20260807 22997000455
PFCOUNT hll:dst:{22997000001}:20260807
PFMERGE hll:dst:{22997000001}:7j hll:dst:{22997000001}:20260801 hll:dst:{22997000001}:20260802
```

Le profil horaire repose sur les opérations de bits.

```
SETBIT bmp:hr:{22997000001} 51 1
GETBIT bmp:hr:{22997000001} 51
BITCOUNT bmp:hr:{22997000001}
```

Le suivi géographique repose sur l'enregistrement de position et la recherche par rayon.

```
GEOADD geo:last 2.4183 6.3703 22997000001
GEOPOS geo:last 22997000001
GEOSEARCH geo:last FROMLONLAT 2.4183 6.3703 BYRADIUS 5 km ASC COUNT 20
```

La liste noire repose sur les commandes du module RedisBloom.

```
BF.RESERVE bf:blacklist 0.001 10000000
BF.ADD bf:blacklist 22997000455
BF.EXISTS bf:blacklist 22997000455
```

La série des montants repose sur RedisTimeSeries, avec une règle d'agrégation calculée à l'écriture plutôt qu'à la lecture.

```
TS.CREATE ts:amt:{22997000001} RETENTION 604800000 DUPLICATE_POLICY LAST
TS.CREATERULE ts:amt:{22997000001} ts:amt:1h:{22997000001} AGGREGATION sum 3600000
TS.ADD ts:amt:{22997000001} 1754582400000 250000
TS.RANGE ts:amt:{22997000001} 1754496000000 1754582400000 AGGREGATION sum 3600000
```

Le flux et son groupe de consommateurs reposent sur les commandes suivantes. Les deux dernières sont celles qui distinguent réellement un Stream d'un mécanisme de publication et abonnement, puisqu'elles permettent de constater puis de reprendre les messages restés sans acquittement.

```
XADD stream:tx * tx_id T88213 msisdn 22997000001 montant 250000 decision BLOCK score 84
XGROUP CREATE stream:tx enrichissement 0 MKSTREAM
XREADGROUP GROUP enrichissement worker1 COUNT 100 BLOCK 2000 STREAMS stream:tx >
XACK stream:tx enrichissement 1754582400000-0
XPENDING stream:tx enrichissement
XAUTOCLAIM stream:tx enrichissement worker2 60000 0 COUNT 50
```

Enfin, les commandes d'inspection que nous utilisons pour étayer les affirmations de ce rapport.

```
OBJECT ENCODING vel:snd:{22997000001}
MEMORY USAGE hll:dst:{22997000001}:20260807
SLOWLOG GET 10
INFO memory
INFO replication
```

### 4.6 Mesures

Les mesures suivantes ont été relevées sur le prototype. La configuration matérielle utilisée est à préciser, ainsi que la version exacte de l'image Redis.

| Indicateur | Valeur mesurée | Méthode |
|---|---|---|
| Latence médiane de décision | à compléter | commande simulate en charge |
| Latence au 99e centile | à compléter | idem |
| Débit soutenu depuis une instance | à compléter | idem |
| Empreinte mémoire, mille abonnés actifs | à compléter | INFO memory |
| Commandes dépassant dix millisecondes | à compléter | SLOWLOG GET |

Nous insistons sur un point de méthode. Ces mesures sont celles d'un prototype exécuté sur une machine de développement, dans des conteneurs partageant les mêmes ressources. Elles établissent un ordre de grandeur et vérifient que le budget de latence est tenable, elles ne constituent pas un dimensionnement de production.

### 4.7 Ce que le projet ne traite pas

Il nous paraît plus utile d'énumérer nous-mêmes les limites de ce travail que de les laisser découvrir.

Le prototype tourne sur une instance Redis unique. La modélisation prévoit le passage en mode Cluster, à travers les hash tags, mais ce passage n'a pas été éprouvé et il révélerait probablement des ajustements.

Aucune authentification ne protège l'interface de programmation. En production, elle serait accessible uniquement depuis le réseau interne et protégée par une authentification mutuelle ou par une signature de requête.

Les poids des règles sont fixés à la main. Un système réel les apprendrait à partir de cas étiquetés, et les réviserait régulièrement.

Surtout, nous n'avons pas évalué la qualité de détection. Nous savons que nos règles se déclenchent sur les schémas que nous avons construits, ce qui est très différent de savoir combien de fraudes réelles elles détecteraient et combien de transactions légitimes elles bloqueraient à tort. La méthode d'évaluation serait de rejouer un historique de transactions étiquetées, de mesurer la précision et le rappel de chaque règle prise séparément puis de l'ensemble, et d'ajuster les poids en conséquence. Nous n'avons pas eu accès à un tel jeu de données.

---

## Conclusion

Nous sommes partis d'une contrainte simple à énoncer : décider en vingt millisecondes si une transaction de mobile money doit être acceptée, en s'appuyant sur le comportement récent du compte. Cette contrainte, une fois traduite en agrégations sur fenêtres glissantes, exclut le modèle relationnel du chemin critique et exclut également les trois autres bases proposées dans ce sujet, pour des raisons que la Partie 4 détaille.

Redis convient parce que son modèle de données ne se limite pas à des paires clé-valeur mais offre des structures spécialisées dont chacune répond exactement à l'une de nos questions. Le Sorted Set porte la fenêtre glissante, le HyperLogLog porte le comptage approché des contreparties, le Bitmap porte le profil horaire, le filtre de Bloom porte la liste noire, le type Geospatial porte la cohérence des déplacements. Le script Lua permet enfin de composer ces lectures en une décision unique et atomique, ce qu'aucun mécanisme transactionnel classique de Redis ne permettrait.

Cette réussite tient à un ensemble de choix cohérents faits par les concepteurs de Redis, et le travail de modélisation a consisté autant à exploiter ces choix qu'à en accepter les conséquences. L'exécution sur un seul fil nous donne la sérialisation gratuitement et nous interdit toute opération longue. La conservation en mémoire nous donne la latence et nous impose une politique d'éviction pensée. La réplication asynchrone nous donne la disponibilité et nous prive de la durabilité forte. Le partitionnement nous donne la capacité et nous impose de regrouper les clés liées.

C'est pourquoi la conclusion de ce rapport n'est pas que Redis remplace le relationnel, mais qu'il en occupe le complément. Nous avons réparti les responsabilités : Redis porte le comportement récent, où la latence prime et où une seconde de perte est tolérable ; PostgreSQL porte l'audit réglementaire et l'analyse, où la durabilité et l'expressivité priment et où la latence importe peu. Les deux bases ne sont pas en concurrence, elles répondent à deux questions différentes.

S'il fallait retenir un enseignement de ce travail, ce serait celui-ci : choisir une base NoSQL n'est pas choisir une technologie plus moderne, c'est accepter de déplacer une complexité. Ce que le serveur relationnel faisait pour nous, index secondaires, jointures, intégrité référentielle, agrégations imprévues, il faut désormais le construire nous-mêmes ou décider de s'en passer. Ce déplacement se justifie lorsqu'une contrainte le rend nécessaire, et il se paie lorsqu'il est fait par imitation.

---

## Annexes

**Annexe A.** Script de scoring en langage Lua, fichier app/scoring/lua/scoring.lua, intégralement commenté.

**Annexe B.** Configuration Redis, fichier redis/redis-stack.conf, avec la justification de chaque directive.

**Annexe C.** Relevés d'exécution : sorties de OBJECT ENCODING, MEMORY USAGE, SLOWLOG GET et XPENDING, avec les copies d'écran des sessions.

**Annexe D.** Composition de la pile Docker, fichier docker-compose.yml, et configuration du répartiteur Apache, fichier apache/httpd.conf.

**Annexe E.** Copies d'écran de la console de supervision pendant l'exécution des scénarios de fraude.

**Annexe F.** Dépôt du code source. Lien à insérer.
