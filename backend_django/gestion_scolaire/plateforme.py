"""
Administration de la plateforme : les écoles clientes et leur premier compte.

Réservée au super administrateur, qui n'appartient à aucune école. Il crée une
école et son administrateur, suit son offre (Standard ou Pro) et le règlement
de l'abonnement ; il ne voit rien de l'intérieur des écoles — élèves, notes,
finances restent derrière la matrice des autorisations, où il ne figure pas.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import matricules, models
from .audit import journaliser
from .permissions import ROLES_DIRECTION, EstSuperAdmin

Utilisateur = get_user_model()


# ---------- Serializers ----------

class AdminEcoleSerializer(serializers.ModelSerializer):
    """Un compte de direction d'une école, tel que la plateforme le liste."""
    role_libelle = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = Utilisateur
        fields = ["id", "nom", "prenom", "email", "telephone", "role", "role_libelle",
                  "statut", "date_creation"]
        read_only_fields = fields


class NouvelAdminSerializer(serializers.Serializer):
    """Le premier administrateur d'une école, ou un administrateur de plus."""
    nom = serializers.CharField(max_length=100)
    prenom = serializers.CharField(max_length=100)
    email = serializers.EmailField(max_length=150)
    telephone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        # L'e-mail est l'identifiant de connexion sur toute la plateforme.
        if Utilisateur.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Cette adresse e-mail est déjà utilisée.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def creer(self, ecole):
        donnees = dict(self.validated_data)
        return Utilisateur.objects.create_user(
            email=donnees.pop("email"), id_ecole=ecole, password=donnees.pop("password"),
            role=Utilisateur.Role.ADMIN, **donnees)


class EcolePlateformeSerializer(serializers.ModelSerializer):
    """
    Une école cliente : coordonnées, offre, abonnement et comptes de direction.

    À la création, `admin` porte le premier administrateur : une école sans
    compte ne pourrait pas se connecter, et personne d'autre que la plateforme
    ne pourrait lui en créer un.
    """
    admin = NouvelAdminSerializer(write_only=True, required=False)
    admins = serializers.SerializerMethodField()
    nb_utilisateurs = serializers.IntegerField(read_only=True, default=0)
    nb_eleves = serializers.IntegerField(read_only=True, default=0)
    plan_libelle = serializers.CharField(source="get_plan_abonnement_display", read_only=True)
    statut_libelle = serializers.CharField(source="get_statut_display", read_only=True)

    class Meta:
        model = models.Ecole
        fields = ["id", "nom", "code_ecole", "adresse", "telephone", "email", "plan_abonnement", "plan_libelle",
                  "abonnement_paye", "date_paiement", "statut", "statut_libelle",
                  "date_activation", "date_creation", "nb_utilisateurs", "nb_eleves",
                  "admins", "admin"]
        read_only_fields = ["id", "date_creation"]
        # Facultatif : sans code saisi, `Ecole.save` en tire un du nom.
        extra_kwargs = {"code_ecole": {"required": False, "allow_blank": True, "validators": []}}

    def get_admins(self, obj) -> list:
        return AdminEcoleSerializer(getattr(obj, "comptes_direction", []), many=True).data

    def validate_code_ecole(self, valeur):
        """
        Le code ouvre chaque matricule de l'école. Le modifier ne touche pas
        aux matricules déjà attribués, seulement aux suivants.
        """
        code = matricules.normaliser_code(valeur)
        if not code:
            if self.instance is not None:
                raise serializers.ValidationError("Le code de l'école ne peut pas être vide.")
            return ""
        if not matricules.code_valide(code):
            raise serializers.ValidationError(
                f"De 2 à {matricules.CODE_LONGUEUR_MAX} lettres ou chiffres, sans espace ni tiret.")
        autres = models.Ecole.objects.filter(code_ecole=code)
        if self.instance is not None:
            autres = autres.exclude(pk=self.instance.pk)
        if autres.exists():
            raise serializers.ValidationError("Ce code est déjà pris par une autre école.")
        return code

    def validate(self, attrs):
        if self.instance is None and "admin" not in attrs:
            raise serializers.ValidationError(
                {"admin": "Renseignez le premier administrateur de l'école."})
        # Marquer l'abonnement réglé sans date : c'est aujourd'hui qu'il l'est.
        if attrs.get("abonnement_paye") and not attrs.get("date_paiement") \
                and not (self.instance and self.instance.date_paiement):
            attrs["date_paiement"] = timezone.localdate()
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        admin = validated_data.pop("admin")
        validated_data.setdefault("date_activation", timezone.localdate())
        ecole = models.Ecole.objects.create(**validated_data)
        entree = NouvelAdminSerializer(data=self.initial_data["admin"])
        entree.is_valid(raise_exception=True)
        entree.creer(ecole)
        return ecole

    def update(self, instance, validated_data):
        validated_data.pop("admin", None)
        return super().update(instance, validated_data)


# ---------- Vue ----------

class EcolePlateformeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                             mixins.CreateModelMixin, mixins.UpdateModelMixin,
                             viewsets.GenericViewSet):
    """
    Les écoles clientes : GET/POST /api/plateforme/ecoles/, GET/PATCH …/{id}/.

    Pas de suppression : une école qui part est « résiliée », ses données
    restent le temps de les lui remettre.
    """
    serializer_class = EcolePlateformeSerializer
    permission_classes = [EstSuperAdmin]
    http_method_names = ["get", "post", "patch", "head", "options"]
    search_fields = ["nom", "email", "telephone"]
    filterset_fields = ["plan_abonnement", "statut", "abonnement_paye"]

    def get_queryset(self):
        direction = (Utilisateur.objects.filter(role__in=ROLES_DIRECTION)
                     .order_by("nom", "prenom"))
        # Deux sous-requêtes plutôt que deux jointures : jointes ensemble, les
        # tables des comptes et des élèves se multipliaient ligne à ligne, et
        # la liste mettait plusieurs secondes pour une seule école.
        def compte(requete):
            return Coalesce(Subquery(requete.order_by().values("id_ecole")
                                     .annotate(n=Count("pk")).values("n")[:1]), 0)
        return (models.Ecole.objects
                .annotate(nb_utilisateurs=compte(Utilisateur.objects.filter(id_ecole=OuterRef("pk"))),
                          nb_eleves=compte(models.Eleve.objects.filter(id_ecole=OuterRef("pk"), statut="actif")))
                .prefetch_related(Prefetch("utilisateurs", queryset=direction,
                                           to_attr="comptes_direction"))
                .order_by("nom"))

    def perform_create(self, serializer):
        ecole = serializer.save()
        journaliser(self.request, models.JournalActivite.Action.CREATION, id_ecole=ecole.pk,
                    entite="ecole", id_entite=ecole.pk,
                    description=f"École créée par la plateforme : {ecole.nom}")

    @extend_schema(request=NouvelAdminSerializer, responses=AdminEcoleSerializer)
    @action(detail=True, methods=["post"])
    def admins(self, request, pk=None):
        """Ajoute un administrateur à l'école : POST /api/plateforme/ecoles/{id}/admins/"""
        ecole = self.get_object()
        entree = NouvelAdminSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        compte = entree.creer(ecole)
        journaliser(request, models.JournalActivite.Action.CREATION, id_ecole=ecole.pk,
                    entite="utilisateur", id_entite=compte.pk,
                    description=f"Administrateur créé par la plateforme : {compte.email}")
        return Response(AdminEcoleSerializer(compte).data, status=status.HTTP_201_CREATED)
