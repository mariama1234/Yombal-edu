# YombalEdu

Application de gestion scolaire avec un backend Django REST et un frontend Angular.

## Fonctionnalités

- Gestion des élèves, classes, inscriptions et présences
- Notes, bulletins et statistiques
- Facturation, paiements et reçus
- Gestion multi-écoles et API sécurisée

## Démarrage

### Backend

Prérequis : Python 3.12+ et MySQL 8.0.11+ ou MariaDB 10.11+.

```bash
cd backend_django
python -m venv venv
```

Active l’environnement virtuel, puis exécute :

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Configure la connexion à la base avec les variables `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `DB_HOST` et `DB_PORT`. Pour les essais locaux sans serveur MySQL,
définis `DB_ENGINE=sqlite`.

### Frontend

Prérequis : Node.js.

```bash
cd frontend_angular
npm install
npm start
```

Ouvre [http://localhost:4200](http://localhost:4200). L’API Django utilise
par défaut [http://localhost:8000/api](http://localhost:8000/api).

## Documentation de l’API

Lance le backend et consulte [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/).
