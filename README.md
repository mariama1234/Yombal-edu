# YombalEdu — Projet full-stack (Django + Angular)

Ce dossier contient le backend et le frontend générés à partir du schéma
MySQL validé et du design Figma/HTML validé avec le client.

```
yombaledu_fullstack/
├── backend_django/     API REST Django + DRF (32 modèles, JWT, multi-écoles)
└── frontend_angular/   Application Angular 18 (standalone components)
```

## Ce qui est réellement testé, pas juste généré

- Les modèles Django ont été validés avec `manage.py check` et
  `manage.py makemigrations` / `migrate` (SQLite en local, MySQL en prod).
- L'API a été testée en conditions réelles : obtention d'un token JWT,
  création d'un élève via l'endpoint composite `/api/eleves/inscrire/`,
  vérification de l'isolation multi-écoles.
- Le frontend Angular a été **compilé avec succès** (`ng build`, 0 erreur)
  après plusieurs allers-retours de correction (voir détail plus bas).
- Le build a été servi et chargé pour vérifier l'absence d'erreur runtime.

Ce que je n'ai **pas** pu faire dans cet environnement : un test de
clic-par-clic dans un vrai navigateur (Playwright), l'installateur de
navigateur étant bloqué par les restrictions réseau du bac à sable. Fais
un premier tour toi-même une fois lancé en local — voir plus bas.

## Portée du travail — sois au clair là-dessus avant de présenter ça

**Modules avec formulaire réel, câblé de bout en bout au backend :**
Tableau de bord, Élèves (liste + inscription 3 sections), Classes,
Parents & tuteurs, Matières, Présences, Notes, Enseignants & personnel,
Emploi du temps, Périodes scolaires, Coefficients, Affectations,
Comptes & rôles, Finances (impayés → encaissement → reçu automatique →
journal de caisse), Communications, Documents, Paramètres.

**Écrans en consultation seule :** Bulletins, Reçus, Journal d'activité —
leur contenu est produit par l'application, pas saisi à la main.

**Les 38 tables ont toutes un endpoint REST.** Voir « Les tables ajoutées »
plus bas pour celles absentes du dump initial.

**Ce qui reste à faire :** l'expédition réelle des SMS et e-mails
(les destinataires sont calculés, il manque le prestataire), la génération
des fichiers de rapport, et le calcul automatique des bulletins à partir
des notes et des coefficients.

## Démarrage rapide

### 1. Backend Django

Prérequis : **MariaDB 10.11+ ou MySQL 8.0.11+**. Django 6.1 refuse de
démarrer sur une version antérieure — la MariaDB 10.4 livrée avec XAMPP ne
convient pas.

Crée d'abord la base :

```sql
CREATE DATABASE gestion_scolaire
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```bash
cd backend_django
python3 -m venv venv && source venv/bin/activate
pip install django djangorestframework djangorestframework-simplejwt django-cors-headers django-filter mysqlclient "drf-spectacular[sidecar]"
python3 manage.py migrate
python3 manage.py createsuperuser   # ou utilise le script de seed ci-dessous
python3 manage.py runserver
```

### Commandes de mise en route

```bash
python3 manage.py init_config   # paramètres et catégories de dépense d'une école
python3 manage.py seed_demo     # école, comptes et élèves de démonstration
python3 manage.py test gestion_scolaire   # 89 tests, environ 18 min
```

Le run est long parce que la création de la base de test MySQL et les
migrations le sont ; c'est normal, pas un blocage.

`init_config` n'invente aucune donnée : elle installe les 19 paramètres par
défaut (devise, barème sur 20, moyenne de passage, préfixe des matricules et
des reçus, délai de relance...) et 10 catégories de dépense. Elle est
idempotente et peut donc tourner sur une école réelle. `seed_demo`, à
l'inverse, crée des données fictives destinées aux essais uniquement.

Pour créer manuellement une école + un admin de test :

```bash
python3 manage.py shell
```
```python
from gestion_scolaire.models import Ecole
from django.contrib.auth import get_user_model
U = get_user_model()
ecole = Ecole.objects.create(nom="École MIC CITE", plan_abonnement="standard")
admin = U.objects.create_user(email="admin@micite.sn", id_ecole=ecole, nom="Diallo", prenom="Ndalé", role="admin", password="motdepasse123")
admin.is_staff = True; admin.save()
```

### 2. Frontend Angular

```bash
cd frontend_angular
npm install
npm start   # équivalent à ng serve, sur http://localhost:4200
```

L'URL de l'API est configurée dans `src/environments/environment.ts`
(`http://localhost:8000/api` par défaut).

### 3. Se connecter

Va sur `http://localhost:4200/connexion` avec les identifiants créés
à l'étape 1.

## Configuration de la base

