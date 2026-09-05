# YombalEdu — Projet full-stack (Django + Angular)

Ce dossier contient le backend et le frontend générés à partir du schéma
MySQL validé et du design Figma/HTML validé avec le client.

```
yombaledu_fullstack/
├── backend_django/     API REST Django + DRF (32 modèles, JWT, multi-écoles)
└── frontend_angular/   Application Angular 18 (standalone components)
```

## Ce qui est réellement testé, pas juste généré

- **234 tests automatisés** passent en une dizaine de secondes :
  `DB_ENGINE=sqlite python manage.py test gestion_scolaire`. Ils couvrent
  l'isolation multi-écoles, l'audit, le parcours élève (mouvements,
  réinscription, dossier), la pédagogie (pondérations, rang, bulletins,
  palmarès), l'import de listes et tout le module financier (facturation en
  masse, encaissement, relances, journal, bilan, exports Excel et PDF).
  Les tests utilisent un hachage de mot de passe rapide (`settings.py`,
  actif uniquement sous `manage.py test`) : avec PBKDF2 la suite prenait
  une demi-heure, au point qu'on renonçait à la lancer.
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
Tableau de bord, Élèves (liste, inscription 3 sections, import, dossier
numérique, mouvements, réinscription), Classes, Parents & tuteurs, Matières,
Présences, Évaluations, Saisie des notes en grille, Bulletins, Palmarès &
statistiques, Enseignants & personnel, Emploi du temps, Périodes scolaires,
Coefficients, Affectations, Comptes & rôles, Gestion financière (voir
ci-dessous), Communications, Documents, Paramètres.

**Écrans en consultation seule :** Reçus, Journal d'activité — leur contenu
est produit par l'application, pas saisi à la main.

**Les 38 tables ont toutes un endpoint REST.** Voir « Les tables ajoutées »
plus bas pour celles absentes du dump initial.

