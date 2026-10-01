"""
Modèles Django — YombalEdu
Traduction fidèle du schéma MySQL validé (schema_gestion_scolaire.sql).
Organisé dans le même ordre que le script SQL : cœur multi-écoles,
module élèves, module pédagogique, emploi du temps, module financier.
"""
from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models, transaction
from django.core.validators import MinValueValidator
from django.utils import timezone


# =====================================================================
# 0. ECOLE (racine du multi-tenant)
# =====================================================================

class Ecole(models.Model):
    class PlanAbonnement(models.TextChoices):
        ESSAI = "essai", "Essai"
        STANDARD = "standard", "Standard"
        PRO = "pro", "Pro"

    class Statut(models.TextChoices):
        ACTIF = "actif", "Actif"
        SUSPENDU = "suspendu", "Suspendu"
        RESILIE = "resilie", "Résilié"

    nom = models.CharField(max_length=150)
    adresse = models.CharField(max_length=255, blank=True, null=True)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(max_length=150, blank=True, null=True)
    logo_url = models.CharField(max_length=255, blank=True, null=True)
    plan_abonnement = models.CharField(max_length=20, choices=PlanAbonnement.choices, default=PlanAbonnement.ESSAI)
    date_activation = models.DateField(blank=True, null=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.ACTIF)
    # L'abonnement de la période en cours est-il réglé ? Tenu par le super
    # administrateur de la plateforme, qui encaisse les écoles clientes.
    abonnement_paye = models.BooleanField(default=False)
    date_paiement = models.DateField(blank=True, null=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ecole"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


# =====================================================================
# 1. UTILISATEURS & ROLES (AUTH_USER_MODEL)
# =====================================================================

class UtilisateurManager(BaseUserManager):
    def create_user(self, email, id_ecole=None, password=None, **extra_fields):
        if not email:
            raise ValueError("L'adresse e-mail est obligatoire.")
        user = self.model(email=self.normalize_email(email), id_ecole=id_ecole, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, id_ecole, password=None, **extra_fields):
        extra_fields.setdefault("role", Utilisateur.Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, id_ecole, password, **extra_fields)


class Utilisateur(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        # Les droits de chaque rôle sont dans permissions.py (matrice par profil).
        # Le super administrateur n'y figure pas : il gère la plateforme (les
        # écoles clientes et leur premier compte), jamais l'intérieur d'une école.
        SUPER_ADMIN = "super_admin", "Super administrateur"
        ADMIN = "admin", "Administrateur"
        DIRECTEUR = "directeur", "Directeur"
        RESPONSABLE_ADMINISTRATIF = "responsable_administratif", "Responsable administratif"
        SURVEILLANT = "surveillant", "Surveillant"
        SECRETAIRE = "secretaire", "Secrétaire administratif"
        CAISSIER = "caissier", "Caissier"
        COMPTABLE = "comptable", "Comptable"
        RESPONSABLE_FINANCIER = "responsable_financier", "Responsable financier"
        ENSEIGNANT = "enseignant", "Enseignant"
        PARENT = "parent", "Parent"
        ELEVE = "eleve", "Élève"

    class Statut(models.TextChoices):
        ACTIF = "actif", "Actif"
        INACTIF = "inactif", "Inactif"

    # Vide pour le seul super administrateur : il n'appartient à aucune école.
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="utilisateurs",
                                 db_column="id_ecole", null=True, blank=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    email = models.EmailField(max_length=150, unique=True)
    role = models.CharField(max_length=30, choices=Role.choices)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.ACTIF)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    objects = UtilisateurManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["id_ecole", "nom", "prenom", "role"]

    class Meta:
        db_table = "utilisateur"
        ordering = ["nom", "prenom"]
        constraints = [
            # Le super administrateur n'a pas d'école, tout autre compte en a
            # une : sans ce garde-fou, un compte d'école sans école échapperait
            # au cloisonnement, qui filtre sur `id_ecole`.
            models.CheckConstraint(
                condition=(models.Q(role="super_admin", id_ecole__isnull=True)
                           | (~models.Q(role="super_admin") & models.Q(id_ecole__isnull=False))),
                name="chk_utilisateur_ecole_selon_role",
            ),
        ]

    def __str__(self):
        return f"{self.prenom} {self.nom} ({self.role})"


# =====================================================================
# 2. MODULE GESTION DES ELEVES
# =====================================================================

class AnneeScolaire(models.Model):
    class Statut(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVEE = "archivee", "Archivée"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="annees_scolaires", db_column="id_ecole")
    libelle = models.CharField(max_length=20)  # ex: "2026-2027"
    date_debut = models.DateField()
    date_fin = models.DateField()
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.ACTIVE)

    class Meta:
        db_table = "annee_scolaire"
        ordering = ["-date_debut"]
        constraints = [
            models.UniqueConstraint(fields=["id_ecole", "libelle"], name="uq_annee_ecole")
        ]

    def save(self, *args, **kwargs):
        """
        Une seule année active par école : activer celle-ci archive les autres.

        L'invariant est tenu ici plutôt que dans la vue parce qu'il existe
        plusieurs chemins d'activation — l'écran des années, les commandes de
        seed, le shell — et qu'aucun ne doit pouvoir en laisser deux actives.
        C'était la cause d'un blocage réel : l'écran d'inscription se
        présélectionnait sur la mauvaise des deux, et refusait ensuite toutes
        les classes proposées.

        Le contraire — exiger qu'il en reste toujours une — n'est pas imposé :
        entre deux années, tout archiver est un état légitime.
        """
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.statut == self.Statut.ACTIVE:
                # `update()` ne repasse pas par save() : pas de récursion.
                AnneeScolaire.objects.filter(
                    id_ecole_id=self.id_ecole_id, statut=self.Statut.ACTIVE,
                ).exclude(pk=self.pk).update(statut=self.Statut.ARCHIVEE)

    def __str__(self):
        return self.libelle


