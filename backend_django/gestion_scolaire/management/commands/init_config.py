"""
Configuration de base d'une école : paramètres et catégories de dépense.

    python manage.py init_config                # toutes les écoles
    python manage.py init_config --ecole 1      # une seule

À la différence de `seed_demo`, cette commande ne crée aucune donnée fictive :
elle est destinée à être lancée sur une vraie école, à sa création. Elle est
idempotente et ne touche jamais à une valeur déjà personnalisée.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from gestion_scolaire import models

P = models.ParametreEcole

# (cle, libelle, valeur, type_valeur, categorie, description)
PARAMETRES = [
    # --- Général ---
    ("devise", "Devise", "FCFA", P.TypeValeur.TEXTE, P.Categorie.GENERAL,
     "Symbole affiché à côté des montants."),
    ("nom_directeur", "Nom du directeur", "", P.TypeValeur.TEXTE, P.Categorie.GENERAL,
     "Apparaît sur les bulletins et les documents officiels."),
    ("langue_documents", "Langue des documents", "fr", P.TypeValeur.TEXTE, P.Categorie.GENERAL,
     "Langue des bulletins et reçus générés."),
    ("prefixe_matricule", "Préfixe des matricules", "ELV", P.TypeValeur.TEXTE, P.Categorie.GENERAL,
     "Les matricules sont composés ainsi : PREFIXE-ANNEE-0001."),

    # --- Scolarité ---
    ("capacite_classe_defaut", "Capacité par défaut d'une classe", "40",
     P.TypeValeur.NOMBRE, P.Categorie.SCOLARITE,
     "Proposée à la création d'une classe."),
    ("age_minimum_inscription", "Âge minimum à l'inscription", "3",
     P.TypeValeur.NOMBRE, P.Categorie.SCOLARITE,
     "En années révolues."),
    ("jours_ouvres", "Jours de cours", '["lundi","mardi","mercredi","jeudi","vendredi"]',
     P.TypeValeur.JSON, P.Categorie.SCOLARITE,
     "Jours proposés dans l'emploi du temps."),
    ("seuil_absences_alerte", "Absences avant alerte", "5",
     P.TypeValeur.NOMBRE, P.Categorie.SCOLARITE,
     "Nombre d'absences non justifiées déclenchant une alerte sur la période."),

    # --- Notation ---
    ("note_maximale", "Note maximale", "20", P.TypeValeur.NOMBRE, P.Categorie.NOTATION,
     "Barème de saisie des notes."),
    ("moyenne_passage", "Moyenne de passage", "10", P.TypeValeur.NOMBRE, P.Categorie.NOTATION,
     "Seuil d'admission en classe supérieure."),
    ("decimales_moyenne", "Décimales des moyennes", "2",
     P.TypeValeur.NOMBRE, P.Categorie.NOTATION,
     "Arrondi appliqué aux moyennes des bulletins."),
    ("afficher_rang", "Afficher le rang sur le bulletin", "true",
     P.TypeValeur.BOOLEEN, P.Categorie.NOTATION, None),

    # --- Finance ---
    ("prefixe_numero_recu", "Préfixe des numéros de reçu", "REC",
     P.TypeValeur.TEXTE, P.Categorie.FINANCE,
     "Composé ainsi : PREFIXE-ANNEE-NUMERO."),
    ("entete_recu", "En-tête des reçus", "", P.TypeValeur.TEXTE, P.Categorie.FINANCE,
     "Texte libre imprimé en haut des reçus."),
    ("paiement_partiel_autorise", "Autoriser les paiements partiels", "true",
     P.TypeValeur.BOOLEEN, P.Categorie.FINANCE,
     "Un frais peut être réglé en plusieurs versements."),
    ("seuil_relance_jours", "Délai avant relance", "15",
     P.TypeValeur.NOMBRE, P.Categorie.FINANCE,
     "Jours après l'échéance avant de relancer une famille."),

    # --- Notifications ---
    ("canal_relance_defaut", "Canal de relance par défaut", "sms",
     P.TypeValeur.TEXTE, P.Categorie.NOTIFICATION,
     "sms ou email."),
    ("notifier_absence_parent", "Prévenir les parents d'une absence", "true",
     P.TypeValeur.BOOLEEN, P.Categorie.NOTIFICATION, None),
    ("heure_envoi_notifications", "Heure d'envoi des notifications", "18:00",
     P.TypeValeur.TEXTE, P.Categorie.NOTIFICATION,
     "Heure à laquelle les envois groupés partent."),
]

TYPES_DEPENSE = [
    "Salaires et charges",
    "Loyer",
    "Électricité et eau",
    "Fournitures scolaires",
    "Matériel pédagogique",
    "Entretien et maintenance",
    "Transport",
    "Communication et internet",
    "Frais administratifs",
    "Autre",
]


class Command(BaseCommand):
    help = "Installe les paramètres et catégories de dépense par défaut d'une école."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ecole", type=int, default=None,
            help="Identifiant de l'école. Par défaut, toutes les écoles.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        ecoles = models.Ecole.objects.all()
        if options["ecole"] is not None:
            ecoles = ecoles.filter(pk=options["ecole"])
            if not ecoles.exists():
                self.stderr.write(self.style.ERROR(
                    f"Aucune école d'identifiant {options['ecole']}."
                ))
                return

        if not ecoles.exists():
            self.stderr.write(self.style.WARNING(
                "Aucune école en base. Crée d'abord une école, ou lance seed_demo."
            ))
            return

        for ecole in ecoles:
            params = depenses = 0

            for cle, libelle, valeur, type_valeur, categorie, description in PARAMETRES:
                _, cree = models.ParametreEcole.objects.get_or_create(
                    id_ecole=ecole, cle=cle,
                    defaults={
                        "libelle": libelle, "valeur": valeur, "type_valeur": type_valeur,
                        "categorie": categorie, "description": description,
                    },
                )
                params += cree

            for libelle in TYPES_DEPENSE:
                _, cree = models.TypeDepense.objects.get_or_create(
                    id_ecole=ecole, libelle=libelle,
                )
                depenses += cree

            self.stdout.write(self.style.SUCCESS(
                f"{ecole.nom} — {params} paramètre(s) et {depenses} catégorie(s) "
                f"de dépense ajoutés."
            ))