**Ce qui reste à faire :** l'expédition réelle des SMS et e-mails
(les destinataires et les relances sont calculés, il manque le
prestataire), le branchement des API Orange Money / Wave / Free Money
(la méthode et la référence de transaction sont déjà enregistrées, mais
l'encaissement se saisit à la caisse), la génération des fichiers de
rapport, les portails Parents et Élève, et la gestion des ressources
humaines.

## Module 7 — Gestion financière

Le module couvre le cycle complet décrit dans le TDR : facturer, encaisser,
justifier, relancer, piloter.

| Étape | Écran | API |
|---|---|---|
| Facturer une classe ou toute l'école | `/finances/generer` | `POST /api/frais-eleves/generer/` |
| Suivre les impayés | `/finances` | `GET /api/frais-eleves/impayes/` |
| Encaisser (espèces, Orange Money, Wave, Free Money, carte) | `/finances/encaisser/:id` | `POST /api/paiements/` |
| Reçu PDF remis à la famille | bouton après encaissement | `GET /api/recus/{id}/pdf/` |
| Relancer les échéances dépassées | `/finances/relances` | `POST /api/relances/generer/` |
| Journal caisse & banque | `/finances/journal` | `GET /api/journal-caisse/` |
| Dépenses et budgets | `/depenses`, `/budgets` | CRUD `depenses`, `budgets` |
| Bilan et pilotage | `/finances/bilan` | `GET /api/finances/bilan/` |

Trois principes structurent le module :

- **Facturation et relances passent par un aperçu.** Le premier appel, sans
  `valider`, dit combien de frais seraient créés (ou de familles relancées)
  et pour quel montant ; rien n'est écrit. Le second applique. Générer dix
  mensualités pour six cents élèves ne se défait pas d'un clic. La
  génération est de plus idempotente : un frais déjà présent pour le même
  élève, le même type et la même échéance est ignoré, jamais dupliqué —
  on peut donc relancer l'opération après l'arrivée de nouveaux inscrits.

- **Le journal de caisse est la seule source du solde.** Chaque encaissement
  y écrit un crédit, chaque dépense un débit, et `solde_apres` est calculé à
  l'insertion. Les espèces vont au journal *caisse*, le mobile money et la
  carte au journal *banque*. Modifier ou supprimer une dépense corrige son
  mouvement et réimpute le budget concerné : le réalisé d'un budget ne se
  saisit pas, il se constate à partir des dépenses.

- **Les exports ne coûtent aucune dépendance.** Le `.xlsx` s'appuie sur
  `openpyxl`, déjà là pour l'import des listes d'élèves ; le PDF est écrit
  directement dans `gestion_scolaire/exports.py` (polices standard PDF,
  encodage WinAnsi, pagination et table xref). Une école n'a pas à installer
  un moteur de rendu pour imprimer un reçu.

Tous les exports s'obtiennent avec `?format_export=xlsx` ou `pdf` :

```bash
GET /api/frais-eleves/impayes/export/?format_export=pdf
GET /api/journal-caisse/export/?format_export=xlsx&journal=caisse
GET /api/paiements/export/?format_export=xlsx&debut=2026-10-01
GET /api/depenses/export/?format_export=pdf
GET /api/finances/bilan/?format_export=xlsx
```

Le paramètre s'appelle `format_export` et non `format` : DRF réserve `format`
à la négociation de contenu et répond 404 sur une valeur qu'il ne connaît pas.

## Module 2 — Gestion des élèves

Le parcours complet de l'élève, de l'inscription à la sortie.

| Étape | Écran | API |
|---|---|---|
| Inscrire (élève + inscription + parent) | `/eleves/nouveau` | `POST /api/eleves/inscrire/` |
| Importer une liste (.xlsx, .csv, .docx) | `/eleves/importer` | `POST /api/eleves/importer/` |
| Dossier numérique complet | `/eleves/:id/dossier` | `GET /api/eleves/{id}/dossier/` |
| Changer de classe | dossier → Mouvements | `POST /api/eleves/{id}/changer-classe/` |
| Radier | dossier → Mouvements | `POST /api/eleves/{id}/radier/` |
| Transférer | dossier → Mouvements | `POST /api/eleves/{id}/transferer/` |
| Réinscrire une promotion | `/eleves/reinscrire` | `POST /api/inscriptions/reinscrire/` |

Trois principes, tenus dans `gestion_scolaire/scolarite.py` :

- **Aucun mouvement sans trace.** Chaque geste écrit une ligne d'historique
  scolaire au bon type d'événement. L'inscription initiale est tracée par
  `Inscription.save()` ; les mouvements ultérieurs le sont dans `scolarite`,
  parce qu'eux seuls connaissent la classe quittée.

- **L'effectif suit le mouvement.** `Classe.effectif` est dénormalisé. Il
  n'était recalé que par l'import de listes : une radiation laissait donc un
  effectif faux sur le tableau de bord. Tous les chemins d'écriture passent
  désormais par `recaler_effectif()`, qui recompte plutôt qu'incrémente.

- **La réinscription passe par un aperçu**, comme la facturation : « 42 élèves
  passeraient de 6ème A en 5ème A » avant d'écrire. L'opération est idempotente
  et ignore les élèves radiés ou transférés. Un dépassement de capacité est
  signalé, pas bloqué — une classe surchargée en début d'année est une réalité.

Le dossier numérique assemble en une réponse l'identité, les parents, le
parcours, les pièces, les résultats, la situation financière et l'assiduité :
six appels distincts obligeraient le front à recoudre des données qui n'ont de
sens qu'ensemble.

## Module 5 — Gestion pédagogique

| Étape | Écran | API |
|---|---|---|
| Créer une évaluation (barème, coefficient) | `/evaluations` | CRUD `evaluations` |
| Saisir les notes d'une classe en grille | `/pedagogie/notes` | `GET/POST /api/evaluations/{id}/grille/` puis `/notes/` |
| Calculer les moyennes d'une classe | `/pedagogie/bulletins` | `GET /api/bulletins/moyennes/` |
| Générer les bulletins | `/pedagogie/bulletins` | `POST /api/bulletins/generer/` |
| Bulletin PDF d'un élève | bouton après génération | `GET /api/bulletins/{id}/pdf/` |
| Relevé de notes détaillé | dossier de l'élève | `GET /api/eleves/{id}/releve/` |
| Palmarès et statistiques | `/pedagogie/palmares` | `GET /api/bulletins/palmares/`, `/statistiques/` |

Les règles de calcul vivent dans `gestion_scolaire/pedagogie.py`, écrites une
seule fois :

- **Les notes sont ramenées sur 20 avant d'être moyennées**, via le `bareme` de
  leur évaluation. Une interrogation sur 10 et une composition sur 20 ne se
  moyennent pas telles quelles. L'enseignant continue de saisir la note portée
  sur la copie ; la conversion est l'affaire du calcul.

- **Deux pondérations distinctes.** À l'intérieur d'une matière, les
  évaluations pèsent par leur `coefficient` ; entre matières, ce sont les
  `CoefficientMatiere` de la classe. Une matière sans coefficient déclaré
  compte pour 1.

- **Une matière sans note ne compte pas**, plutôt que de compter zéro : un
  élève non évalué en sport n'a pas à voir sa moyenne s'effondrer. De même, un
  élève sans aucune note n'est pas classé dernier — il n'est pas classé, et ne
  reçoit pas de bulletin vide.

- **Le rang suit la convention des établissements** : ex æquo au même rang, et
  le rang suivant saute d'autant (1, 2, 2, 4).

La saisie se fait en grille et part en une requête : le CRUD imposait un
aller-retour par élève, soit soixante requêtes pour un devoir de 6ème. Elle est
transactionnelle — une seule note hors barème et rien n'est écrit, plutôt
qu'une grille à moitié saisie. Une case laissée vide efface la note (élève
absent, saisie erronée) au lieu de valoir zéro.

