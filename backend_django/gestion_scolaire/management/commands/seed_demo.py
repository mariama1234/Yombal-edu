"""
Jeu de données minimal pour démarrer et tester l'application en local.

    python manage.py seed_demo

Idempotent : relancer la commande ne crée pas de doublons. Ces données sont
provisoires — elles seront remplacées par la base réelle de l'école MIC CITE.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from gestion_scolaire import finances, models, pedagogie, scolarite

Utilisateur = get_user_model()

MOT_DE_PASSE_DEMO = "motdepasse123"


class Command(BaseCommand):
    help = "Crée une école de démonstration, ses utilisateurs et quelques élèves."

    @transaction.atomic
    def handle(self, *args, **options):
        ecole, _ = models.Ecole.objects.get_or_create(
            nom="École MIC CITE",
            defaults={
                "adresse": "Dakar, Sénégal",
                "telephone": "+221 33 000 00 00",
                "email": "contact@miccite.sn",
                "plan_abonnement": models.Ecole.PlanAbonnement.STANDARD,
                "date_activation": date.today(),
            },
        )

        annee, _ = models.AnneeScolaire.objects.get_or_create(
            id_ecole=ecole,
            libelle="2025-2026",
            defaults={"date_debut": date(2025, 10, 1), "date_fin": date(2026, 7, 31)},
        )

        for libelle, debut, fin in [
            ("Trimestre 1", date(2025, 10, 1), date(2025, 12, 20)),
            ("Trimestre 2", date(2026, 1, 5), date(2026, 3, 31)),
            ("Trimestre 3", date(2026, 4, 6), date(2026, 7, 31)),
        ]:
            models.PeriodeScolaire.objects.get_or_create(
                id_annee_scolaire=annee, libelle=libelle,
                defaults={"date_debut": debut, "date_fin": fin},
            )

        comptes = [
            ("admin@miccite.sn", "Diallo", "Ndalé", Utilisateur.Role.ADMIN, True),
            ("caissier@miccite.sn", "Sow", "Fatou", Utilisateur.Role.CAISSIER, False),
            ("prof@miccite.sn", "Ndiaye", "Moussa", Utilisateur.Role.ENSEIGNANT, False),
        ]
        for email, nom, prenom, role, staff in comptes:
            if not Utilisateur.objects.filter(email=email).exists():
                u = Utilisateur.objects.create_user(
                    email=email, id_ecole=ecole, nom=nom, prenom=prenom, role=role,
                    password=MOT_DE_PASSE_DEMO,
                )
                u.is_staff = staff
                u.is_superuser = staff
                u.save()

        prof_user = Utilisateur.objects.get(email="prof@miccite.sn")
        enseignant, _ = models.Enseignant.objects.get_or_create(
            id_ecole=ecole, matricule="ENS-001",
            defaults={"id_utilisateur": prof_user, "specialite": "Mathématiques",
                      "date_embauche": date(2023, 9, 1)},
        )

        niveau, _ = models.Niveau.objects.get_or_create(id_ecole=ecole, libelle="6ème")
        classe, _ = models.Classe.objects.get_or_create(
            id_ecole=ecole, id_annee_scolaire=annee, id_niveau=niveau, nom="6ème A",
            defaults={"capacite_max": 40, "id_enseignant_principal": enseignant},
        )

        for nom_matiere in ["Mathématiques", "Français", "Anglais", "Histoire-Géographie", "SVT"]:
            models.Matiere.objects.get_or_create(id_ecole=ecole, nom=nom_matiere)

        type_mensualite, _ = models.TypeFrais.objects.get_or_create(
            id_ecole=ecole, libelle=models.TypeFrais.Libelle.MENSUALITE,
            defaults={"montant_defaut": 25000, "frequence": "Mensuelle"},
        )
        models.TypeFrais.objects.get_or_create(
            id_ecole=ecole, libelle=models.TypeFrais.Libelle.INSCRIPTION,
            defaults={"montant_defaut": 50000, "frequence": "Annuelle"},
        )

        eleves = [
            ("MIC-2026-001", "Ba", "Aïssatou", "F", date(2013, 4, 12)),
            ("MIC-2026-002", "Fall", "Ibrahima", "M", date(2013, 9, 3)),
            ("MIC-2026-003", "Sarr", "Mariama", "F", date(2014, 1, 25)),
        ]
        for matricule, nom, prenom, sexe, naissance in eleves:
            eleve, cree = models.Eleve.objects.get_or_create(
                id_ecole=ecole, matricule=matricule,
                defaults={"nom": nom, "prenom": prenom, "sexe": sexe,
                          "date_naissance": naissance,
                          "date_premiere_inscription": date(2025, 9, 15)},
            )
            models.Inscription.objects.get_or_create(
                id_eleve=eleve, id_annee_scolaire=annee,
                defaults={"id_classe": classe, "date_inscription": date(2025, 9, 15)},
            )
            models.FraisEleve.objects.get_or_create(
                id_eleve=eleve, id_type_frais=type_mensualite, id_annee_scolaire=annee,
                defaults={"montant_du": 25000, "echeance": date(2025, 11, 5)},
            )

        # Côté charges : sans catégories ni budget, les écrans Dépenses,
        # Budgets et Bilan s'ouvrent vides et on ne voit pas ce qu'ils font.
        types_depense = {}
        for libelle in ["Loyer", "Salaires", "Fournitures"]:
            types_depense[libelle], _ = models.TypeDepense.objects.get_or_create(
                id_ecole=ecole, libelle=libelle)

        models.Budget.objects.get_or_create(
            id_ecole=ecole, id_type_depense=types_depense["Loyer"],
            id_annee_scolaire=annee, categorie="Charges fixes",
            defaults={"montant_prevu": 1800000},
        )
        models.Budget.objects.get_or_create(
            id_ecole=ecole, id_type_depense=types_depense["Salaires"],
            id_annee_scolaire=annee, categorie="Masse salariale",
            defaults={"montant_prevu": 9000000},
        )

        for libelle, motif, montant, jour in [
            ("Loyer", "Loyer octobre", 150000, date(2025, 10, 5)),
            ("Salaires", "Salaires octobre", 720000, date(2025, 10, 28)),
            ("Fournitures", "Craies et registres", 45000, date(2025, 10, 12)),
        ]:
            depense, cree = models.Depense.objects.get_or_create(
                id_ecole=ecole, id_type_depense=types_depense[libelle], motif=motif,
                defaults={"montant": montant, "date_depense": jour},
            )
            # Passe par la même fonction que l'API : mouvement au journal
            # et budget réalisé recalculé, sinon la démo montrerait un
            # solde de caisse qui ne correspond à aucune dépense.
            if cree:
                finances.enregistrer_depense(depense)

        # Côté pédagogie : sans coefficients, évaluations ni notes, les écrans
        # Saisie des notes, Bulletins et Palmarès s'ouvrent vides — et c'est
        # le cœur de la proposition de valeur qu'on ne voit pas tourner.
        trimestre = models.PeriodeScolaire.objects.get(
            id_annee_scolaire=annee, libelle="Trimestre 1")
        maths = models.Matiere.objects.get(id_ecole=ecole, nom="Mathématiques")
        francais = models.Matiere.objects.get(id_ecole=ecole, nom="Français")

        for matiere, coefficient in [(maths, 4), (francais, 2)]:
            models.CoefficientMatiere.objects.get_or_create(
                id_matiere=matiere, id_classe=classe, id_annee_scolaire=annee,
                defaults={"coefficient": coefficient},
            )

        # Un devoir sur 10 et une composition sur 20 : le barème n'est pas
        # décoratif, il montre que les notes sont ramenées sur 20 au calcul.
        evaluations = {}
        for cle, matiere, type_eval, jour, bareme, coefficient in [
            ("devoir_maths", maths, models.Evaluation.Type.DEVOIR,
             date(2025, 11, 10), 10, 1),
            ("compo_maths", maths, models.Evaluation.Type.COMPOSITION,
             date(2025, 12, 10), 20, 3),
            ("compo_francais", francais, models.Evaluation.Type.COMPOSITION,
             date(2025, 12, 12), 20, 2),
        ]:
            evaluations[cle], _ = models.Evaluation.objects.get_or_create(
                id_matiere=matiere, id_classe=classe, id_periode=trimestre,
                type=type_eval, date_evaluation=jour,
                defaults={"bareme": bareme, "coefficient": coefficient},
            )

        inscrits = list(
            models.Eleve.objects.filter(id_ecole=ecole, matricule__startswith="MIC-2026-")
            .order_by("matricule")
        )
        # Les valeurs sont choisies pour donner un classement net (14,50 /
        # 14,17 / 12,17) : un ex æquo en tête ferait douter du calcul dès la
        # première ouverture de l'écran.
        notes = {
            "devoir_maths": [8, 6, 9],
            "compo_maths": [15, 11, 17],
            "compo_francais": [13, 14, 8],
        }
        for cle, valeurs in notes.items():
            for eleve, valeur in zip(inscrits, valeurs):
                models.Note.objects.get_or_create(
                    id_eleve=eleve, id_evaluation=evaluations[cle],
                    defaults={"valeur": valeur, "id_utilisateur": prof_user},
                )

        scolarite.recaler_effectif(classe)

        # Les bulletins passent par la même fonction que l'API : moyennes
        # pondérées, rang et appréciation calculés une seule fois, ici comme
        # en production.
        if inscrits:
            pedagogie.generer_bulletins(ecole, classe, trimestre, appliquer=True)

        self.stdout.write(self.style.SUCCESS(
            f"Données de démo prêtes — école « {ecole.nom} », "
            f"{models.Eleve.objects.filter(id_ecole=ecole).count()} élèves.\n"
            f"Connexion : admin@miccite.sn / {MOT_DE_PASSE_DEMO}"
        ))
