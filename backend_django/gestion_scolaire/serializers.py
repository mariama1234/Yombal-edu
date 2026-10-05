import re
from decimal import Decimal

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.db import transaction
from . import finances, justificatifs, models, scolarite
from .permissions import ROLES_DIRECTION, peut_lire

Utilisateur = get_user_model()


def prochain_matricule(ecole, annee):
    """
    Matricule suivant pour une école et une année données, au format
    PREFIXE-ANNEE-0001.

    La numérotation est propre à chaque école — un compteur global ferait
    dépendre les matricules d'une école de ceux des autres. Elle repose sur
    le plus grand numéro déjà attribué et non sur un décompte, pour qu'une
    suppression au milieu de la liste ne provoque pas de collision.

    Limite assumée : supprimer le dernier élève inscrit libère son numéro,
    qui sera réattribué. Tenir une séquence persistante coûterait une table
    de plus pour un cas qui ne devrait pas se produire — un départ se
    matérialise par `statut` (radié, transféré, diplômé), pas par une
    suppression de ligne, qui effacerait au passage notes et paiements.
    """
    prefixe = (
        models.ParametreEcole.objects
        .filter(id_ecole=ecole, cle="prefixe_matricule")
        .values_list("valeur", flat=True)
        .first()
    ) or "ELV"

    debut = re.sub(r"\D", "", annee.libelle)[:4] or str(annee.date_debut.year)
    base = f"{prefixe}-{debut}-"

    # select_for_update sérialise les inscriptions simultanées dans la même
    # école, sinon deux requêtes concurrentes calculeraient le même numéro.
    existants = (
        models.Eleve.objects
        .select_for_update()
        .filter(id_ecole=ecole, matricule__startswith=base)
        .values_list("matricule", flat=True)
    )

    dernier = 0
    for matricule in existants:
        suffixe = matricule[len(base):]
        if suffixe.isdigit():
            dernier = max(dernier, int(suffixe))

    return f"{base}{dernier + 1:04d}"


# ---------- Auth / utilisateur ----------

class UtilisateurSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ["id", "id_ecole", "nom", "prenom", "email", "role", "telephone", "statut", "date_creation"]
        read_only_fields = ["id", "date_creation"]


class MonProfilSerializer(serializers.ModelSerializer):
    """
    Ce que chacun corrige sur son propre compte.

    Ni l'e-mail, ni le rôle, ni le statut : l'identifiant de connexion et les
    droits restent entre les mains de la direction — sinon n'importe quel
    compte se donnerait les siens.
    """

    class Meta:
        model = Utilisateur
        fields = ["nom", "prenom", "telephone"]


class ChangementMotDePasseSerializer(serializers.Serializer):
    """
    Changement de son propre mot de passe.

    L'actuel est redemandé : un écran resté ouvert ne doit pas suffire à
    changer le mot de passe de quelqu'un. Le nouveau passe par les
    validateurs de Django (longueur, mot de passe courant, tout en chiffres).
    """
    mot_de_passe_actuel = serializers.CharField()
    nouveau_mot_de_passe = serializers.CharField()

    def validate_mot_de_passe_actuel(self, valeur):
        if not self.context["utilisateur"].check_password(valeur):
            raise serializers.ValidationError("Mot de passe actuel incorrect.")
        return valeur

    def validate_nouveau_mot_de_passe(self, valeur):
        utilisateur = self.context["utilisateur"]
        try:
            validate_password(valeur, utilisateur)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return valeur

    def validate(self, attrs):
        if attrs["mot_de_passe_actuel"] == attrs["nouveau_mot_de_passe"]:
            raise serializers.ValidationError({
                "nouveau_mot_de_passe": "Choisissez un mot de passe différent de l'actuel."})
        return attrs


class UtilisateurCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Utilisateur
        fields = ["id", "id_ecole", "nom", "prenom", "email", "role", "telephone", "password"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = Utilisateur(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UtilisateurAdminSerializer(serializers.ModelSerializer):
    """
    Gestion des comptes par un administrateur.

    `id_ecole` est imposé par la vue, jamais par le client : un admin ne peut
    créer de compte que dans sa propre école. `is_staff` et `is_superuser`
    ne sont pas exposés — ils ne s'accordent pas par l'API.
    """
    # allow_blank : le formulaire de modification renvoie une chaîne vide quand
    # l'utilisateur ne touche pas au mot de passe.
    password = serializers.CharField(write_only=True, min_length=8, required=False,
                                     allow_blank=True,
                                     help_text="Laisser vide pour conserver le mot de passe actuel.")

    class Meta:
        model = Utilisateur
        fields = ["id", "id_ecole", "nom", "prenom", "email", "role", "telephone",
                  "statut", "date_creation", "password"]
        read_only_fields = ["id", "id_ecole", "date_creation"]
        # L'unicité est vérifiée par `validate_email`, sans tenir compte des
        # majuscules et avec un message clair ; le contrôle générique du modèle
        # passait avant lui et renvoyait « utilisateur with this email… ».
        # La contrainte de la base reste le dernier garde-fou.
        extra_kwargs = {"email": {"validators": []}}

    def validate(self, attrs):
        """
        Une école doit toujours garder un administrateur actif : sans cela,
        plus personne ne peut gérer les comptes et l'école est verrouillée.
        Le cas se produit quand l'unique admin se rétrograde ou se désactive.
        """
        # Un directeur ou un responsable administratif a les mêmes droits : il
        # compte comme administrateur.
        if not self.instance or self.instance.role not in ROLES_DIRECTION:
            return attrs

        role = attrs.get("role", self.instance.role)
        statut = attrs.get("statut", self.instance.statut)
        reste_admin_actif = (
            role in ROLES_DIRECTION and statut == Utilisateur.Statut.ACTIF
        )
        if reste_admin_actif:
            return attrs

        autres = Utilisateur.objects.filter(
            id_ecole_id=self.instance.id_ecole_id,
            role__in=ROLES_DIRECTION,
            statut=Utilisateur.Statut.ACTIF,
        ).exclude(pk=self.instance.pk)
        if not autres.exists():
            raise serializers.ValidationError(
                "Cette école n'aurait plus d'administrateur actif. "
                "Nommez d'abord un autre administrateur."
            )
        return attrs

    def validate_role(self, value):
        # Un administrateur d'école ne crée pas de compte de plateforme : un
        # super administrateur rattaché à une école violerait la contrainte, et
        # lui donnerait la main sur toutes les autres.
        if value == Utilisateur.Role.SUPER_ADMIN:
            raise serializers.ValidationError("Ce rôle ne s'attribue pas depuis une école.")
        return value

    def validate_email(self, value):
        """
        L'e-mail sert d'identifiant de connexion : il doit être unique sur
        toute l'instance, pas seulement dans l'école, sinon l'authentification
        serait ambiguë entre deux écoles.
        """
        qs = Utilisateur.objects.filter(email__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Cette adresse e-mail est déjà utilisée.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError(
                {"password": "Un mot de passe est obligatoire à la création."}
            )
        utilisateur = Utilisateur(**validated_data)
        utilisateur.set_password(password)
        utilisateur.save()
        return utilisateur

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for champ, valeur in validated_data.items():
            setattr(instance, champ, valeur)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


# ---------- Cœur ----------

class EcoleSerializer(serializers.ModelSerializer):
    """
    Fiche de l'établissement, modifiable par son administrateur.

    L'abonnement et le statut relèvent de la plateforme, pas du client : sans
    cela, une école pourrait passer seule en plan premium ou se réactiver
    après suspension.
    """
    class Meta:
        model = models.Ecole
        fields = "__all__"
        # Le règlement de l'abonnement aussi : une école ne se déclare pas payée.
        read_only_fields = ["plan_abonnement", "statut", "date_activation", "date_creation",
                            "abonnement_paye", "date_paiement"]


# ---------- Module élèves ----------

class UniciteParEcoleMixin:
    """
    Rejette proprement un doublon sur une contrainte du type
    (id_ecole, champ).

    DRF ne peut pas générer le validateur lui-même : `id_ecole` est en lecture
    seule — il vient du compte connecté, jamais du corps de la requête — donc
    il ne fait pas partie des données validées. Sans ce contrôle, un doublon
    remonte en `IntegrityError` et l'utilisateur reçoit une erreur 500 muette
    là où il attend « ce libellé existe déjà ».

    `champ_unique` nomme la colonne, `message_unicite` le texte affiché.
    """
    champ_unique = None
    message_unicite = "Cette valeur existe déjà pour votre école."

    def validate(self, attrs):
        attrs = super().validate(attrs)
        requete = self.context.get("request")
        if requete is None or self.champ_unique is None:
            return attrs

        valeur = attrs.get(self.champ_unique,
                           getattr(self.instance, self.champ_unique, None))
        if valeur in (None, ""):
            return attrs

        doublons = self.Meta.model.objects.filter(
            id_ecole_id=requete.user.id_ecole_id, **{self.champ_unique: valeur})
        if self.instance is not None:
            doublons = doublons.exclude(pk=self.instance.pk)
        if doublons.exists():
            raise serializers.ValidationError({self.champ_unique: self.message_unicite})
        return attrs


class AnneeScolaireSerializer(UniciteParEcoleMixin, serializers.ModelSerializer):
    champ_unique = "libelle"
    message_unicite = "Une année scolaire porte déjà ce libellé dans votre école."

    # Compté côté serveur : l'écran des années a besoin de savoir si une année
    # porte déjà des classes ou des inscriptions avant qu'on l'archive.
    nombre_classes = serializers.IntegerField(source="classes.count", read_only=True)
    nombre_inscriptions = serializers.IntegerField(source="inscriptions.count", read_only=True)

    class Meta:
        model = models.AnneeScolaire
        fields = "__all__"
        read_only_fields = ["id_ecole"]

    def validate(self, attrs):
        """
        Une année qui se termine avant d'avoir commencé rendrait toutes les
        périodes qu'on y rattache invalides — `PeriodeScolaireSerializer` exige
        qu'elles tiennent dans ces dates.
        """
        # Le `super()` porte le contrôle d'unicité du mixin : sans lui, il
        # serait purement et simplement sauté.
        attrs = super().validate(attrs)

        debut = attrs.get("date_debut", getattr(self.instance, "date_debut", None))
        fin = attrs.get("date_fin", getattr(self.instance, "date_fin", None))
        if debut and fin and fin <= debut:
            raise serializers.ValidationError(
                {"date_fin": "La fin de l'année scolaire doit suivre son début."}
            )
        return attrs


class PeriodeScolaireSerializer(serializers.ModelSerializer):
    annee_libelle = serializers.CharField(source="id_annee_scolaire.libelle", read_only=True)

    class Meta:
        model = models.PeriodeScolaire
        fields = "__all__"

    def validate(self, attrs):
        debut = attrs.get("date_debut", getattr(self.instance, "date_debut", None))
        fin = attrs.get("date_fin", getattr(self.instance, "date_fin", None))
        if debut and fin and fin <= debut:
            raise serializers.ValidationError(
                {"date_fin": "La fin de la période doit suivre son début."}
            )

        annee = attrs.get("id_annee_scolaire", getattr(self.instance, "id_annee_scolaire", None))
        if annee and debut and fin and (debut < annee.date_debut or fin > annee.date_fin):
            raise serializers.ValidationError(
                "La période doit tenir dans les dates de l'année scolaire "
                f"({annee.date_debut:%d/%m/%Y} — {annee.date_fin:%d/%m/%Y})."
            )
        return attrs


class NiveauSerializer(serializers.ModelSerializer):
    # Montant de chaque frais pour ce niveau, par identifiant de type de frais.
    # Un frais absent applique son montant par défaut.
    tarifs = serializers.SerializerMethodField()

    class Meta:
        model = models.Niveau
        fields = "__all__"
        read_only_fields = ["id_ecole"]

    def get_tarifs(self, obj) -> dict:
        return {str(t.id_type_frais_id): finances.montant_json(t.montant) for t in obj.tarifs.all()}

    def validate(self, attrs):
        age_min = attrs.get("age_min", getattr(self.instance, "age_min", None))
        age_max = attrs.get("age_max", getattr(self.instance, "age_max", None))
        if age_min is not None and age_max is not None and age_min > age_max:
            raise serializers.ValidationError(
                {"age_max": "L'âge maximal doit être supérieur ou égal à l'âge minimal."})
        return attrs

    def create(self, validated_data):
        """Un niveau connu de la grille naît avec sa fourchette d'âge, modifiable ensuite."""
        if validated_data.get("age_min") is None and validated_data.get("age_max") is None:
            fourchette = scolarite.fourchette_par_defaut(validated_data.get("libelle"))
            if fourchette:
                validated_data["age_min"], validated_data["age_max"] = fourchette
        return super().create(validated_data)


class ClasseSerializer(serializers.ModelSerializer):
    niveau_libelle = serializers.CharField(source="id_niveau.libelle", read_only=True, allow_null=True)
    enseignant_nom = serializers.SerializerMethodField()

    class Meta:
        model = models.Classe
        fields = "__all__"
        read_only_fields = ["id_ecole"]

    def get_enseignant_nom(self, obj) -> str | None:
        if obj.id_enseignant_principal:
            return f"{obj.id_enseignant_principal.prenom if hasattr(obj.id_enseignant_principal,'prenom') else ''} {obj.id_enseignant_principal.nom if hasattr(obj.id_enseignant_principal,'nom') else obj.id_enseignant_principal.matricule}"
        return None


class CreationAccesSerializer(serializers.Serializer):
    """
    Entrée de « Créer son accès » : le compte de connexion d'une fiche.

    Tout est facultatif : le nom et l'e-mail sont pris sur la fiche quand elle
    les porte, et le mot de passe est tiré au sort puis affiché une fois à
    l'écran — la direction le transmet à la famille ou à l'enseignant.
    """
    email = serializers.EmailField(max_length=150, required=False, allow_blank=True)
    nom = serializers.CharField(max_length=100, required=False, allow_blank=True)
    prenom = serializers.CharField(max_length=100, required=False, allow_blank=True)
    mot_de_passe = serializers.CharField(min_length=8, required=False, allow_blank=True,
                                         help_text="Vide : un mot de passe provisoire est tiré au sort.")
    telephone = serializers.CharField(max_length=30, required=False, allow_blank=True)


class AccesFicheMixin(serializers.ModelSerializer):
    """Expose l'accès de la fiche : l'écran propose de le créer, ou dit qu'il existe."""
    a_un_acces = serializers.SerializerMethodField()
    acces_email = serializers.SerializerMethodField()

    def get_a_un_acces(self, obj):
        return obj.id_utilisateur_id is not None

    def get_acces_email(self, obj):
        return obj.id_utilisateur.email if obj.id_utilisateur_id else None


class ParentTuteurSerializer(AccesFicheMixin):
    class Meta:
        model = models.ParentTuteur
        fields = "__all__"
        read_only_fields = ["id_ecole", "id_utilisateur"]


class EleveParentSerializer(serializers.ModelSerializer):
    eleve_nom = serializers.SerializerMethodField()
    parent_nom = serializers.SerializerMethodField()

    class Meta:
        model = models.EleveParent
        fields = "__all__"

    def get_eleve_nom(self, obj) -> str:
        return f"{obj.id_eleve.prenom} {obj.id_eleve.nom}"

    def get_parent_nom(self, obj) -> str:
        return f"{obj.id_parent.prenom} {obj.id_parent.nom}"

    def validate(self, attrs):
        eleve = attrs.get("id_eleve", getattr(self.instance, "id_eleve", None))
        parent = attrs.get("id_parent", getattr(self.instance, "id_parent", None))

        if eleve and parent and eleve.id_ecole_id != parent.id_ecole_id:
            raise serializers.ValidationError(
                "L'élève et le parent doivent appartenir à la même école."
            )

        # La table a une contrainte d'unicité sur le couple : on la contrôle
        # ici pour renvoyer un 400 plutôt qu'une erreur d'intégrité en 500.
        if eleve and parent:
            doublons = models.EleveParent.objects.filter(id_eleve=eleve, id_parent=parent)
            if self.instance:
                doublons = doublons.exclude(pk=self.instance.pk)
            if doublons.exists():
                raise serializers.ValidationError("Ce parent est déjà rattaché à cet élève.")
        return attrs


class DossierAdministratifSerializer(serializers.ModelSerializer):
    eleve_nom = serializers.SerializerMethodField()

    class Meta:
        model = models.DossierAdministratif
        fields = "__all__"
        read_only_fields = ["date_ajout"]

    def get_eleve_nom(self, obj) -> str:
        return f"{obj.id_eleve.prenom} {obj.id_eleve.nom}"


class HistoriqueScolaireSerializer(serializers.ModelSerializer):
    eleve_nom = serializers.SerializerMethodField()
    evenement_libelle = serializers.CharField(source="get_type_evenement_display", read_only=True)
    ancienne_classe_nom = serializers.CharField(source="id_ancienne_classe.nom", read_only=True)
    nouvelle_classe_nom = serializers.CharField(source="id_nouvelle_classe.nom", read_only=True)

    class Meta:
        model = models.HistoriqueScolaire
        fields = "__all__"

    def get_eleve_nom(self, obj) -> str:
        return f"{obj.id_eleve.prenom} {obj.id_eleve.nom}"


class EleveSerializer(UniciteParEcoleMixin, serializers.ModelSerializer):
    champ_unique = "matricule"
    message_unicite = "Un élève porte déjà ce matricule dans votre école."

    # État de l'inscription sur l'année consultée, à ne pas confondre avec
    # `statut` : celui-ci dit si l'élève fait partie de l'école (actif, radié,
    # transféré), celui-là où en est sa scolarité de l'année. Un élève
    # « actif » peut n'être que pré-inscrit, et c'est précisément la nuance
    # qu'une liste qui n'affiche que « actif » faisait disparaître.
    inscription_statut = serializers.SerializerMethodField()
    inscription_libelle = serializers.SerializerMethodField()
    inscription_autorise_en_classe = serializers.SerializerMethodField()
    classe = serializers.SerializerMethodField()
    # « Ba » ne désigne personne dans une école qui en compte quinze : les
    # listes déroulantes affichent le nom complet, et le matricule tranche
    # entre deux homonymes.
    nom_complet = serializers.SerializerMethodField()

    class Meta:
        model = models.Eleve
        fields = "__all__"
        # Le cas social touche à ce que doit la famille : il passe par
        # l'action dédiée (droit d'écrire les frais), pas par la fiche.
        read_only_fields = ["id_ecole", "cas_social", "taux_reduction_sociale", "motif_cas_social"]

    def validate_numero_registre(self, valeur):
        return (valeur or "").strip() or None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        requete = self.context.get("request")
        numero = attrs.get("numero_registre")
        if numero and requete is not None:
            doublons = models.Eleve.objects.filter(id_ecole_id=requete.user.id_ecole_id,
                                                   numero_registre=numero)
            if self.instance is not None:
                doublons = doublons.exclude(pk=self.instance.pk)
            if doublons.exists():
                raise serializers.ValidationError(
                    {"numero_registre": "Un élève porte déjà ce numéro de registre dans votre école."})
        return attrs

    def get_nom_complet(self, obj):
        return f"{obj.prenom} {obj.nom}".strip()

    def _inscription(self, obj):
        """
        Inscription de l'élève sur l'année demandée dans l'URL.

        Sans année précisée — une recherche globale, par exemple — on prend la
        plus récente : afficher l'état d'une année qu'on n'a pas demandée
        serait pire que de n'en afficher aucun.
        """
        if not hasattr(obj, "_cache_inscription"):
            requete = self.context.get("request")
            annee = requete.query_params.get("id_annee_scolaire") if requete else None
            qs = obj.inscriptions.select_related("id_classe")
            if annee:
                qs = qs.filter(id_annee_scolaire=annee)
            obj._cache_inscription = qs.order_by(
                "-id_annee_scolaire__date_debut", "-date_inscription").first()
        return obj._cache_inscription

    def get_inscription_statut(self, obj) -> str | None:
        inscription = self._inscription(obj)
        return inscription.statut if inscription else None

    def get_inscription_libelle(self, obj) -> str | None:
        inscription = self._inscription(obj)
        return inscription.get_statut_display() if inscription else None

    def get_classe(self, obj) -> str | None:
        inscription = self._inscription(obj)
        return inscription.id_classe.nom if inscription else None

    def get_inscription_autorise_en_classe(self, obj) -> bool:
        inscription = self._inscription(obj)
        return bool(inscription and inscription.autorise_en_classe)


class EleveInscriptionSerializer(serializers.Serializer):
    """
    Serializer composite pour le formulaire "Inscrire un élève" :
    crée en une seule opération l'élève + son inscription + (optionnel) son parent/tuteur.
    Reflète exactement les 3 sections du formulaire Angular.
    """
    # Section "Informations de l'élève"
    id_ecole = serializers.PrimaryKeyRelatedField(queryset=models.Ecole.objects.all())
    nom = serializers.CharField(max_length=100)
    prenom = serializers.CharField(max_length=100)
    date_naissance = serializers.DateField(required=False, allow_null=True)
    sexe = serializers.ChoiceField(choices=models.Eleve.Sexe.choices, required=False, allow_null=True)
    adresse = serializers.CharField(max_length=255, required=False, allow_blank=True)
    photo_url = serializers.CharField(max_length=255, required=False, allow_blank=True)
    numero_registre = serializers.CharField(max_length=50, required=False, allow_blank=True,
                                            allow_null=True)

    # Section "Scolarité"
    id_classe = serializers.PrimaryKeyRelatedField(queryset=models.Classe.objects.all())
    id_annee_scolaire = serializers.PrimaryKeyRelatedField(queryset=models.AnneeScolaire.objects.all())
    date_inscription = serializers.DateField()

    # Section "Parent / Tuteur" (optionnelle)
    parent_nom_complet = serializers.CharField(max_length=200, required=False, allow_blank=True)
    parent_telephone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    parent_email = serializers.EmailField(required=False, allow_blank=True)
    parent_lien = serializers.ChoiceField(choices=models.EleveParent.LienParente.choices, required=False)
    parent_adresse = serializers.CharField(max_length=255, required=False, allow_blank=True)

    # Âge hors de la fourchette du niveau : la personne a vu l'avertissement
    # et confirme. Sans effet quand l'école bloque.
    derogation_age = serializers.BooleanField(required=False, default=False)

    # Section "Frais" : inscription et mensualités sont émises d'office ; ces
    # types-là s'y ajoutent (transport, demi-pension, fournitures…).
    frais_optionnels = serializers.PrimaryKeyRelatedField(
        queryset=models.TypeFrais.objects.all(), many=True, required=False)

    def validate(self, attrs):
        """La classe et l'année visées doivent relever de l'école de l'élève."""
        ecole = attrs["id_ecole"]
        classe = attrs["id_classe"]
        annee = attrs["id_annee_scolaire"]

        if classe.id_ecole_id != ecole.pk:
            raise serializers.ValidationError(
                {"id_classe": "Cette classe n'appartient pas à votre école."}
            )
        if annee.id_ecole_id != ecole.pk:
            raise serializers.ValidationError(
                {"id_annee_scolaire": "Cette année scolaire n'appartient pas à votre école."}
            )
        if classe.id_annee_scolaire_id != annee.pk:
            raise serializers.ValidationError(
                {"id_classe": "Cette classe relève d'une autre année scolaire."}
            )
        if any(t.id_ecole_id != ecole.pk for t in attrs.get("frais_optionnels", [])):
            raise serializers.ValidationError(
                {"frais_optionnels": "Ce type de frais n'appartient pas à votre école."}
            )

        attrs["numero_registre"] = (attrs.get("numero_registre") or "").strip() or None
        if (attrs["numero_registre"] and models.Eleve.objects
                .filter(id_ecole=ecole, numero_registre=attrs["numero_registre"]).exists()):
            raise serializers.ValidationError(
                {"numero_registre": "Un élève porte déjà ce numéro de registre dans votre école."})

        # `ErreurAge` traverse `is_valid()` et devient une réponse 400.
        attrs["_derogation_age"] = scolarite.exiger_age_conforme(
            ecole, classe, attrs.get("date_naissance"), attrs.get("prenom", ""),
            derogation=attrs.get("derogation_age", False))
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        matricule = prochain_matricule(
            validated_data["id_ecole"], validated_data["id_annee_scolaire"]
        )

        eleve = models.Eleve.objects.create(
            id_ecole=validated_data["id_ecole"],
            matricule=matricule,
            nom=validated_data["nom"],
            prenom=validated_data["prenom"],
            date_naissance=validated_data.get("date_naissance"),
            sexe=validated_data.get("sexe"),
            adresse=validated_data.get("adresse", ""),
            photo_url=validated_data.get("photo_url", ""),
            numero_registre=validated_data.get("numero_registre"),
            date_premiere_inscription=validated_data["date_inscription"],
        )

        models.Inscription.objects.create(
            id_eleve=eleve,
            id_classe=validated_data["id_classe"],
            id_annee_scolaire=validated_data["id_annee_scolaire"],
            date_inscription=validated_data["date_inscription"],
        )
        if validated_data.get("_derogation_age"):
            requete = self.context.get("request")
            scolarite.tracer_derogation_age(
                eleve, validated_data["id_classe"], validated_data["date_inscription"],
                validated_data["_derogation_age"], getattr(requete, "user", None))

        # C'est cette liste que la famille règle ensuite à la caisse.
        finances.emettre_frais_inscription(
            eleve, validated_data["id_annee_scolaire"], validated_data["date_inscription"],
            validated_data.get("frais_optionnels", []), classe=validated_data["id_classe"],
        )

        if validated_data.get("parent_nom_complet"):
            nom_complet = validated_data["parent_nom_complet"].split(" ", 1)
            parent = models.ParentTuteur.objects.create(
                id_ecole=validated_data["id_ecole"],
                nom=nom_complet[-1],
                prenom=nom_complet[0] if len(nom_complet) > 1 else "",
                telephone=validated_data.get("parent_telephone", ""),
                email=validated_data.get("parent_email", ""),
                adresse=validated_data.get("parent_adresse") or None,
            )
            models.EleveParent.objects.create(
                id_eleve=eleve,
                id_parent=parent,
                lien_parente=validated_data.get("parent_lien", models.EleveParent.LienParente.AUTRE),
                contact_principal=True,
            )

        return eleve

    def to_representation(self, instance):
        return EleveSerializer(instance).data


class InscriptionSerializer(serializers.ModelSerializer):
    derogation_age = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = models.Inscription
        fields = "__all__"

    def validate(self, attrs):
        """Même contrôle d'âge qu'à l'inscription par le formulaire, à la création."""
        attrs = super().validate(attrs)
        derogation = attrs.pop("derogation_age", False)
        if self.instance is None and attrs.get("id_classe") and attrs.get("id_eleve"):
            eleve, classe = attrs["id_eleve"], attrs["id_classe"]
            attrs["_derogation_age"] = scolarite.exiger_age_conforme(
                classe.id_ecole, classe, eleve.date_naissance, eleve.prenom,
                derogation=derogation)
        return attrs

    def create(self, validated_data):
        message = validated_data.pop("_derogation_age", None)
        inscription = super().create(validated_data)
        if message:
            requete = self.context.get("request")
            scolarite.tracer_derogation_age(inscription.id_eleve, inscription.id_classe,
                                            inscription.date_inscription, message,
                                            getattr(requete, "user", None))
        return inscription


# ---------- Mouvements de l'élève (changement de classe, radiation,
#            transfert, réinscription) ----------
#
# Ces quatre entrées ne portent que les champs du geste : l'élève vient de
# l'URL et l'école du compte connecté, jamais du corps de la requête.

class ChangementClasseSerializer(serializers.Serializer):
    """Entrée de POST /api/eleves/{id}/changer-classe/."""
    id_classe = serializers.PrimaryKeyRelatedField(queryset=models.Classe.objects.all())
    date_effet = serializers.DateField(required=False, allow_null=True)
    motif = serializers.CharField(max_length=255, required=False, allow_blank=True)
    commentaire = serializers.CharField(required=False, allow_blank=True)


class RadiationSerializer(serializers.Serializer):
    """
    Entrée de POST /api/eleves/{id}/radier/.

    Le motif est obligatoire ici comme dans `scolarite.radier()` : une
    radiation sans motif écrit devient indéfendable dès qu'on la conteste.
    """
    date_effet = serializers.DateField(required=False, allow_null=True)
    motif = serializers.CharField(max_length=255)
    commentaire = serializers.CharField(required=False, allow_blank=True)


class AutorisationClasseSerializer(serializers.Serializer):
    """Entrée de POST /api/eleves/{id}/autoriser-en-classe/ : le motif reste au dossier."""
    motif = serializers.CharField(max_length=255, min_length=3)


class RetraitAutorisationSerializer(serializers.Serializer):
    """Entrée (vide) de POST /api/eleves/{id}/retirer-autorisation/."""


class ClotureCaisseSerializer(serializers.Serializer):
    """Entrée de POST /api/caisse/cloturer/ : la journée arrêtée, et pourquoi."""
    date = serializers.DateField(required=False, allow_null=True)
    commentaire = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class AjoutFraisSerializer(serializers.Serializer):
    """Entrée de POST /api/eleves/{id}/ajouter-frais/."""
    frais = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    date_debut = serializers.DateField(required=False, allow_null=True)
    id_annee_scolaire = serializers.IntegerField(required=False, allow_null=True)


class TransfertSerializer(serializers.Serializer):
    """Entrée de POST /api/eleves/{id}/transferer/."""
    etablissement = serializers.CharField(max_length=200)
    date_effet = serializers.DateField(required=False, allow_null=True)
    commentaire = serializers.CharField(required=False, allow_blank=True)


class ReinscriptionEleveSerializer(serializers.Serializer):
    """
    Entrée de POST /api/eleves/{id}/reinscrire/ — un élève, une classe.

    Distinct de `ReinscriptionSerializer`, qui fait monter une promotion
    entière : ici on ne donne que la classe d'arrivée, l'année s'en déduit.
    Demander l'année en plus laisserait saisir un couple incohérent.
    """
    id_classe = serializers.PrimaryKeyRelatedField(queryset=models.Classe.objects.all())
    date_inscription = serializers.DateField(required=False, allow_null=True)
    # Frais au choix pour la nouvelle année. Absent : ceux de l'an passé sont
    # reconduits ; liste vide : aucun.
    frais_optionnels = serializers.PrimaryKeyRelatedField(
        queryset=models.TypeFrais.objects.all(), many=True, required=False)
    derogation_age = serializers.BooleanField(required=False, default=False)


class ReinscriptionSerializer(serializers.Serializer):
    """
    Entrée de POST /api/inscriptions/reinscrire/.

    Même contrat que la génération des frais : `valider` à false renvoie
    l'aperçu, à true écrit les inscriptions.
    """
    id_annee_source = serializers.PrimaryKeyRelatedField(queryset=models.AnneeScolaire.objects.all())
    id_annee_cible = serializers.PrimaryKeyRelatedField(queryset=models.AnneeScolaire.objects.all())
    id_classe_cible = serializers.PrimaryKeyRelatedField(queryset=models.Classe.objects.all())
    id_classe_source = serializers.PrimaryKeyRelatedField(queryset=models.Classe.objects.all(),
                                                          required=False, allow_null=True)
    date_inscription = serializers.DateField(required=False, allow_null=True)
    # Élèves cochés à l'aperçu. Absent : toute la classe de départ.
    eleves = serializers.ListField(child=serializers.IntegerField(), required=False,
                                   allow_null=True)
    valider = serializers.BooleanField(default=False)
    derogation_age = serializers.BooleanField(required=False, default=False)


class CasSocialSerializer(serializers.Serializer):
    """Entrée de POST /api/eleves/{id}/cas-social/."""
    cas_social = serializers.BooleanField()
    taux_reduction_sociale = serializers.DecimalField(max_digits=5, decimal_places=2,
                                                      required=False, allow_null=True)
    motif_cas_social = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                             allow_null=True)


class FicheSanitaireSerializer(serializers.ModelSerializer):
    maj_par = serializers.SerializerMethodField()

    class Meta:
        model = models.FicheSanitaire
        exclude = ["id", "id_eleve"]
        read_only_fields = ["date_maj", "id_utilisateur_maj"]

    def get_maj_par(self, obj) -> str | None:
        u = obj.id_utilisateur_maj
        return f"{u.prenom} {u.nom}".strip() if u else None


# ---------- Module pédagogique ----------

class EnseignantSerializer(UniciteParEcoleMixin, AccesFicheMixin):
    champ_unique = "matricule"
    message_unicite = "Un enseignant porte déjà ce matricule dans votre école."

    # Un enseignant n'a ni nom ni prénom en propre : ils vivent sur son compte.
    nom = serializers.SerializerMethodField()
    prenom = serializers.SerializerMethodField()
    nom_complet = serializers.SerializerMethodField()

    class Meta:
        model = models.Enseignant
        fields = "__all__"
        read_only_fields = ["id_ecole", "id_utilisateur"]

    def get_nom(self, obj):
        return obj.id_utilisateur.nom if obj.id_utilisateur_id else None

    def get_prenom(self, obj):
        return obj.id_utilisateur.prenom if obj.id_utilisateur_id else None

    def get_nom_complet(self, obj):
        if not obj.id_utilisateur_id:
            return obj.matricule
        return f"{obj.id_utilisateur.prenom} {obj.id_utilisateur.nom}".strip()


class MatiereSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Matiere
        fields = "__all__"
        read_only_fields = ["id_ecole"]


class EvaluationSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Evaluation
        fields = "__all__"


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Note
        fields = "__all__"
        read_only_fields = ["id_utilisateur"]

    def create(self, validated_data):
        validated_data["id_utilisateur"] = self.context["request"].user
        return super().create(validated_data)


class BulletinSerializer(serializers.ModelSerializer):
    eleve_nom = serializers.SerializerMethodField()
    classe_nom = serializers.CharField(source="id_classe.nom", read_only=True)
    periode_libelle = serializers.CharField(source="id_periode.libelle", read_only=True)

    class Meta:
        model = models.Bulletin
        fields = "__all__"

    def get_eleve_nom(self, obj) -> str:
        return f"{obj.id_eleve.prenom} {obj.id_eleve.nom}"



class LigneSaisieNoteSerializer(serializers.Serializer):
    """Une case de la grille de saisie."""
    id_eleve = serializers.IntegerField()
    # `allow_null` : effacer une note est un geste légitime (élève absent,
    # saisie erronée). Sans lui, une note fausse serait indélébile.
    valeur = serializers.DecimalField(max_digits=5, decimal_places=2,
                                      required=False, allow_null=True)
    appreciation = serializers.CharField(max_length=255, required=False,
                                         allow_blank=True, allow_null=True)


class SaisieNotesSerializer(serializers.Serializer):
    """
    Entrée de POST /api/evaluations/{id}/notes/.

    Toute la grille part en une requête : le CRUD imposait un aller-retour par
    élève, soit soixante requêtes pour un devoir de 6ème.
    """
    notes = LigneSaisieNoteSerializer(many=True, allow_empty=False)


class GenerationBulletinsSerializer(serializers.Serializer):
    """Entrée de POST /api/bulletins/generer/."""
    id_classe = serializers.PrimaryKeyRelatedField(queryset=models.Classe.objects.all())
    id_periode = serializers.PrimaryKeyRelatedField(queryset=models.PeriodeScolaire.objects.all())
    valider = serializers.BooleanField(default=False)


class AbsenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Absence
        fields = "__all__"


# ---------- Emploi du temps ----------

class SalleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Salle
        fields = "__all__"
        read_only_fields = ["id_ecole"]


class EmploiDuTempsSerializer(serializers.ModelSerializer):
    matiere_nom = serializers.CharField(source="id_matiere.nom", read_only=True)
    enseignant_matricule = serializers.CharField(source="id_enseignant.matricule", read_only=True)
    salle_nom = serializers.CharField(source="id_salle.nom", read_only=True)

    class Meta:
        model = models.EmploiDuTemps
        fields = "__all__"

    def validate(self, attrs):
        """
        Sans ce contrôle, la contrainte `chk_seance_horaire` remonte en erreur
        serveur : la base refuse l'insertion, mais après le serializer.
        """
        debut = attrs.get("heure_debut", getattr(self.instance, "heure_debut", None))
        fin = attrs.get("heure_fin", getattr(self.instance, "heure_fin", None))
        if debut and fin and fin <= debut:
            raise serializers.ValidationError(
                {"heure_fin": "La fin du cours doit suivre son début."}
            )
        return attrs


# ---------- Module financier ----------

class TypeFraisSerializer(UniciteParEcoleMixin, serializers.ModelSerializer):
    """
    Un frais du catalogue, sous le nom que l'école lui donne.

    Deux garde-fous : deux frais ne portent pas le même nom dans une école, et
    il n'y a qu'un seul type de nature « inscription » et un seul de nature
    « mensualité » — ce sont eux que l'inscription facture d'office.
    """
    champ_unique = "libelle"
    message_unicite = "Votre établissement a déjà un frais portant ce nom."

    nature_libelle = serializers.CharField(source="get_nature_display", read_only=True)

    class Meta:
        model = models.TypeFrais
        fields = "__all__"
        read_only_fields = ["id_ecole"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        nature = attrs.get("nature", getattr(self.instance, "nature", models.TypeFrais.Nature.AUTRE))
        requete = self.context.get("request")
        if requete is not None and nature in finances.TYPES_D_OFFICE:
            doublon = (models.TypeFrais.objects
                       .filter(id_ecole_id=requete.user.id_ecole_id, nature=nature)
                       .exclude(pk=getattr(self.instance, "pk", None)).first())
            if doublon is not None:
                raise serializers.ValidationError({
                    "nature": f"« {doublon.libelle} » a déjà cette nature : modifiez-le "
                              "plutôt que d'en créer un second.",
                })
        return attrs


class FraisEleveSerializer(serializers.ModelSerializer):
    montant_net = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    montant_paye = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    solde_restant = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    eleve_nom = serializers.CharField(source="id_eleve.nom", read_only=True)
    eleve_prenom = serializers.CharField(source="id_eleve.prenom", read_only=True)
    type_frais = serializers.CharField(source="id_type_frais.libelle",
                                       read_only=True)
    en_retard = serializers.SerializerMethodField()

    class Meta:
        model = models.FraisEleve
        fields = "__all__"

    def get_en_retard(self, obj) -> bool:
        return obj.est_en_retard()

    def validate(self, attrs):
        """
        Une remise ne peut ni être négative, ni dépasser le tarif, ni descendre
        sous ce que la famille a déjà versé — sinon le solde passerait au
        négatif et la caisse devrait de l'argent à l'élève.
        """
        montant = attrs.get("montant_du",
                            getattr(self.instance, "montant_du", None))
        remise = attrs.get("remise", getattr(self.instance, "remise", None) or 0)
        if montant is not None and remise is not None and remise > montant:
            raise serializers.ValidationError(
                {"remise": "La remise ne peut pas dépasser le montant du frais."}
            )
        if self.instance is not None and montant is not None and remise is not None:
            deja = self.instance.montant_paye
            if montant - remise < deja:
                raise serializers.ValidationError({
                    "remise": f"{deja} F ont déjà été encaissés sur ce frais : "
                              "le montant net ne peut pas descendre en dessous."
                })
        return attrs

    def update(self, instance, validated_data):
        """
        Une remise accordée après coup peut solder le frais sans qu'un centime
        de plus soit versé : le statut doit suivre, sans quoi la ligne resterait
        « impayée » dans les listes et les relances.
        """
        frais = super().update(instance, validated_data)
        finances.recalculer_statut(frais)
        # Une remise peut solder les droits d'inscription sans versement : la
        # pré-inscription doit alors se confirmer comme après un paiement.
        finances.confirmer_inscription(frais.id_eleve_id, frais.id_annee_scolaire_id)
        return frais


class PaiementSerializer(serializers.ModelSerializer):
    # Le reçu est créé dans la foulée de l'encaissement : le renvoyer ici évite
    # à l'écran caissier une seconde requête pour proposer l'impression.
    # Champs calculés plutôt que `source="recu.…"` : un paiement importé sans
    # reçu ferait alors échouer toute la sérialisation de la liste.
    id_recu = serializers.SerializerMethodField()
    numero_recu = serializers.SerializerMethodField()
    eleve_nom = serializers.SerializerMethodField()

    class Meta:
        model = models.Paiement
        fields = "__all__"
        read_only_fields = ["id_utilisateur_caissier"]
        extra_kwargs = {
            # Une école encaisse souvent le lundi un règlement reçu le samedi :
            # la date doit pouvoir être saisie. Omise, elle vaut « maintenant ».
            "date_paiement": {"required": False},
        }

    @staticmethod
    def _recu(obj):
        # Le ViewSet fait un select_related("recu") : pas de requête en plus.
        try:
            return obj.recu
        except ObjectDoesNotExist:
            return None

    def get_id_recu(self, obj) -> int | None:
        recu = self._recu(obj)
        return recu.pk if recu else None

    def get_numero_recu(self, obj) -> str | None:
        recu = self._recu(obj)
        return recu.numero_recu if recu else None

    def get_eleve_nom(self, obj) -> str:
        eleve = obj.id_frais_eleve.id_eleve
        return f"{eleve.prenom} {eleve.nom}"

    def validate(self, attrs):
        """
        Deux garde-fous de caisse. Un montant négatif passerait au crédit du
        journal et fausserait le solde ; un encaissement supérieur au dû
        solderait un frais pour un montant que l'école n'a pas reçu.
        """
        if self.instance is not None:
            return attrs

        montant = attrs.get("montant")
        frais = attrs.get("id_frais_eleve")
        if montant is not None and montant <= 0:
            raise serializers.ValidationError(
                {"montant": "Le montant encaissé doit être strictement positif."}
            )
        if frais is not None and montant is not None and montant > frais.solde_restant:
            raise serializers.ValidationError({
                "montant": "Montant supérieur au solde restant "
                           f"({frais.solde_restant} sur {frais.montant_du} dus)."
            })
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """
        Un encaissement entraîne trois écritures — statut du frais, reçu,
        mouvement de caisse — regroupées dans `finances.enregistrer_paiement`
        pour que le parcours caissier et un éventuel import de paiements
        produisent exactement les mêmes conséquences.
        """
        caissier = self.context["request"].user
        validated_data["id_utilisateur_caissier"] = caissier
        paiement = super().create(validated_data)
        finances.enregistrer_paiement(paiement, utilisateur=caissier)
        return paiement


class CoefficientMatiereSerializer(serializers.ModelSerializer):
    matiere_nom = serializers.CharField(source="id_matiere.nom", read_only=True)
    classe_nom = serializers.CharField(source="id_classe.nom", read_only=True)

    class Meta:
        model = models.CoefficientMatiere
        fields = "__all__"

    def validate_coefficient(self, value):
        if value <= 0:
            raise serializers.ValidationError("Le coefficient doit être strictement positif.")
        return value

    def validate(self, attrs):
        matiere = attrs.get("id_matiere", getattr(self.instance, "id_matiere", None))
        classe = attrs.get("id_classe", getattr(self.instance, "id_classe", None))
        annee = attrs.get("id_annee_scolaire", getattr(self.instance, "id_annee_scolaire", None))

        if matiere and classe and matiere.id_ecole_id != classe.id_ecole_id:
            raise serializers.ValidationError(
                "La matière et la classe doivent appartenir à la même école."
            )
        if classe and annee and classe.id_annee_scolaire_id != annee.pk:
            raise serializers.ValidationError(
                {"id_annee_scolaire": "Cette classe relève d'une autre année scolaire."}
            )

        if matiere and classe and annee:
            doublons = models.CoefficientMatiere.objects.filter(
                id_matiere=matiere, id_classe=classe, id_annee_scolaire=annee
            )
            if self.instance:
                doublons = doublons.exclude(pk=self.instance.pk)
            if doublons.exists():
                raise serializers.ValidationError(
                    "Un coefficient est déjà défini pour cette matière dans cette classe."
                )
        return attrs


class AffectationEnseignantSerializer(serializers.ModelSerializer):
    enseignant_matricule = serializers.CharField(source="id_enseignant.matricule", read_only=True)
    matiere_nom = serializers.CharField(source="id_matiere.nom", read_only=True)
    classe_nom = serializers.CharField(source="id_classe.nom", read_only=True)

    class Meta:
        model = models.AffectationEnseignant
        fields = "__all__"

    def validate(self, attrs):
        enseignant = attrs.get("id_enseignant", getattr(self.instance, "id_enseignant", None))
        classe = attrs.get("id_classe", getattr(self.instance, "id_classe", None))
        matiere = attrs.get("id_matiere", getattr(self.instance, "id_matiere", None))
        annee = attrs.get("id_annee_scolaire", getattr(self.instance, "id_annee_scolaire", None))

        ecoles = {o.id_ecole_id for o in (enseignant, classe, matiere) if o is not None}
        if len(ecoles) > 1:
            raise serializers.ValidationError(
                "Enseignant, matière et classe doivent appartenir à la même école."
            )
        if classe and annee and classe.id_annee_scolaire_id != annee.pk:
            raise serializers.ValidationError(
                {"id_annee_scolaire": "Cette classe relève d'une autre année scolaire."}
            )

        # Pas de contrainte d'unicité en base sur ce quadruplet : on évite au
        # moins de créer deux fois la même affectation.
        if enseignant and matiere and classe and annee:
            doublons = models.AffectationEnseignant.objects.filter(
                id_enseignant=enseignant, id_matiere=matiere,
                id_classe=classe, id_annee_scolaire=annee,
            )
            if self.instance:
                doublons = doublons.exclude(pk=self.instance.pk)
            if doublons.exists():
                raise serializers.ValidationError("Cette affectation existe déjà.")
        return attrs


class ConseilClasseSerializer(serializers.ModelSerializer):
    classe_nom = serializers.CharField(source="id_classe.nom", read_only=True)
    periode_libelle = serializers.CharField(source="id_periode.libelle", read_only=True)

    class Meta:
        model = models.ConseilClasse
        fields = "__all__"


class RecuSerializer(serializers.ModelSerializer):
    montant = serializers.DecimalField(source="id_paiement.montant", max_digits=12,
                                       decimal_places=2, read_only=True)
    methode = serializers.CharField(source="id_paiement.get_methode_display", read_only=True)
    eleve_nom = serializers.SerializerMethodField()

    class Meta:
        model = models.Recu
        fields = "__all__"

    def get_eleve_nom(self, obj) -> str:
        eleve = obj.id_paiement.id_frais_eleve.id_eleve
        return f"{eleve.prenom} {eleve.nom}"


class RelanceSerializer(serializers.ModelSerializer):
    eleve_nom = serializers.SerializerMethodField()
    type_frais = serializers.CharField(source="id_frais_eleve.id_type_frais.libelle",
                                       read_only=True)

    class Meta:
        model = models.Relance
        fields = "__all__"

    def get_eleve_nom(self, obj) -> str:
        eleve = obj.id_frais_eleve.id_eleve
        return f"{eleve.prenom} {eleve.nom}"


class GenerationFraisSerializer(serializers.Serializer):
    """
    Entrée de POST /api/frais-eleves/generer/.

    Deux façons de décrire les échéances : une liste explicite, ou une
    première échéance et un nombre de mensualités. La seconde couvre le cas
    courant — dix mensualités au 5 du mois — sans faire saisir dix dates.
    """
    id_type_frais = serializers.PrimaryKeyRelatedField(queryset=models.TypeFrais.objects.all())
    id_annee_scolaire = serializers.PrimaryKeyRelatedField(queryset=models.AnneeScolaire.objects.all())
    id_classe = serializers.PrimaryKeyRelatedField(queryset=models.Classe.objects.all(),
                                                   required=False, allow_null=True)
    montant = serializers.DecimalField(max_digits=12, decimal_places=2, required=False,
                                       allow_null=True)
    echeances = serializers.ListField(child=serializers.DateField(), required=False,
                                      allow_empty=False, max_length=24)
    premiere_echeance = serializers.DateField(required=False)
    nombre_echeances = serializers.IntegerField(required=False, min_value=1, max_value=24)
    valider = serializers.BooleanField(default=False)

    def validate(self, attrs):
        echeances = attrs.get("echeances")
        premiere = attrs.get("premiere_echeance")
        if not echeances:
            if premiere is None:
                raise serializers.ValidationError({
                    "echeances": "Indiquez une liste d'échéances, ou une première "
                                 "échéance et un nombre de mensualités."
                })
            echeances = finances.echeances_mensuelles(
                premiere, attrs.get("nombre_echeances", 1))
        attrs["echeances"] = sorted(set(echeances))
        return attrs


class EncaissementSerializer(serializers.Serializer):
    """
    Entrée de POST /api/paiements/encaisser/ — le paiement vu depuis le
    dossier de l'élève, et non depuis une ligne de frais.

    L'administrateur saisit « Amadou a versé 50 000 F en espèces le 5 octobre » ;
    c'est le serveur qui retrouve l'inscription, l'année, les échéances
    ouvertes et répartit le montant. Passer par `id_frais_eleve` obligerait à
    connaître d'avance la ligne exacte — impossible pour un règlement qui
    couvre deux mensualités, et hors de portée d'un encaissement automatique.
    """
    id_eleve = serializers.PrimaryKeyRelatedField(queryset=models.Eleve.objects.all())
    id_annee_scolaire = serializers.PrimaryKeyRelatedField(
        queryset=models.AnneeScolaire.objects.all(), required=False, allow_null=True)
    id_type_frais = serializers.PrimaryKeyRelatedField(
        queryset=models.TypeFrais.objects.all(), required=False, allow_null=True)
    mois = serializers.RegexField(r"^\d{4}-\d{2}$", required=False, allow_blank=True,
                                  allow_null=True)
    montant = serializers.DecimalField(max_digits=12, decimal_places=2)
    methode = serializers.ChoiceField(choices=models.Paiement.Methode.choices)
    date_paiement = serializers.DateTimeField(required=False, allow_null=True)
    reference_transaction = serializers.CharField(required=False, allow_blank=True,
                                                  allow_null=True, max_length=100)
    commentaire = serializers.CharField(required=False, allow_blank=True,
                                        allow_null=True)
    # Frais cochés à l'écran : le paiement ne s'impute que sur eux. Absent, il
    # solde les frais ouverts du plus ancien au plus récent.
    frais = serializers.ListField(child=serializers.IntegerField(), required=False,
                                  allow_null=True, allow_empty=True)


class GenerationRelancesSerializer(serializers.Serializer):
    """Entrée de POST /api/relances/generer/."""
    canal = serializers.ChoiceField(choices=models.Relance.Canal.choices)
    jours_retard = serializers.IntegerField(required=False, min_value=0, max_value=365,
                                            default=0)
    delai_min = serializers.IntegerField(required=False, min_value=0, max_value=365,
                                         default=7)
    valider = serializers.BooleanField(default=False)


class TypeDepenseSerializer(serializers.ModelSerializer):
    """
    Poste de charge, avec son enveloppe de l'année et ce qu'il en reste.

    `budget_alloue` se saisit ici mais ne vit pas sur le poste : il est écrit
    dans la ligne `Budget` du poste pour l'année consultée. C'est le seul
    écran qui la touche — une enveloppe se déclarait autrefois à deux endroits
    sans que rien ne les relie, et on ne savait plus laquelle faisait foi.

    En lecture, l'enveloppe et le dépensé viennent de l'annotation posée par
    la vue (`finances.annoter_consommation`) et valent 0 en son absence :
    après une écriture, l'instance renvoyée sort de la sauvegarde et non de la
    liste annotée.
    """
    budget_alloue = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    montant_depense = serializers.SerializerMethodField()
    budget_disponible = serializers.SerializerMethodField()

    # Les chiffres du budget en sortent pour qui n'a pas accès au module
    # Budget : un caissier choisit une catégorie sans lire son enveloppe.
    CHAMPS_BUDGET = ("budget_alloue", "montant_depense", "budget_disponible")

    class Meta:
        model = models.TypeDepense
        fields = ["id", "id_ecole", "libelle", "budget_alloue",
                  "montant_depense", "budget_disponible"]
        read_only_fields = ["id_ecole"]

    def to_representation(self, instance):
        donnees = super().to_representation(instance)
        utilisateur = getattr(self.context.get("request"), "user", None)
        if utilisateur is not None and not peut_lire(utilisateur, "budget"):
            for champ in self.CHAMPS_BUDGET:
                donnees.pop(champ, None)
        return donnees

    # ---------- lecture ----------

    def _alloue(self, obj) -> Decimal:
        return Decimal(getattr(obj, "budget_alloue", 0) or 0)

    def _consomme(self, obj) -> Decimal:
        return Decimal(getattr(obj, "montant_depense", 0) or 0)

    def get_montant_depense(self, obj) -> float:
        return float(self._consomme(obj))

    def get_budget_disponible(self, obj) -> float:
        return float(self._alloue(obj) - self._consomme(obj))

    # ---------- écriture ----------

    def create(self, validated_data):
        montant = validated_data.pop("budget_alloue", None)
        return self._avec_budget(super().create(validated_data), montant)

    def update(self, instance, validated_data):
        montant = validated_data.pop("budget_alloue", None)
        return self._avec_budget(super().update(instance, validated_data), montant)

    def _avec_budget(self, instance, montant):
        """
        Reporte l'enveloppe saisie sur la ligne de budget de l'année.

        Sans année active — une école qui n'en a pas encore ouvert — il n'y a
        pas d'exercice à qui attribuer le montant : le poste se crée quand
        même, sans enveloppe, plutôt que d'échouer sur un écran de
        paramétrage.
        """
        annee = self.context.get("annee")
        budget = finances.budget_du_poste(instance, annee)
        if montant is not None and annee is not None:
            if budget is None:
                budget = models.Budget(
                    id_ecole=instance.id_ecole, id_type_depense=instance,
                    id_annee_scolaire=annee, categorie=instance.libelle)
            budget.montant_prevu = montant
            budget.save()
            # Une enveloppe posée après coup doit refléter les dépenses déjà
            # engagées, sinon le réalisé resterait à zéro jusqu'à la suivante.
            finances.imputer_au_budget(models.Depense(
                id_ecole=instance.id_ecole, id_type_depense=instance,
                date_depense=annee.date_debut))
        # L'instance renvoyée au client n'est pas annotée : on lui pose les
        # valeurs que la liste aurait calculées.
        instance.budget_alloue = budget.montant_prevu if budget else Decimal(0)
        return instance


class DepenseSerializer(serializers.ModelSerializer):
    categorie = serializers.CharField(source="id_type_depense.libelle", read_only=True)
    saisi_par = serializers.SerializerMethodField()
    # Le justificatif se dépose par POST /depenses/{id}/justificatif/ : le
    # champ ne porte qu'un chemin interne, que le client n'a pas à connaître.
    a_justificatif = serializers.SerializerMethodField()
    justificatif_url = serializers.CharField(read_only=True)

    class Meta:
        model = models.Depense
        fields = "__all__"
        # L'auteur est celui du compte connecté, jamais une valeur saisie.
        read_only_fields = ["id_ecole", "id_utilisateur"]

    def get_a_justificatif(self, obj) -> bool:
        return justificatifs.chemin(obj) is not None

    def get_saisi_par(self, obj) -> str | None:
        u = obj.id_utilisateur
        return f"{u.prenom} {u.nom}".strip() if u else None


class BudgetSerializer(serializers.ModelSerializer):
    categorie_depense = serializers.CharField(source="id_type_depense.libelle", read_only=True)
    # Le réalisé est recalculé à partir des dépenses (`finances.imputer_au_budget`) :
    # le laisser modifiable permettrait d'afficher un budget tenu sans l'avoir tenu.
    montant_realise = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    ecart = serializers.SerializerMethodField()

    class Meta:
        model = models.Budget
        fields = "__all__"
        read_only_fields = ["id_ecole"]

    def get_ecart(self, obj) -> float:
        return float(obj.montant_prevu - obj.montant_realise)


class JournalCaisseSerializer(serializers.ModelSerializer):
    journal = serializers.CharField(source="get_type_journal_display", read_only=True)

    class Meta:
        model = models.JournalCaisse
        fields = "__all__"
        read_only_fields = ["id_ecole"]


# ---------- Communications ----------

class CommunicationDestinataireSerializer(serializers.ModelSerializer):
    destinataire_nom = serializers.SerializerMethodField()

    class Meta:
        model = models.CommunicationDestinataire
        fields = "__all__"
        read_only_fields = ["statut_envoi", "erreur", "lu", "date_lecture"]

    def get_destinataire_nom(self, obj) -> str | None:
        cible = obj.id_utilisateur or obj.id_parent
        return f"{cible.prenom} {cible.nom}" if cible else obj.contact

    def validate(self, attrs):
        if not attrs.get("id_utilisateur") and not attrs.get("id_parent"):
            raise serializers.ValidationError(
                "Renseignez un destinataire : un compte utilisateur ou un parent/tuteur."
            )
        return attrs


class CommunicationSerializer(serializers.ModelSerializer):
    auteur_nom = serializers.SerializerMethodField()
    nb_destinataires = serializers.SerializerMethodField()
    nb_lus = serializers.SerializerMethodField()

    class Meta:
        model = models.Communication
        fields = "__all__"
        read_only_fields = ["id_ecole", "id_utilisateur_auteur", "date_creation", "date_envoi"]

    def get_auteur_nom(self, obj) -> str | None:
        if not obj.id_utilisateur_auteur:
            return None
        return f"{obj.id_utilisateur_auteur.prenom} {obj.id_utilisateur_auteur.nom}"

    def get_nb_destinataires(self, obj) -> int:
        return obj.destinataires.count()

    def get_nb_lus(self, obj) -> int:
        return obj.destinataires.filter(lu=True).count()

    def validate(self, attrs):
        """La cible détermine quel champ complémentaire est obligatoire."""
        cible = attrs.get("cible", getattr(self.instance, "cible", None))
        id_classe = attrs.get("id_classe", getattr(self.instance, "id_classe", None))
        role_cible = attrs.get("role_cible", getattr(self.instance, "role_cible", None))

        if cible == models.Communication.Cible.CLASSE and not id_classe:
            raise serializers.ValidationError({"id_classe": "Obligatoire pour une communication ciblant une classe."})
        if cible == models.Communication.Cible.ROLE and not role_cible:
            raise serializers.ValidationError({"role_cible": "Obligatoire pour une communication ciblant un rôle."})
        return attrs


# ---------- Documents & rapports ----------

class DocumentSerializer(serializers.ModelSerializer):
    deposant_nom = serializers.SerializerMethodField()

    class Meta:
        model = models.Document
        fields = "__all__"
        read_only_fields = ["id_ecole", "id_utilisateur_depot", "date_ajout"]

    def get_deposant_nom(self, obj) -> str | None:
        if not obj.id_utilisateur_depot:
            return None
        return f"{obj.id_utilisateur_depot.prenom} {obj.id_utilisateur_depot.nom}"


class RapportSerializer(serializers.ModelSerializer):
    type_rapport_libelle = serializers.CharField(source="get_type_rapport_display", read_only=True)

    class Meta:
        model = models.Rapport
        fields = "__all__"
        read_only_fields = ["id_ecole", "id_utilisateur", "statut", "fichier_url", "erreur",
                            "date_demande", "date_generation"]


# ---------- Paramètres ----------

class ParametreEcoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ParametreEcole
        fields = "__all__"
        read_only_fields = ["id_ecole", "date_maj"]

    def validate(self, attrs):
        instance = self.instance
        if instance and not instance.modifiable:
            raise serializers.ValidationError("Ce paramètre est verrouillé et ne peut pas être modifié.")

        # id_ecole étant renseigné par la vue, il est absent des données validées :
        # le UniqueTogetherValidator de DRF ne peut pas s'appliquer et l'unicité
        # (id_ecole, cle) remonterait en erreur 500. On la contrôle ici.
        cle = attrs.get("cle", getattr(instance, "cle", None))
        requete = self.context.get("request")
        if cle and requete is not None:
            doublons = models.ParametreEcole.objects.filter(
                id_ecole_id=requete.user.id_ecole_id, cle=cle
            )
            if instance:
                doublons = doublons.exclude(pk=instance.pk)
            if doublons.exists():
                raise serializers.ValidationError(
                    {"cle": "Un paramètre portant cette clé existe déjà pour cette école."}
                )

        type_valeur = attrs.get("type_valeur", getattr(instance, "type_valeur", None))
        valeur = attrs.get("valeur", getattr(instance, "valeur", None))
        if valeur in (None, ""):
            return attrs

        if type_valeur == models.ParametreEcole.TypeValeur.NOMBRE:
            try:
                float(valeur)
            except (TypeError, ValueError):
                raise serializers.ValidationError({"valeur": "Doit être un nombre."})
        elif type_valeur == models.ParametreEcole.TypeValeur.BOOLEEN:
            if str(valeur).lower() not in ("true", "false", "0", "1", "oui", "non"):
                raise serializers.ValidationError({"valeur": "Doit être un booléen (true/false)."})
        elif type_valeur == models.ParametreEcole.TypeValeur.JSON:
            import json
            try:
                json.loads(valeur)
            except (TypeError, ValueError):
                raise serializers.ValidationError({"valeur": "JSON invalide."})
        return attrs


# ---------- Journal d'activité ----------

class JournalActiviteSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.SerializerMethodField()
    action_libelle = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = models.JournalActivite
        fields = "__all__"
        read_only_fields = ["id_ecole"]

    def get_utilisateur_nom(self, obj) -> str | None:
        if not obj.id_utilisateur:
            return None
        return f"{obj.id_utilisateur.prenom} {obj.id_utilisateur.nom}"


# ---------- Corbeille ----------

class CorbeilleSerializer(serializers.ModelSerializer):
    """
    Une ligne de la corbeille, sans son instantané : `donnees` contient des
    lignes brutes — mots de passe hachés compris quand c'est un compte — qui
    n'ont rien à faire dans une réponse d'API.
    """
    type_libelle = serializers.SerializerMethodField()
    emporte = serializers.SerializerMethodField()
    supprime_par = serializers.SerializerMethodField()

    class Meta:
        model = models.Corbeille
        fields = ["id", "modele", "entite", "id_entite", "libelle", "type_libelle",
                  "resume", "emporte", "nb_elements", "supprime_par", "id_utilisateur",
                  "date_suppression"]
        read_only_fields = fields

    def get_type_libelle(self, obj) -> str:
        from django.apps import apps
        try:
            return str(apps.get_model(obj.modele)._meta.verbose_name).capitalize()
        except LookupError:
            return obj.entite

    def get_emporte(self, obj) -> str:
        """« 30 inscriptions, 4 évaluations » : ce que restaurer remettra aussi."""
        return ", ".join(f"{nombre} {libelle}" for libelle, nombre in (obj.resume or {}).items())

    def get_supprime_par(self, obj) -> str | None:
        if not obj.id_utilisateur:
            return None
        return f"{obj.id_utilisateur.prenom} {obj.id_utilisateur.nom}"


# ---------- Grille des tarifs ----------

class LigneTarifSerializer(serializers.Serializer):
    """Une case de la grille ; vide (`null`), le montant par défaut du frais s'applique."""
    id_niveau = serializers.IntegerField()
    id_type_frais = serializers.IntegerField()
    montant = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0,
                                       required=False, allow_null=True)


class GrilleTarifsSerializer(serializers.Serializer):
    """Entrée de POST /api/tarifs/grille/ : seules les cases modifiées sont envoyées."""
    tarifs = LigneTarifSerializer(many=True, allow_empty=False)