class Eleve(models.Model):
    class Sexe(models.TextChoices):
        M = "M", "Masculin"
        F = "F", "Féminin"

    class Statut(models.TextChoices):
        ACTIF = "actif", "Actif"
        RADIE = "radie", "Radié"
        TRANSFERE = "transfere", "Transféré"
        DIPLOME = "diplome", "Diplômé"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="eleves", db_column="id_ecole")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="eleve_profil", db_column="id_utilisateur")
    matricule = models.CharField(max_length=30)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    date_naissance = models.DateField(blank=True, null=True)
    sexe = models.CharField(max_length=1, choices=Sexe.choices, blank=True, null=True)
    adresse = models.CharField(max_length=255, blank=True, null=True)
    photo_url = models.CharField(max_length=255, blank=True, null=True)
    date_premiere_inscription = models.DateField(blank=True, null=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.ACTIF)

    class Meta:
        db_table = "eleve"
        ordering = ["nom", "prenom"]
        constraints = [
            models.UniqueConstraint(fields=["id_ecole", "matricule"], name="uq_eleve_matricule_ecole")
        ]

    def __str__(self):
        return f"{self.prenom} {self.nom} ({self.matricule})"


class ParentTuteur(models.Model):
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="parents", db_column="id_ecole")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="parent_profil", db_column="id_utilisateur")
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    profession = models.CharField(max_length=100, blank=True, null=True)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(max_length=150, blank=True, null=True)

    class Meta:
        db_table = "parent_tuteur"
        ordering = ["nom", "prenom"]

    def __str__(self):
        return f"{self.prenom} {self.nom}"


class EleveParent(models.Model):
    class LienParente(models.TextChoices):
        PERE = "pere", "Père"
        MERE = "mere", "Mère"
        TUTEUR = "tuteur", "Tuteur"
        AUTRE = "autre", "Autre"

    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="liens_parents", db_column="id_eleve")
    id_parent = models.ForeignKey(ParentTuteur, on_delete=models.CASCADE, related_name="liens_eleves", db_column="id_parent")
    lien_parente = models.CharField(max_length=10, choices=LienParente.choices)
    contact_principal = models.BooleanField(default=False)

    class Meta:
        db_table = "eleve_parent"
        ordering = ["id_eleve", "id_parent"]
        constraints = [
            models.UniqueConstraint(fields=["id_eleve", "id_parent"], name="pk_eleve_parent")
        ]


class DossierAdministratif(models.Model):
    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="dossiers", db_column="id_eleve")
    type_piece = models.CharField(max_length=100)
    fichier_url = models.CharField(max_length=255)
    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dossier_administratif"
        ordering = ["-date_ajout"]


class Niveau(models.Model):
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="niveaux", db_column="id_ecole")
    libelle = models.CharField(max_length=50)
    # Les montants des frais pour ce niveau vivent dans la grille des tarifs
    # (`TarifNiveau`), un par type de frais.

    class Meta:
        db_table = "niveau"
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle


class Enseignant(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "actif", "Actif"
        INACTIF = "inactif", "Inactif"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="enseignants", db_column="id_ecole")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="enseignant_profil", db_column="id_utilisateur")
    matricule = models.CharField(max_length=30)
    specialite = models.CharField(max_length=100, blank=True, null=True)
    date_embauche = models.DateField(blank=True, null=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.ACTIF)

    class Meta:
        db_table = "enseignant"
        ordering = ["matricule"]
        constraints = [
            models.UniqueConstraint(fields=["id_ecole", "matricule"], name="uq_enseignant_matricule_ecole")
        ]

    def __str__(self):
        return f"{self.prenom if hasattr(self,'prenom') else ''} {self.nom if hasattr(self,'nom') else self.matricule}"


class Classe(models.Model):
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="classes", db_column="id_ecole")
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="classes", db_column="id_annee_scolaire")
    # Facultatif : une école peut nommer ses classes sans les ranger par niveau.
    # Supprimer un niveau détache donc ses classes au lieu de les supprimer.
    id_niveau = models.ForeignKey(Niveau, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="classes", db_column="id_niveau")
    nom = models.CharField(max_length=50)
    effectif = models.PositiveIntegerField(default=0)
    capacite_max = models.PositiveIntegerField()
    id_enseignant_principal = models.ForeignKey(Enseignant, on_delete=models.SET_NULL, null=True, blank=True,
                                                  related_name="classes_dirigees", db_column="id_enseignant_principal")

    class Meta:
        db_table = "classe"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    @property
    def libelle_niveau(self):
        """Libellé du niveau, ou `None` : le niveau d'une classe est facultatif."""
        return self.id_niveau.libelle if self.id_niveau_id else None