La génération des bulletins passe par le même aperçu que la facturation, et
régénérer met à jour les bulletins existants : une note corrigée après le
conseil de classe doit pouvoir se répercuter.

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

## Importer une liste d'élèves

Écran : **Élèves → Importer une liste** (`/eleves/importer`, réservé aux admins).
Endpoint : `POST /api/eleves/importer/` en `multipart/form-data`.

| Champ | Rôle |
|---|---|
| `fichier` | La liste, en `.xlsx`, `.csv` ou `.docx` |
| `classe` | Facultatif — force la classe de tout le fichier |
| `valider` | Absent : aperçu seul. `true` : écrit en base |

L'import se fait **en deux appels avec le même fichier** : le premier renvoie
un aperçu de ce qui serait créé, le second écrit. Le fichier est ré-analysé
côté serveur à la validation, plutôt que de faire revenir le plan par le
navigateur — un plan renvoyé par le client serait un moyen d'écrire des élèves
qu'aucun fichier ne contenait.

**Le format Word est accepté volontairement.** Les écoles produisent leurs
listes nominatives sous Word, un tableau par classe ; n'accepter que l'Excel
imposerait de tout ressaisir. Le nom de la classe est lu dans le titre au-dessus
du tableau (« Liste nominative CE1 A 2025 / 2026 Mme NDIAYE » → classe `CE1 A`,
niveau `CE1`). Un document qui empile plusieurs tableaux devient plusieurs
classes. Les anciens `.doc` et `.xls` ne sont pas lus — il faut les
réenregistrer en `.docx` / `.xlsx`.

Colonnes reconnues, quel que soit l'intitulé exact : `nom`, `prenom`, `classe`,
`telephone`, `sexe`, `matricule`. Dans un tableau Word sans en-tête, la
disposition `N° | PRÉNOMS | NOMS` des listes d'école est supposée.

Ce que l'import décide, ligne par ligne :

- **création** — l'élève est créé, inscrit dans sa classe pour l'année scolaire
  active, et reçoit un matricule `PREFIXE-ANNEE-0001` (le préfixe vient du
  paramètre `prefixe_matricule`) ;
- **doublon** — un élève de même nom et prénom est déjà inscrit *dans cette
  école et dans cette classe* : rien n'est écrit. C'est le cas du fichier
  réimporté par erreur. Le contrôle est borné à l'école, sinon deux écoles
  clientes se bloqueraient mutuellement des inscriptions ;
- **ignorée** — ligne vide, sans nom, ou sans classe identifiable.

Faute de date de naissance dans les listes, un élève est identifié par
**nom + prénom + classe**. La classe fait partie de l'identité parce que les
homonymes sont fréquents : dans les listes de Mic Cité, treize noms
apparaissent dans deux classes différentes — un élève de CI A et un élève de
CM2 A ne sont pas le même enfant. Ces homonymes sont créés, et signalés dans
l'aperçu pour vérification.

Les classes et niveaux absents sont créés au passage. Les annotations
d'enseignant entre parenthèses (`LO (NN)`) sont sorties de l'état civil et
affichées à part, pour que rien ne disparaisse en silence. Toute l'écriture
est dans une transaction : un import à moitié appliqué laisserait des élèves
sans inscription.

`openpyxl` n'est nécessaire que pour le `.xlsx` ; le `.csv` et le `.docx` sont
lus avec la bibliothèque standard.

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
