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