class Inscription(models.Model):
    class Statut(models.TextChoices):
        # Une inscription naît « en attente » : la place est réservée, la
        # classe est choisie, les frais sont émis — mais l'élève ne compte pas
        # encore parmi les inscrits de l'année. C'est la règle de l'école : il
        # est inscrit quand les droits d'inscription ET le premier mois sont
        # réglés (voir `finances.confirmer_inscription`) ; avant, il est
        # « inscrit incomplet ». Sans cet état intermédiaire, un élève jamais
        # payé était indiscernable d'un élève à jour dans toutes les listes.
        EN_ATTENTE = "en_attente", "Inscrit incomplet"
        # « Inscrit » et non « En cours » : c'est le mot que l'école emploie,
        # et le seul qui s'oppose lisiblement à « Inscrit incomplet ».
        # « En cours » se lisait comme une démarche inachevée.
        EN_COURS = "en_cours", "Inscrit"
        TERMINEE = "terminee", "Terminée"
        ANNULEE = "annulee", "Annulée"

    # Les états qui font d'un élève un inscrit de l'année pour de bon.
    STATUTS_CONFIRMES = ("en_cours", "terminee")

    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="inscriptions", db_column="id_eleve")
    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="inscriptions", db_column="id_classe")
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="inscriptions", db_column="id_annee_scolaire")
    date_inscription = models.DateField()
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    # Exception décidée par l'administration : un inscrit incomplet — droits
    # d'inscription réglés, premier mois non — suit malgré tout les cours
    # (difficulté financière passagère). Son statut ne change pas : il reste
    # incomplet, et se confirmera de lui-même au règlement.
    autorise_en_classe = models.BooleanField(default=False)
    motif_autorisation = models.CharField(max_length=255, blank=True, null=True)
    date_autorisation = models.DateTimeField(blank=True, null=True)
    id_utilisateur_autorisation = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="autorisations_en_classe", db_column="id_utilisateur_autorisation")

    @classmethod
    def filtre_en_classe(cls):
        """Les inscrits, et les inscrits incomplets autorisés à suivre les cours."""
        return (models.Q(statut__in=cls.STATUTS_CONFIRMES)
                | models.Q(statut=cls.Statut.EN_ATTENTE, autorise_en_classe=True))

    class Meta:
        db_table = "inscription"
        ordering = ["-date_inscription"]
        constraints = [
            models.UniqueConstraint(fields=["id_eleve", "id_annee_scolaire"], name="uq_inscription_eleve_annee")
        ]

    def save(self, *args, **kwargs):
        """
        Toute inscription laisse une trace dans l'historique scolaire.

        C'est fait ici plutôt que dans la vue parce qu'il existe plusieurs
        chemins d'inscription — l'endpoint composite /eleves/inscrire/, le
        CRUD /inscriptions/, les commandes de seed — et qu'aucun ne doit
        pouvoir créer une inscription sans trace.
        """
        creation = self._state.adding
        super().save(*args, **kwargs)
        if not creation:
            return

        deja_inscrit = (
            Inscription.objects
            .filter(id_eleve_id=self.id_eleve_id)
            .exclude(pk=self.pk)
            .exists()
        )
        HistoriqueScolaire.objects.create(
            id_eleve_id=self.id_eleve_id,
            type_evenement=(
                HistoriqueScolaire.TypeEvenement.REINSCRIPTION if deja_inscrit
                else HistoriqueScolaire.TypeEvenement.INSCRIPTION
            ),
            id_nouvelle_classe_id=self.id_classe_id,
            date_evenement=self.date_inscription,
            motif=f"Inscription en {self.id_classe.nom}",
        )


class HistoriqueScolaire(models.Model):
    class TypeEvenement(models.TextChoices):
        INSCRIPTION = "inscription", "Inscription"
        REINSCRIPTION = "reinscription", "Réinscription"
        CHANGEMENT_CLASSE = "changement_classe", "Changement de classe"
        RADIATION = "radiation", "Radiation"
        TRANSFERT = "transfert", "Transfert"

    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="historique", db_column="id_eleve")
    type_evenement = models.CharField(max_length=25, choices=TypeEvenement.choices)
    id_ancienne_classe = models.ForeignKey(Classe, on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name="+", db_column="id_ancienne_classe")
    id_nouvelle_classe = models.ForeignKey(Classe, on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name="+", db_column="id_nouvelle_classe")
    date_evenement = models.DateField()
    motif = models.CharField(max_length=255, blank=True, null=True)
    commentaire = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "historique_scolaire"
        ordering = ["-date_evenement"]


# =====================================================================
# 3. MODULE GESTION PEDAGOGIQUE
# =====================================================================

class Matiere(models.Model):
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="matieres", db_column="id_ecole")
    nom = models.CharField(max_length=100)

    class Meta:
        db_table = "matiere"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class CoefficientMatiere(models.Model):
    id_matiere = models.ForeignKey(Matiere, on_delete=models.CASCADE, related_name="coefficients", db_column="id_matiere")
    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="coefficients_matieres", db_column="id_classe")
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="coefficients", db_column="id_annee_scolaire")
    coefficient = models.DecimalField(max_digits=4, decimal_places=2, default=1.00)

    class Meta:
        db_table = "coefficient_matiere"
        ordering = ["id_classe", "id_matiere"]
        constraints = [
            models.UniqueConstraint(fields=["id_matiere", "id_classe", "id_annee_scolaire"], name="uq_coeff_matiere_classe_annee")
        ]


class AffectationEnseignant(models.Model):
    id_enseignant = models.ForeignKey(Enseignant, on_delete=models.CASCADE, related_name="affectations", db_column="id_enseignant")
    id_matiere = models.ForeignKey(Matiere, on_delete=models.CASCADE, related_name="affectations", db_column="id_matiere")
    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="affectations", db_column="id_classe")
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="affectations", db_column="id_annee_scolaire")
    volume_horaire = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)

    class Meta:
        db_table = "affectation_enseignant"
        ordering = ["id_classe", "id_matiere"]


class PeriodeScolaire(models.Model):
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="periodes", db_column="id_annee_scolaire")
    libelle = models.CharField(max_length=50)  # ex: "Trimestre 1"
    date_debut = models.DateField()
    date_fin = models.DateField()

    class Meta:
        db_table = "periode_scolaire"
        ordering = ["date_debut"]

    def __str__(self):
        return self.libelle