`backend_django/config/settings.py` pointe sur MySQL/MariaDB. Les valeurs
par défaut correspondent à une installation locale (`root`, sans mot de
passe, `127.0.0.1:3306`) ; en production, surcharge par variables
d'environnement — aucun secret n'est à écrire dans le fichier :

```bash
export DB_NAME=gestion_scolaire
export DB_USER=ton_user
export DB_PASSWORD=ton_mot_de_passe
export DB_HOST=localhost
export DB_PORT=3306
```

### Deux serveurs, deux bases

Django 6.1 exige MariaDB 10.11+ ou MySQL 8.0.11+, ce que la MariaDB 10.4 de
XAMPP ne satisfait pas. En local, deux serveurs cohabitent donc :

| | Port 3306 — XAMPP | Port 3307 — instance applicative |
|---|---|---|
| Version | MariaDB 10.4.32 | MariaDB 10.11.16 |
| Emplacement | `C:\xampp\mysql` | `C:\mariadb-10.11.16` |
| Base | `gestion_scolaire_ref` — les 32 tables du dump validé avec le client, en lecture | `gestion_scolaire` — les 38 tables de l'application |
| Gérée par | Personne, c'est une référence consultable dans phpMyAdmin | Django, via `manage.py migrate` |

Démarrer l'instance applicative :

```powershell
C:\mariadb-10.11.16\bin\mysqld.exe --defaults-file=C:\mariadb-10.11.16\my.ini
```

Les deux schémas ne peuvent pas cohabiter dans la même base : les noms de
tables sont identiques mais les colonnes diffèrent (voir les écarts
ci-dessous). N'importe **jamais** `Yombal Edu.sql` dans `gestion_scolaire`,
sinon les migrations échouent en `#1050 table already exists`.

> Le fichier `Yombal Edu.sql` d'origine récupéré depuis ClickUp était
> inutilisable : son indentation contenait 414 espaces insécables (U+00A0)
> issus d'une copie depuis une page web, que MySQL lit comme des
> identifiants (`#1064`). Si tu réexportes depuis ClickUp, télécharge la
> pièce jointe au lieu de copier le contenu affiché.

### Écarts connus avec `Yombal Edu.sql`

Le schéma produit par Django **n'est pas identique** au dump SQL validé.
À savoir avant de brancher l'API sur une base créée à partir du dump :

- **Clés primaires** : le dump nomme ses PK (`id_eleve`, `id_ecole`...),
  Django génère partout une colonne `id`. Les tables ne sont pas
  interchangeables — laisse Django créer le schéma.
- **`utilisateur`** : le dump stocke `mot_de_passe_hash` et autorise le même
  e-mail dans deux écoles différentes ; Django utilise `password`, impose
  l'e-mail unique sur toute l'instance, et ajoute `last_login`,
  `is_superuser` et les tables de permissions `utilisateur_groups` /
  `utilisateur_user_permissions`.
- **`eleve_parent`** : PK composite dans le dump, colonne `id` + contrainte
  d'unicité côté Django.
- **Dates** : `date_paiement`, `date_saisie`, `date_envoi` et
  `date_generation` sont en `auto_now_add` — elles prennent l'heure du
  serveur et ne peuvent pas être antidatées, alors que le dump le permet.

## Les tables ajoutées

Six tables absentes du dump initial, dans la migration `0002` :

| Table | Rôle |
|---|---|
| `communication` | Annonce ou message : type, canal, cible, statut d'envoi |
| `communication_destinataire` | Un destinataire et son état d'envoi/lecture |
| `document` | Pièce déposée, avec catégorie et visibilité par rôle |
| `rapport` | Historique des exports demandés (PDF/Excel/CSV) |
| `parametre_ecole` | Réglages par école, en clé/valeur |
| `journal_activite` | Audit : qui a fait quoi, quand, depuis quelle IP |

**Communications.** Une communication se crée en brouillon, puis s'envoie
via `POST /api/communications/{id}/envoyer/`, qui traduit la cible en
destinataires concrets. Le canal décide de leur nature : `interne` vise les
comptes de l'application, `sms` et `email` visent les fiches
`parent_tuteur` — la plupart des familles n'ont pas de compte mais ont un
téléphone. Le champ `contact` fige le numéro utilisé au moment de l'envoi.
**L'expédition réelle n'est pas branchée** : les destinataires sont
calculés et enregistrés en `en_attente`, il reste à connecter un
prestataire SMS.

**Rapports.** Même logique : la demande est enregistrée en `en_attente`,
la génération effective du fichier reste à implémenter.