class Evaluation(models.Model):
    class Type(models.TextChoices):
        DEVOIR = "devoir", "Devoir"
        COMPOSITION = "composition", "Composition"
        EXAMEN = "examen", "Examen"

    id_matiere = models.ForeignKey(Matiere, on_delete=models.CASCADE, related_name="evaluations", db_column="id_matiere")
    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="evaluations", db_column="id_classe")
    id_periode = models.ForeignKey(PeriodeScolaire, on_delete=models.CASCADE, related_name="evaluations", db_column="id_periode")
    type = models.CharField(max_length=15, choices=Type.choices)
    date_evaluation = models.DateField()
    coefficient = models.DecimalField(max_digits=4, decimal_places=2, default=1.00)
    # Barème de l'épreuve. Sans lui, une interrogation notée sur 10 et une
    # composition sur 20 se moyenneraient telles quelles : toutes les moyennes
    # seraient fausses dès qu'un enseignant sort du /20. Les notes sont
    # ramenées sur 20 au calcul, jamais à la saisie — l'enseignant continue de
    # saisir la note telle qu'il l'a mise sur la copie.
    bareme = models.DecimalField(max_digits=5, decimal_places=2, default=20.00,
                                 validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        db_table = "evaluation"
        ordering = ["-date_evaluation"]


class Note(models.Model):
    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="notes", db_column="id_eleve")
    id_evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name="notes", db_column="id_evaluation")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT, related_name="notes_saisies", db_column="id_utilisateur")
    valeur = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0)])
    appreciation = models.CharField(max_length=255, blank=True, null=True)
    date_saisie = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "note"
        ordering = ["-date_saisie"]
        constraints = [
            models.UniqueConstraint(fields=["id_eleve", "id_evaluation"], name="uq_note_eleve_eval")
        ]


class Bulletin(models.Model):
    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="bulletins", db_column="id_eleve")
    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="bulletins", db_column="id_classe")
    id_periode = models.ForeignKey(PeriodeScolaire, on_delete=models.CASCADE, related_name="bulletins", db_column="id_periode")
    moyenne_generale = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    rang_classe = models.PositiveIntegerField(blank=True, null=True)
    appreciation_generale = models.CharField(max_length=255, blank=True, null=True)
    date_generation = models.DateTimeField(auto_now_add=True)
    fichier_pdf_url = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "bulletin"
        ordering = ["-date_generation"]
        constraints = [
            models.UniqueConstraint(fields=["id_eleve", "id_classe", "id_periode"], name="uq_bulletin_eleve_classe_periode")
        ]


class ConseilClasse(models.Model):
    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="conseils", db_column="id_classe")
    id_periode = models.ForeignKey(PeriodeScolaire, on_delete=models.CASCADE, related_name="conseils", db_column="id_periode")
    date_conseil = models.DateField()
    decisions = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "conseil_classe"
        ordering = ["-date_conseil"]


class Absence(models.Model):
    class Statut(models.TextChoices):
        ABSENT = "absent", "Absent"
        RETARD = "retard", "Retard"

    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="absences", db_column="id_eleve")
    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="absences", db_column="id_classe")
    id_matiere = models.ForeignKey(Matiere, on_delete=models.SET_NULL, null=True, blank=True, related_name="absences", db_column="id_matiere")
    date_absence = models.DateTimeField()
    statut = models.CharField(max_length=10, choices=Statut.choices)
    justifiee = models.BooleanField(default=False)
    motif = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "absence"
        ordering = ["-date_absence"]


# =====================================================================
# 4. MODULE EMPLOI DU TEMPS
# =====================================================================

class Salle(models.Model):
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="salles", db_column="id_ecole")
    nom = models.CharField(max_length=50)
    capacite = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        db_table = "salle"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class EmploiDuTemps(models.Model):
    class JourSemaine(models.TextChoices):
        LUNDI = "lundi", "Lundi"
        MARDI = "mardi", "Mardi"
        MERCREDI = "mercredi", "Mercredi"
        JEUDI = "jeudi", "Jeudi"
        VENDREDI = "vendredi", "Vendredi"
        SAMEDI = "samedi", "Samedi"

    id_classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="seances", db_column="id_classe")
    id_matiere = models.ForeignKey(Matiere, on_delete=models.CASCADE, related_name="seances", db_column="id_matiere")
    id_enseignant = models.ForeignKey(Enseignant, on_delete=models.CASCADE, related_name="seances", db_column="id_enseignant")
    id_salle = models.ForeignKey(Salle, on_delete=models.SET_NULL, null=True, blank=True, related_name="seances", db_column="id_salle")
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="seances", db_column="id_annee_scolaire")
    jour_semaine = models.CharField(max_length=10, choices=JourSemaine.choices)
    heure_debut = models.TimeField()
    heure_fin = models.TimeField()

    class Meta:
        db_table = "emploi_du_temps"
        ordering = ["jour_semaine", "heure_debut"]
        constraints = [
            models.UniqueConstraint(fields=["id_classe", "jour_semaine", "heure_debut", "id_annee_scolaire"], name="uq_seance_classe_creneau"),
            models.UniqueConstraint(fields=["id_enseignant", "jour_semaine", "heure_debut", "id_annee_scolaire"], name="uq_seance_enseignant_creneau"),
            models.UniqueConstraint(fields=["id_salle", "jour_semaine", "heure_debut", "id_annee_scolaire"], name="uq_seance_salle_creneau"),
            models.CheckConstraint(condition=models.Q(heure_fin__gt=models.F("heure_debut")), name="chk_seance_horaire"),
        ]


# =====================================================================
# 5. MODULE GESTION FINANCIERE
# =====================================================================

class TypeFrais(models.Model):
    """
    Un frais du catalogue de l'école, sous le nom qu'elle lui donne.

    Le nom est libre : une école coranique, un collège ou une crèche n'appellent
    pas leurs frais de la même façon, et le logiciel n'a pas à le leur imposer.
    Seule la `nature` pèse sur les règles : les frais d'inscription et la
    mensualité sont facturés d'office et font un inscrit, tout le reste se coche.
    """

    class Nature(models.TextChoices):
        INSCRIPTION = "inscription", "Frais d'inscription"
        MENSUALITE = "mensualite", "Mensualité"
        AUTRE = "autre", "Autre frais (à cocher)"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="types_frais", db_column="id_ecole")
    libelle = models.CharField(max_length=100)
    nature = models.CharField(max_length=15, choices=Nature.choices, default=Nature.AUTRE)
    montant_defaut = models.DecimalField(max_digits=12, decimal_places=2)
    frequence = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = "type_frais"
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle


class TarifNiveau(models.Model):
    """
    Montant d'un frais pour un niveau.

    L'inscription en 3ème n'est pas celle de 4ème, et le transport ou la cantine
    peuvent varier eux aussi. Sans ligne pour un niveau, c'est le montant par
    défaut du type de frais qui s'applique : la grille ne stocke que les cases
    renseignées.
    """
    id_niveau = models.ForeignKey(Niveau, on_delete=models.CASCADE, related_name="tarifs",
                                  db_column="id_niveau")
    id_type_frais = models.ForeignKey(TypeFrais, on_delete=models.CASCADE, related_name="tarifs",
                                      db_column="id_type_frais")
    montant = models.DecimalField(max_digits=12, decimal_places=2,
                                  validators=[MinValueValidator(Decimal("0"))])

    class Meta:
        db_table = "tarif_niveau"
        ordering = ["id_niveau", "id_type_frais"]
        constraints = [
            models.UniqueConstraint(fields=["id_niveau", "id_type_frais"], name="uq_tarif_niveau_type"),
        ]


class FraisEleve(models.Model):
    class Statut(models.TextChoices):
        PAYE = "paye", "Payé"
        PARTIEL = "partiel", "Partiel"
        IMPAYE = "impaye", "Impayé"

    id_eleve = models.ForeignKey(Eleve, on_delete=models.CASCADE, related_name="frais", db_column="id_eleve")
    id_type_frais = models.ForeignKey(TypeFrais, on_delete=models.CASCADE, related_name="frais_eleves", db_column="id_type_frais")
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="frais", db_column="id_annee_scolaire")
    montant_du = models.DecimalField(max_digits=12, decimal_places=2)
    # La remise est portée à côté du montant initial, jamais soustraite de
    # `montant_du` : une famille qui conteste veut lire le tarif de départ et
    # la réduction accordée, pas un montant déjà rogné dont plus personne ne
    # sait d'où il vient.
    remise = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                 validators=[MinValueValidator(Decimal("0"))])
    motif_remise = models.CharField(max_length=255, blank=True, null=True)
    echeance = models.DateField(blank=True, null=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.IMPAYE)

    class Meta:
        db_table = "frais_eleve"
        ordering = ["echeance", "id"]

    @property
    def montant_net(self):
        """Ce que la famille doit réellement : montant initial moins la remise."""
        return self.montant_du - (self.remise or Decimal("0"))

    @property
    def montant_paye(self):
        return sum((p.montant for p in self.paiements.all()), Decimal("0"))

    @property
    def solde_restant(self):
        return self.montant_net - self.montant_paye

    def est_en_retard(self, aujourdhui=None):
        """Échéance dépassée et solde non nul — le 🔴 du tableau de bord."""
        if self.echeance is None or self.solde_restant <= 0:
            return False
        from django.utils import timezone as _tz
        return self.echeance < (aujourdhui or _tz.localdate())


class Paiement(models.Model):
    class Methode(models.TextChoices):
        ORANGE_MONEY = "orange_money", "Orange Money"
        WAVE = "wave", "Wave"
        FREE_MONEY = "free_money", "Free Money"
        CARTE_BANCAIRE = "carte_bancaire", "Carte bancaire"
        ESPECES = "especes", "Espèces"

    id_frais_eleve = models.ForeignKey(FraisEleve, on_delete=models.CASCADE, related_name="paiements", db_column="id_frais_eleve")
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    # `default` et non `auto_now_add` : une école encaisse souvent le lundi un
    # règlement reçu le samedi, et le reçu doit porter la date réelle. Sans
    # valeur fournie, on retombe sur « maintenant », comme avant.
    date_paiement = models.DateTimeField(default=timezone.now)
    methode = models.CharField(max_length=20, choices=Methode.choices)
    reference_transaction = models.CharField(max_length=100, blank=True, null=True)
    commentaire = models.TextField(blank=True, null=True)
    id_utilisateur_caissier = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                                  related_name="paiements_encaisses", db_column="id_utilisateur_caissier")

    class Meta:
        db_table = "paiement"
        ordering = ["-date_paiement"]


class Recu(models.Model):
    id_paiement = models.OneToOneField(Paiement, on_delete=models.CASCADE, related_name="recu", db_column="id_paiement")
    numero_recu = models.CharField(max_length=50, unique=True)
    date_emission = models.DateTimeField(auto_now_add=True)
    fichier_pdf_url = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "recu"
        ordering = ["-date_emission"]