**Journal d'activité.** Alimenté automatiquement par `JournalisationMixin`
(`gestion_scolaire/audit.py`), appliqué à tous les ViewSets en écriture :
chaque création, modification et suppression y est tracée, ainsi que les
connexions et les échecs de connexion. Consultable en lecture seule par un
admin sur `/api/journal-activite/`. Un échec d'écriture dans le journal
n'interrompt jamais l'opération métier qu'il accompagne.

## Explorer et tester l'API

Une fois `python3 manage.py runserver` lancé :

| Adresse | À quoi ça sert |
|---|---|
| `http://localhost:8000/api/docs/` | **Swagger UI** — les 38 endpoints, avec un bouton *Try it out* pour les appeler depuis le navigateur |
| `http://localhost:8000/api/redoc/` | ReDoc — même contenu, plus agréable à parcourir |
| `http://localhost:8000/api/schema/` | Le fichier OpenAPI 3 brut, importable dans Postman ou Insomnia |
| `http://localhost:8000/api/` | L'API navigable de DRF, pratique pour cliquer de lien en lien |

**Pour appeler un endpoint protégé depuis Swagger :**

1. Déplier `POST /api/auth/token/`, *Try it out*, puis envoyer
   `{"email": "admin@miccite.sn", "password": "motdepasse123"}`
   (le compte créé par `seed_demo`).
2. Copier la valeur de `access` dans la réponse.
3. Cliquer sur **Authorize** en haut à droite et saisir `Bearer <le jeton>`.
4. Tous les endpoints deviennent appelables. Le jeton reste en place d'une
   page à l'autre, et vaut 8 heures.

Les fichiers de Swagger UI sont servis depuis le projet, pas depuis un CDN :
la documentation reste consultable sans connexion internet.

En ligne de commande, le même parcours :

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@miccite.sn","password":"motdepasse123"}' | jq -r .access)

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/eleves/
```

## Isolation multi-écoles — la règle à ne pas casser

C'est l'invariant central du produit, et il se joue dans **les deux sens**.
Filtrer la lecture ne suffit pas : tant que `id_ecole` restait modifiable par
le client, un administrateur pouvait créer des enregistrements dans l'école
d'un autre établissement — invisibles chez lui, bien réels chez la victime.

Deux mixins de `gestion_scolaire/views.py` s'en chargent, et il faut les deux :

- **`EcoleFiltreeMixin`** filtre le queryset en lecture et impose `id_ecole`
  en écriture, pour les tables qui portent cette colonne. Les serializers
  concernés déclarent `id_ecole` dans `read_only_fields` — ce qui a un second
  effet utile : le champ n'est plus exigé du client, donc les formulaires
  génériques n'ont pas à le transmettre.
- **`CoherenceEcoleMixin`** refuse toute écriture dont une clé étrangère
  pointe vers une autre école. C'est indispensable pour les tables **sans**
  `id_ecole` — note, absence, évaluation, paiement, bulletin... — qui
  acceptaient sinon un `id_eleve` ou un `id_classe` appartenant à autrui.
  Il s'appuie sur `ecole_de()`, qui remonte la chaîne de clés étrangères
  jusqu'à l'école.

Quand un modèle n'a pas de FK directe vers `Ecole`, le viewset redéfinit en
plus `get_queryset()` pour filtrer via la jointure (`id_eleve__id_ecole_id`,
`id_classe__id_ecole_id`...). `TestSurfaceAPI` vérifie que chaque endpoint
enregistré exige une authentification et répond sans erreur serveur.

**À retenir pour la suite :** un nouveau viewset en écriture doit porter
`CoherenceEcoleMixin`, et `EcoleFiltreeMixin` si son modèle a `id_ecole`.
Ne jamais prendre le filtrage de liste pour une preuve d'isolation : tester
aussi l'accès par identifiant et l'écriture.

`/api/ecoles/` est restreint à sa propre école, sans création ni suppression :
ouvrir ou fermer un établissement est une opération de la plateforme.
`plan_abonnement` et `statut` y sont en lecture seule, pour qu'une école ne
puisse pas changer seule d'abonnement ni lever sa suspension.

## Le système CRUD générique — comment ajouter un module

Plutôt que d'écrire 8 formulaires quasi-identiques à la main, les modules
secondaires (Parents, Matières, Présences, Notes, Personnel, Emploi du temps,
Classes) sont pilotés par une configuration unique :
`frontend_angular/src/app/shared/crud/entity-configs.ts`.

Pour ajouter un nouveau module, pas besoin de créer de composant — ajoute
une entrée dans ce fichier (colonnes de la liste, champs du formulaire),
puis deux lignes dans `app.routes.ts` :

```typescript
{ path: 'ton-module', component: GenericListComponent, data: { entityKey: 'ton-module' } },
{ path: 'ton-module/:id', component: GenericFormComponent, data: { entityKey: 'ton-module' } },
```