class Relance(models.Model):
    # WhatsApp plutôt que le SMS : c'est là que les familles lisent leurs
    # messages, et une relance s'y accompagne du reçu ou de l'état des frais.
    class Canal(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "E-mail"

    class Statut(models.TextChoices):
        ENVOYEE = "envoyee", "Envoyée"
        ECHOUEE = "echouee", "Échouée"

    id_frais_eleve = models.ForeignKey(FraisEleve, on_delete=models.CASCADE, related_name="relances", db_column="id_frais_eleve")
    date_envoi = models.DateTimeField(auto_now_add=True)
    canal = models.CharField(max_length=10, choices=Canal.choices)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ENVOYEE)

    class Meta:
        db_table = "relance"
        ordering = ["-date_envoi"]


class TypeDepense(models.Model):
    """
    Poste de charge de l'école : loyer, salaires, fournitures…

    Le poste ne porte pas son budget : une enveloppe vaut pour une année, et
    celle de l'an dernier n'est pas celle en cours. Le montant vit donc dans
    `Budget`, une ligne par poste et par année, même si l'écran des catégories
    est le seul endroit où on la saisit (voir `TypeDepenseSerializer`).
    """
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="types_depenses", db_column="id_ecole")
    libelle = models.CharField(max_length=100)

    class Meta:
        db_table = "type_depense"
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle


class Depense(models.Model):
    id_type_depense = models.ForeignKey(TypeDepense, on_delete=models.CASCADE, related_name="depenses", db_column="id_type_depense")
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="depenses", db_column="id_ecole")
    motif = models.CharField(max_length=100)
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    date_depense = models.DateField()
    justificatif_url = models.CharField(max_length=255, blank=True, null=True)
    # Qui l'a saisie : l'historique de caisse rattache chaque sortie à quelqu'un.
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="depenses_saisies", db_column="id_utilisateur")

    class Meta:
        db_table = "depense"
        ordering = ["-date_depense"]


class Budget(models.Model):
    id_type_depense = models.ForeignKey(TypeDepense, on_delete=models.CASCADE, related_name="budgets", db_column="id_type_depense")
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="budgets", db_column="id_ecole")
    id_annee_scolaire = models.ForeignKey(AnneeScolaire, on_delete=models.CASCADE, related_name="budgets", db_column="id_annee_scolaire")
    categorie = models.CharField(max_length=100)
    montant_prevu = models.DecimalField(max_digits=12, decimal_places=2)
    montant_realise = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        db_table = "budget"
        ordering = ["categorie"]


class JournalCaisse(models.Model):
    class TypeJournal(models.TextChoices):
        CAISSE = "caisse", "Caisse"
        BANQUE = "banque", "Banque"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="journal", db_column="id_ecole")
    id_paiement = models.ForeignKey(Paiement, on_delete=models.SET_NULL, null=True, blank=True, related_name="mouvements", db_column="id_paiement")
    id_depense = models.ForeignKey(Depense, on_delete=models.SET_NULL, null=True, blank=True, related_name="mouvements", db_column="id_depense")
    type_journal = models.CharField(max_length=10, choices=TypeJournal.choices)
    date_mouvement = models.DateField()
    libelle = models.CharField(max_length=255)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    solde_apres = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="mouvements_caisse", db_column="id_utilisateur")

    class Meta:
        db_table = "journal_caisse"
        ordering = ["-date_mouvement"]


# =====================================================================
# 6. MODULE COMMUNICATIONS
#    Absent du dump SQL initial — ajouté ici, mêmes conventions
#    (id_ecole en tête, db_table explicite, enums en TextChoices).
# =====================================================================

class ClotureCaisse(models.Model):
    """
    Arrêté de caisse d'une journée : ce que le logiciel dit au moment où la
    caissière compte son tiroir.

    C'est une photographie, pas un verrou. Les totaux sont figés à l'instant de
    la clôture ; une opération enregistrée après coup n'est pas refusée, elle
    apparaît en écart — c'est précisément l'information qu'on cherche le
    lendemain quand la caisse ne tombe pas juste.
    """
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE,
                                 related_name="clotures_caisse", db_column="id_ecole")
    date_journee = models.DateField()
    total_encaisse = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_depense = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Ce que le tiroir devrait contenir : les espèces reçues, moins les dépenses.
    especes_attendues = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    commentaire = models.TextField(blank=True, null=True)
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="clotures_caisse", db_column="id_utilisateur")
    date_cloture = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cloture_caisse"
        ordering = ["-date_journee"]
        constraints = [
            models.UniqueConstraint(fields=["id_ecole", "date_journee"],
                                    name="uq_cloture_ecole_journee"),
        ]

    def __str__(self):
        return f"Caisse du {self.date_journee}"


class Communication(models.Model):
    class Type(models.TextChoices):
        ANNONCE = "annonce", "Annonce"
        MESSAGE = "message", "Message"
        CONVOCATION = "convocation", "Convocation"
        ALERTE = "alerte", "Alerte"

    class Canal(models.TextChoices):
        INTERNE = "interne", "Interne"
        SMS = "sms", "SMS"
        EMAIL = "email", "E-mail"

    class Cible(models.TextChoices):
        ECOLE = "ecole", "Toute l'école"
        ROLE = "role", "Un rôle"
        CLASSE = "classe", "Une classe"
        INDIVIDUEL = "individuel", "Destinataires choisis"

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        PROGRAMMEE = "programmee", "Programmée"
        ENVOYEE = "envoyee", "Envoyée"
        ECHOUEE = "echouee", "Échouée"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="communications", db_column="id_ecole")
    id_utilisateur_auteur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                               related_name="communications_envoyees", db_column="id_utilisateur_auteur")
    type_communication = models.CharField(max_length=15, choices=Type.choices, default=Type.ANNONCE)
    canal = models.CharField(max_length=10, choices=Canal.choices, default=Canal.INTERNE)
    cible = models.CharField(max_length=15, choices=Cible.choices, default=Cible.ECOLE)
    # Renseignés selon la cible : une classe précise, ou un rôle (parents, enseignants...).
    id_classe = models.ForeignKey(Classe, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="communications", db_column="id_classe")
    # Les rôles d'une école : le super administrateur n'est le destinataire
    # d'aucune communication d'établissement.
    role_cible = models.CharField(max_length=30, blank=True, null=True,
                                  choices=[c for c in Utilisateur.Role.choices if c[0] != "super_admin"])
    objet = models.CharField(max_length=255)
    contenu = models.TextField()
    statut = models.CharField(max_length=15, choices=Statut.choices, default=Statut.BROUILLON)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_envoi = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "communication"
        ordering = ["-date_creation"]

    def __str__(self):
        return self.objet


class CommunicationDestinataire(models.Model):
    """
    Un destinataire d'une communication, avec l'état d'envoi et de lecture.

    Deux cas, d'où deux FK facultatives : le canal interne vise un compte
    `utilisateur`, tandis que SMS et e-mail visent un `parent_tuteur` — la
    plupart des familles n'ont pas de compte, mais ont un téléphone. `contact`
    fige le numéro ou l'e-mail utilisé au moment de l'envoi, pour que le suivi
    reste exact même si la fiche du parent change ensuite.
    """

    class StatutEnvoi(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        ENVOYE = "envoye", "Envoyé"
        ECHEC = "echec", "Échec"

    id_communication = models.ForeignKey(Communication, on_delete=models.CASCADE,
                                          related_name="destinataires", db_column="id_communication")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, null=True, blank=True,
                                        related_name="communications_recues", db_column="id_utilisateur")
    id_parent = models.ForeignKey(ParentTuteur, on_delete=models.CASCADE, null=True, blank=True,
                                   related_name="communications_recues", db_column="id_parent")
    contact = models.CharField(max_length=150, blank=True, null=True)
    statut_envoi = models.CharField(max_length=12, choices=StatutEnvoi.choices, default=StatutEnvoi.EN_ATTENTE)
    erreur = models.CharField(max_length=255, blank=True, null=True)
    lu = models.BooleanField(default=False)
    date_lecture = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "communication_destinataire"
        ordering = ["id"]
        constraints = [
            # Deux contraintes séparées plutôt qu'une seule sur les deux colonnes :
            # MySQL autorise les doublons de NULL, donc chacune ne dédoublonne
            # que les lignes du cas qui la concerne.
            models.UniqueConstraint(fields=["id_communication", "id_utilisateur"],
                                     name="uq_destinataire_communication_compte"),
            models.UniqueConstraint(fields=["id_communication", "id_parent"],
                                     name="uq_destinataire_communication_parent"),
            models.CheckConstraint(
                condition=models.Q(id_utilisateur__isnull=False) | models.Q(id_parent__isnull=False),
                name="chk_destinataire_cible_renseignee",
            ),
        ]

    def __str__(self):
        return str(self.id_utilisateur or self.id_parent or self.contact or "")


# =====================================================================
# 7. MODULE DOCUMENTS & RAPPORTS
# =====================================================================

class Document(models.Model):
    """
    Document de l'établissement : règlement intérieur, circulaire, note de service.

    Ne porte pas de lien vers un élève : les pièces d'un dossier élève
    (extrait de naissance, certificat de scolarité...) relèvent de
    `DossierAdministratif`, qui vient du schéma validé avec le client. Les
    deux tables se recouvraient ; leurs rôles sont désormais disjoints.
    """

    class Categorie(models.TextChoices):
        ADMINISTRATIF = "administratif", "Administratif"
        PEDAGOGIQUE = "pedagogique", "Pédagogique"
        FINANCIER = "financier", "Financier"
        RH = "rh", "Ressources humaines"
        AUTRE = "autre", "Autre"

    class Visibilite(models.TextChoices):
        ADMIN = "admin", "Administration seule"
        ENSEIGNANTS = "enseignants", "Enseignants"
        PARENTS = "parents", "Parents"
        TOUS = "tous", "Tous les utilisateurs"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="documents", db_column="id_ecole")
    id_utilisateur_depot = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                              related_name="documents_deposes", db_column="id_utilisateur_depot")
    titre = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True, null=True)
    categorie = models.CharField(max_length=15, choices=Categorie.choices, default=Categorie.AUTRE)
    visibilite = models.CharField(max_length=12, choices=Visibilite.choices, default=Visibilite.ADMIN)
    fichier_url = models.CharField(max_length=255)
    type_mime = models.CharField(max_length=100, blank=True, null=True)
    taille_octets = models.PositiveBigIntegerField(blank=True, null=True)
    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document"
        ordering = ["-date_ajout"]

    def __str__(self):
        return self.titre


class Rapport(models.Model):
    """
    Trace d'un export généré (liste d'élèves, état des impayés, journal de caisse...).
    Les données viennent des tables existantes ; cette table garde l'historique
    des générations et l'URL du fichier produit.
    """

    class TypeRapport(models.TextChoices):
        LISTE_ELEVES = "liste_eleves", "Liste des élèves"
        EFFECTIFS = "effectifs", "Effectifs par classe"
        BULLETINS = "bulletins", "Bulletins"
        ABSENCES = "absences", "État des absences"
        ETAT_IMPAYES = "etat_impayes", "État des impayés"
        JOURNAL_CAISSE = "journal_caisse", "Journal de caisse"
        BUDGET = "budget", "Suivi budgétaire"

    class Format(models.TextChoices):
        PDF = "pdf", "PDF"
        XLSX = "xlsx", "Excel"
        CSV = "csv", "CSV"

    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        GENERE = "genere", "Généré"
        ECHOUE = "echoue", "Échoué"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="rapports", db_column="id_ecole")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="rapports_demandes", db_column="id_utilisateur")
    type_rapport = models.CharField(max_length=20, choices=TypeRapport.choices)
    format_export = models.CharField(max_length=5, choices=Format.choices, default=Format.PDF)
    # Filtres de génération (classe, période, année scolaire...) conservés tels quels.
    parametres = models.JSONField(blank=True, null=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    fichier_url = models.CharField(max_length=255, blank=True, null=True)
    erreur = models.CharField(max_length=255, blank=True, null=True)
    date_demande = models.DateTimeField(auto_now_add=True)
    date_generation = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "rapport"
        ordering = ["-date_demande"]

    def __str__(self):
        return f"{self.get_type_rapport_display()} ({self.format_export})"


# =====================================================================
# 8. MODULE PARAMÈTRES
# =====================================================================

class ParametreEcole(models.Model):
    """
    Réglages par école, en clé/valeur pour ne pas figer une colonne par
    besoin futur (barème des notes, devise, seuil de relance, en-tête des reçus...).
    """

    class TypeValeur(models.TextChoices):
        TEXTE = "texte", "Texte"
        NOMBRE = "nombre", "Nombre"
        BOOLEEN = "booleen", "Booléen"
        DATE = "date", "Date"
        JSON = "json", "JSON"

    class Categorie(models.TextChoices):
        GENERAL = "general", "Général"
        SCOLARITE = "scolarite", "Scolarité"
        NOTATION = "notation", "Notation"
        FINANCE = "finance", "Finance"
        NOTIFICATION = "notification", "Notifications"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="parametres", db_column="id_ecole")
    cle = models.CharField(max_length=100)
    libelle = models.CharField(max_length=150)
    valeur = models.TextField(blank=True, null=True)
    type_valeur = models.CharField(max_length=10, choices=TypeValeur.choices, default=TypeValeur.TEXTE)
    categorie = models.CharField(max_length=15, choices=Categorie.choices, default=Categorie.GENERAL)
    description = models.CharField(max_length=255, blank=True, null=True)
    modifiable = models.BooleanField(default=True)
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "parametre_ecole"
        ordering = ["categorie", "cle"]
        constraints = [
            models.UniqueConstraint(fields=["id_ecole", "cle"], name="uq_parametre_ecole_cle")
        ]

    def __str__(self):
        return f"{self.cle} = {self.valeur}"


# =====================================================================
# 9. JOURNAL D'ACTIVITÉ (audit) — distinct du journal de caisse
# =====================================================================

class JournalActivite(models.Model):
    class Action(models.TextChoices):
        CONNEXION = "connexion", "Connexion"
        ECHEC_CONNEXION = "echec_connexion", "Échec de connexion"
        CREATION = "creation", "Création"
        MODIFICATION = "modification", "Modification"
        SUPPRESSION = "suppression", "Suppression"
        RESTAURATION = "restauration", "Restauration"
        EXPORT = "export", "Export"

    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="journal_activite", db_column="id_ecole")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="activites", db_column="id_utilisateur")
    action = models.CharField(max_length=20, choices=Action.choices)
    # Entité touchée, en clair (nom de table) + identifiant, sans FK :
    # l'entrée doit survivre à la suppression de l'objet qu'elle décrit.
    entite = models.CharField(max_length=100, blank=True, null=True)
    id_entite = models.PositiveBigIntegerField(blank=True, null=True)
    description = models.CharField(max_length=255, blank=True, null=True)
    adresse_ip = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True, null=True)
    date_action = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "journal_activite"
        ordering = ["-date_action"]
        indexes = [
            models.Index(fields=["id_ecole", "-date_action"], name="idx_journal_ecole_date"),
        ]

    def __str__(self):
        return f"{self.date_action:%d/%m/%Y %H:%M} — {self.get_action_display()} {self.entite or ''}"


# =====================================================================
# 10. CORBEILLE
# =====================================================================

class Corbeille(models.Model):
    """
    Un élément supprimé, gardé de quoi le remettre en place.

    La suppression reste une vraie suppression : les lignes quittent leurs
    tables, et aucune requête métier n'a à se demander si elle doit ignorer des
    lignes « marquées supprimées ». Ce qui est conservé ici, c'est l'instantané
    pris juste avant — l'élément, tout ce que la suppression a emporté en
    cascade, et les références mises à NULL — que `corbeille.restaurer()`
    réinsère à l'identique, clés primaires comprises.
    """
    id_ecole = models.ForeignKey(Ecole, on_delete=models.CASCADE, related_name="corbeille", db_column="id_ecole")
    id_utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="suppressions", db_column="id_utilisateur")
    # Label du modèle (« gestion_scolaire.Classe ») et table, pour filtrer par module.
    modele = models.CharField(max_length=100)
    entite = models.CharField(max_length=100)
    id_entite = models.PositiveBigIntegerField()
    libelle = models.CharField(max_length=255)
    donnees = models.JSONField()
    # Ce que la suppression a emporté avec l'élément : {« inscriptions » : 30, …}
    resume = models.JSONField(default=dict, blank=True)
    nb_elements = models.PositiveIntegerField(default=1)
    date_suppression = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "corbeille"
        ordering = ["-date_suppression", "-id"]
        indexes = [
            models.Index(fields=["id_ecole", "-date_suppression"], name="idx_corbeille_ecole_date"),
        ]

    def __str__(self):
        return self.libelle
