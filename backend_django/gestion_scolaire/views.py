from rest_framework import mixins, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models import Model as DjangoModel
from django.utils import timezone
from drf_spectacular.utils import extend_schema

from . import models, serializers
from .audit import JournalisationMixin, journaliser
from .permissions import EstAdmin, EstAdminOuCaissier, EstAdminOuEnseignant, LectureSeuleOuAdmin

Utilisateur = get_user_model()


def ecole_de(instance, profondeur=0):
    """
    Remonte jusqu'à l'école dont dépend un objet.

    Beaucoup de tables n'ont pas de `id_ecole` : une note dépend de son élève,
    un paiement de son frais, lui-même d'un élève. On suit donc les clés
    étrangères jusqu'à trouver l'école.
    """
    if instance is None or profondeur > 4:
        return None
    if isinstance(instance, models.Ecole):
        return instance.pk
    if getattr(instance, "id_ecole_id", None) is not None:
        return instance.id_ecole_id

    for champ in instance._meta.fields:
        if not champ.is_relation or champ.related_model is None:
            continue
        trouve = ecole_de(getattr(instance, champ.name, None), profondeur + 1)
        if trouve is not None:
            return trouve
    return None


class CoherenceEcoleMixin:
    """
    Refuse toute écriture qui référencerait un objet d'une autre école.

    Filtrer les querysets protège la lecture, et `EcoleFiltreeMixin` impose
    `id_ecole` là où la colonne existe. Restait une porte ouverte : les tables
    sans `id_ecole` — note, absence, évaluation, paiement... — acceptaient une
    clé étrangère vers l'école d'autrui. Le résultat était invisible chez
    l'auteur, mais bien inscrit chez la victime.
    """

    def _verifier_coherence(self, serializer):
        utilisateur = self.request.user
        if not utilisateur.is_authenticated:
            return
        for champ, valeur in serializer.validated_data.items():
            if not isinstance(valeur, DjangoModel):
                continue
            ecole = ecole_de(valeur)
            if ecole is not None and ecole != utilisateur.id_ecole_id:
                raise ValidationError({champ: "Cet élément appartient à une autre école."})

    def perform_create(self, serializer):
        self._verifier_coherence(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._verifier_coherence(serializer)
        super().perform_update(serializer)


class EcoleFiltreeMixin:
    """
    Isolation multi-tenant, dans les deux sens.

    En lecture, chaque queryset est filtré sur l'école de l'utilisateur
    connecté ; `ecole_field` précise le chemin vers id_ecole quand le modèle
    n'a pas de FK directe vers Ecole.

    En écriture, `id_ecole` est imposé à partir du compte connecté. Filtrer
    la lecture ne suffisait pas : le champ étant modifiable, un client pouvait
    créer un enregistrement dans l'école d'autrui — invisible chez lui, bien
    réel chez la victime. Les serializers concernés déclarent en plus
    `id_ecole` en lecture seule, ce qui évite aussi de l'exiger du client.
    """
    ecole_field = "id_ecole"

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return qs.none()
        return qs.filter(**{self.ecole_field: self.request.user.id_ecole_id})

    def perform_create(self, serializer):
        modele = serializer.Meta.model
        if any(f.name == "id_ecole" for f in modele._meta.fields):
            serializer.save(id_ecole=self.request.user.id_ecole)
        else:
            super().perform_create(serializer)


# ---------- Comptes ----------

class UtilisateurViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    """
    Gestion des comptes, réservée aux administrateurs.

    Un admin n'agit que dans son école : le filtrage vient d'EcoleFiltreeMixin
    en lecture, et `id_ecole` est imposé par la vue en écriture.
    """
    queryset = Utilisateur.objects.all().order_by("nom", "prenom")
    serializer_class = serializers.UtilisateurAdminSerializer
    permission_classes = [EstAdmin]
    filterset_fields = ["role", "statut"]
    search_fields = ["nom", "prenom", "email"]
    entite_journal = "utilisateur"

    def perform_create(self, serializer):
        serializer.save(id_ecole=self.request.user.id_ecole)
        self._journaliser_objet(models.JournalActivite.Action.CREATION, serializer.instance)

    def perform_destroy(self, instance):
        # Se supprimer soi-même déconnecterait l'auteur de l'action et, si
        # c'est le seul admin, verrouillerait l'école. Le cas symétrique —
        # rétrograder ou désactiver le dernier admin — est traité dans le
        # serializer, qui voit la modification demandée.
        if instance.pk == self.request.user.pk:
            raise ValidationError("Vous ne pouvez pas supprimer votre propre compte.")
        super().perform_destroy(instance)


# ---------- Cœur ----------

class EcoleViewSet(CoherenceEcoleMixin, JournalisationMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                   mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Fiche de sa propre école, en lecture et modification.

    Ni création ni suppression : ouvrir ou fermer une école est une opération
    de la plateforme, pas de l'établissement. Le queryset est restreint à
    l'école du compte connecté — sans quoi n'importe quel administrateur
    pourrait lire et renommer les écoles clientes des autres.
    """
    queryset = models.Ecole.objects.all()
    serializer_class = serializers.EcoleSerializer
    permission_classes = [EstAdmin]

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return self.queryset.none()
        return self.queryset.filter(pk=self.request.user.id_ecole_id)


# ---------- Module élèves ----------

class AnneeScolaireViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.AnneeScolaire.objects.all()
    serializer_class = serializers.AnneeScolaireSerializer
    permission_classes = [LectureSeuleOuAdmin]


class PeriodeScolaireViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Trimestres ou semestres. Isolés via l'année scolaire, qui porte l'école."""
    queryset = models.PeriodeScolaire.objects.select_related("id_annee_scolaire").all()
    serializer_class = serializers.PeriodeScolaireSerializer
    permission_classes = [LectureSeuleOuAdmin]
    filterset_fields = ["id_annee_scolaire"]

    def get_queryset(self):
        return super().get_queryset().filter(
            id_annee_scolaire__id_ecole_id=self.request.user.id_ecole_id
        ).order_by("date_debut")


class NiveauViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Niveau.objects.all()
    serializer_class = serializers.NiveauSerializer
    permission_classes = [LectureSeuleOuAdmin]


class ClasseViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Classe.objects.select_related("id_niveau", "id_enseignant_principal").all()
    serializer_class = serializers.ClasseSerializer
    permission_classes = [LectureSeuleOuAdmin]


class ParentTuteurViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.ParentTuteur.objects.all()
    serializer_class = serializers.ParentTuteurSerializer
    permission_classes = [permissions.IsAuthenticated]


class EleveViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Eleve.objects.all()
    serializer_class = serializers.EleveSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["statut"]
    search_fields = ["nom", "prenom", "matricule"]

    def get_serializer_class(self):
        if self.action == "inscrire":
            return serializers.EleveInscriptionSerializer
        return super().get_serializer_class()

    @action(detail=False, methods=["post"])
    def inscrire(self, request):
        """
        Endpoint composite utilisé par le formulaire Angular "Inscrire un élève" :
        POST /api/eleves/inscrire/
        Crée l'élève + son inscription + (optionnel) son parent en une seule requête.
        """
        # L'école vient du compte connecté, jamais du corps de la requête :
        # sinon un admin pourrait inscrire un élève dans une autre école.
        donnees = request.data.copy()
        donnees["id_ecole"] = request.user.id_ecole_id

        serializer = self.get_serializer(data=donnees)
        serializer.is_valid(raise_exception=True)
        eleve = serializer.save()
        journaliser(
            request,
            models.JournalActivite.Action.CREATION,
            entite="eleve",
            id_entite=eleve.pk,
            description=f"Inscription de {eleve.prenom} {eleve.nom} ({eleve.matricule})",
        )
        return Response(serializers.EleveSerializer(eleve).data, status=status.HTTP_201_CREATED)


class InscriptionViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Inscription.objects.select_related("id_eleve", "id_classe").all()
    serializer_class = serializers.InscriptionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["id_classe", "id_annee_scolaire", "statut"]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)

    # La trace d'inscription est écrite par Inscription.save(), pour couvrir
    # aussi l'endpoint composite /eleves/inscrire/. Seul le changement de
    # classe est traité ici : la vue est le seul endroit qui connaît la
    # classe précédente.
    def perform_update(self, serializer):
        classe_avant = models.Inscription.objects.get(pk=serializer.instance.pk).id_classe
        super().perform_update(serializer)
        inscription = serializer.instance
        if classe_avant.pk != inscription.id_classe_id:
            models.HistoriqueScolaire.objects.create(
                id_eleve=inscription.id_eleve,
                type_evenement=models.HistoriqueScolaire.TypeEvenement.CHANGEMENT_CLASSE,
                id_ancienne_classe=classe_avant,
                id_nouvelle_classe=inscription.id_classe,
                date_evenement=timezone.now().date(),
                motif=f"Passage de {classe_avant.nom} à {inscription.id_classe.nom}",
            )


class EleveParentViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Rattache un parent à un élève, en dehors du formulaire d'inscription."""
    queryset = models.EleveParent.objects.select_related("id_eleve", "id_parent").all()
    serializer_class = serializers.EleveParentSerializer
    permission_classes = [LectureSeuleOuAdmin]
    filterset_fields = ["id_eleve", "id_parent", "lien_parente"]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class DossierAdministratifViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """
    Pièces du dossier d'un élève. Distinct de /documents/, qui porte les
    documents de l'établissement.
    """
    queryset = models.DossierAdministratif.objects.select_related("id_eleve").all()
    serializer_class = serializers.DossierAdministratifSerializer
    permission_classes = [EstAdmin]
    filterset_fields = ["id_eleve", "type_piece"]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class HistoriqueScolaireViewSet(EcoleFiltreeMixin, viewsets.ReadOnlyModelViewSet):
    """
    Parcours d'un élève, en lecture seule : les entrées sont écrites
    automatiquement par les inscriptions, pas saisies à la main.
    """
    queryset = models.HistoriqueScolaire.objects.select_related(
        "id_eleve", "id_ancienne_classe", "id_nouvelle_classe"
    ).all().order_by("-date_evenement")
    serializer_class = serializers.HistoriqueScolaireSerializer
    permission_classes = [permissions.IsAuthenticated]
    ecole_field = "id_eleve__id_ecole_id"
    filterset_fields = ["id_eleve", "type_evenement"]


# ---------- Module pédagogique ----------

class EnseignantViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Enseignant.objects.all()
    serializer_class = serializers.EnseignantSerializer
    permission_classes = [LectureSeuleOuAdmin]


class MatiereViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Matiere.objects.all()
    serializer_class = serializers.MatiereSerializer
    permission_classes = [LectureSeuleOuAdmin]


class CoefficientMatiereViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Poids d'une matière dans une classe — base du calcul des moyennes."""
    queryset = models.CoefficientMatiere.objects.select_related("id_matiere", "id_classe").all()
    serializer_class = serializers.CoefficientMatiereSerializer
    permission_classes = [LectureSeuleOuAdmin]
    filterset_fields = ["id_classe", "id_matiere", "id_annee_scolaire"]

    def get_queryset(self):
        return super().get_queryset().filter(
            id_classe__id_ecole_id=self.request.user.id_ecole_id
        )


class AffectationEnseignantViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Qui enseigne quelle matière, dans quelle classe, pour quel volume horaire."""
    queryset = models.AffectationEnseignant.objects.select_related(
        "id_enseignant", "id_matiere", "id_classe"
    ).all()
    serializer_class = serializers.AffectationEnseignantSerializer
    permission_classes = [LectureSeuleOuAdmin]
    filterset_fields = ["id_enseignant", "id_classe", "id_matiere", "id_annee_scolaire"]

    def get_queryset(self):
        return super().get_queryset().filter(
            id_classe__id_ecole_id=self.request.user.id_ecole_id
        )

    @action(detail=False, methods=["get"])
    def charges(self, request):
        """
        GET /api/affectations/charges/ — volume horaire total par enseignant,
        pour vérifier les services d'un coup d'œil.
        """
        lignes = (
            self.get_queryset()
            .values("id_enseignant", "id_enseignant__matricule")
            .annotate(total_heures=Sum("volume_horaire"), nb_affectations=Count("id"))
            .order_by("-total_heures")
        )
        return Response([
            {
                "id_enseignant": l["id_enseignant"],
                "matricule": l["id_enseignant__matricule"],
                "total_heures": l["total_heures"] or 0,
                "nb_affectations": l["nb_affectations"],
            }
            for l in lignes
        ])


class ConseilClasseViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.ConseilClasse.objects.select_related("id_classe", "id_periode").all()
    serializer_class = serializers.ConseilClasseSerializer
    permission_classes = [EstAdminOuEnseignant]
    filterset_fields = ["id_classe", "id_periode"]

    def get_queryset(self):
        return super().get_queryset().filter(
            id_classe__id_ecole_id=self.request.user.id_ecole_id
        )


class EvaluationViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Evaluation.objects.all()
    serializer_class = serializers.EvaluationSerializer
    permission_classes = [EstAdminOuEnseignant]

    def get_queryset(self):
        return super().get_queryset().filter(id_classe__id_ecole_id=self.request.user.id_ecole_id)


class NoteViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Note.objects.all()
    serializer_class = serializers.NoteSerializer
    permission_classes = [EstAdminOuEnseignant]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class BulletinViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Bulletin.objects.all()
    serializer_class = serializers.BulletinSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class AbsenceViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Absence.objects.all()
    serializer_class = serializers.AbsenceSerializer
    permission_classes = [EstAdminOuEnseignant]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


# ---------- Emploi du temps ----------

class SalleViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Salle.objects.all()
    serializer_class = serializers.SalleSerializer
    permission_classes = [LectureSeuleOuAdmin]


class EmploiDuTempsViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.EmploiDuTemps.objects.select_related("id_matiere", "id_enseignant", "id_salle").all()
    serializer_class = serializers.EmploiDuTempsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset().filter(id_classe__id_ecole_id=self.request.user.id_ecole_id)
        id_classe = self.request.query_params.get("classe")
        if id_classe:
            qs = qs.filter(id_classe=id_classe)
        return qs


# ---------- Module financier ----------

class TypeFraisViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.TypeFrais.objects.all()
    serializer_class = serializers.TypeFraisSerializer
    permission_classes = [EstAdmin]


class FraisEleveViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.FraisEleve.objects.select_related("id_eleve", "id_type_frais").all()
    serializer_class = serializers.FraisEleveSerializer
    permission_classes = [EstAdminOuCaissier]

    def get_queryset(self):
        qs = super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)
        id_eleve = self.request.query_params.get("eleve")
        statut = self.request.query_params.get("statut")
        if id_eleve:
            qs = qs.filter(id_eleve=id_eleve)
        if statut:
            qs = qs.filter(statut=statut)
        return qs

    @action(detail=False, methods=["get"])
    def impayes(self, request):
        """GET /api/frais-eleves/impayes/ — utilisé par l'écran Finances (admin) et le parcours caissier."""
        qs = self.get_queryset().exclude(statut=models.FraisEleve.Statut.PAYE)
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page or qs, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class PaiementViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """
    Cœur du parcours caissier : POST /api/paiements/ encaisse un paiement,
    met à jour le frais concerné, génère le reçu et le mouvement de caisse
    automatiquement (voir PaiementSerializer.create).
    """
    queryset = models.Paiement.objects.select_related("id_frais_eleve", "id_frais_eleve__id_eleve").all()
    serializer_class = serializers.PaiementSerializer
    permission_classes = [EstAdminOuCaissier]

    def get_queryset(self):
        return super().get_queryset().filter(id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class RecuViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Recu.objects.all()
    serializer_class = serializers.RecuSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(id_paiement__id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class RelanceViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Relance.objects.all()
    serializer_class = serializers.RelanceSerializer
    permission_classes = [EstAdminOuCaissier]

    def get_queryset(self):
        return super().get_queryset().filter(id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class TypeDepenseViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.TypeDepense.objects.all()
    serializer_class = serializers.TypeDepenseSerializer
    permission_classes = [EstAdmin]


class DepenseViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Depense.objects.all()
    serializer_class = serializers.DepenseSerializer
    permission_classes = [EstAdmin]


class BudgetViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Budget.objects.all()
    serializer_class = serializers.BudgetSerializer
    permission_classes = [EstAdmin]


class JournalCaisseViewSet(EcoleFiltreeMixin, viewsets.ReadOnlyModelViewSet):
    queryset = models.JournalCaisse.objects.all().order_by("-date_mouvement")
    serializer_class = serializers.JournalCaisseSerializer
    permission_classes = [EstAdminOuCaissier]


# ---------- Auth ----------

@extend_schema(
    responses=serializers.UtilisateurSerializer,
    summary="Profil de l'utilisateur connecté",
    tags=["auth"],
)
class MoiView(APIView):
    """GET /api/auth/moi/ — retourne le profil de l'utilisateur connecté (pour l'app Angular au démarrage)."""
    permission_classes = [permissions.IsAuthenticated]
    # APIView n'expose pas de serializer : sans l'indication ci-dessus, la
    # documentation ne saurait pas décrire la réponse.

    def get(self, request):
        return Response(serializers.UtilisateurSerializer(request.user).data)


class ConnexionView(TokenObtainPairView):
    """
    POST /api/auth/token/ — même comportement que SimpleJWT, avec une entrée
    au journal d'activité pour les connexions réussies comme pour les échecs.
    """

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        try:
            reponse = super().post(request, *args, **kwargs)
        except Exception:
            utilisateur = Utilisateur.objects.filter(email=email).first()
            if utilisateur:
                # L'utilisateur est nommé mais pas rattaché : la tentative a
                # échoué, on ne l'attribue pas à un compte authentifié.
                journaliser(
                    request,
                    models.JournalActivite.Action.ECHEC_CONNEXION,
                    id_ecole=utilisateur.id_ecole_id,
                    entite="utilisateur",
                    id_entite=utilisateur.pk,
                    description=f"Échec de connexion pour {email}",
                )
            raise

        utilisateur = Utilisateur.objects.filter(email=email).first()
        if utilisateur:
            journaliser(
                request,
                models.JournalActivite.Action.CONNEXION,
                id_ecole=utilisateur.id_ecole_id,
                entite="utilisateur",
                id_entite=utilisateur.pk,
                description=f"Connexion de {utilisateur.prenom} {utilisateur.nom}",
                utilisateur=utilisateur,
            )
        return reponse


# ---------- Module communications ----------

class CommunicationViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Communication.objects.select_related("id_utilisateur_auteur", "id_classe").all()
    serializer_class = serializers.CommunicationSerializer
    permission_classes = [LectureSeuleOuAdmin]
    filterset_fields = ["statut", "canal", "type_communication"]
    search_fields = ["objet", "contenu"]

    def perform_create(self, serializer):
        serializer.save(
            id_ecole=self.request.user.id_ecole,
            id_utilisateur_auteur=self.request.user,
        )
        self._journaliser_objet(models.JournalActivite.Action.CREATION, serializer.instance)

    @action(detail=True, methods=["post"])
    def envoyer(self, request, pk=None):
        """
        POST /api/communications/{id}/envoyer/
        Résout la cible en destinataires concrets et marque la communication
        comme envoyée. L'expédition réelle (SMS/e-mail) reste à brancher sur
        un prestataire — les destinataires restent alors "en_attente".
        """
        communication = self.get_object()
        if communication.statut == models.Communication.Statut.ENVOYEE:
            return Response({"detail": "Cette communication a déjà été envoyée."},
                            status=status.HTTP_400_BAD_REQUEST)

        destinataires = self._resoudre_destinataires(communication)
        if destinataires:
            models.CommunicationDestinataire.objects.bulk_create(destinataires, ignore_conflicts=True)

        # En cible "individuel", les destinataires ont été saisis à la main :
        # la résolution ne renvoie rien, mais l'envoi doit rester possible.
        total = communication.destinataires.count()
        if not total:
            return Response({"detail": "Aucun destinataire ne correspond à la cible choisie."},
                            status=status.HTTP_400_BAD_REQUEST)

        communication.statut = models.Communication.Statut.ENVOYEE
        communication.date_envoi = timezone.now()
        communication.save(update_fields=["statut", "date_envoi"])

        journaliser(
            request,
            models.JournalActivite.Action.MODIFICATION,
            entite="communication",
            id_entite=communication.pk,
            description=f"Envoi de « {communication.objet} » à {total} destinataire(s)",
        )
        return Response(self.get_serializer(communication).data)

    def _resoudre_destinataires(self, communication):
        """
        Traduit la cible en lignes CommunicationDestinataire (non sauvegardées).

        Le canal décide de la nature des destinataires : en interne on vise des
        comptes utilisateurs, par SMS ou e-mail on vise les parents/tuteurs —
        la plupart des familles n'ont pas de compte mais ont un téléphone.
        """
        Destinataire = models.CommunicationDestinataire
        cible = communication.cible
        canal = communication.canal

        # --- Cible "individuel" : les lignes ont déjà été créées à la main. ---
        if cible == models.Communication.Cible.INDIVIDUEL:
            return []

        ids_eleves = None
        if cible == models.Communication.Cible.CLASSE:
            ids_eleves = list(models.Inscription.objects.filter(
                id_classe_id=communication.id_classe_id
            ).values_list("id_eleve_id", flat=True))

        # --- Canal interne : comptes utilisateurs. ---
        if canal == models.Communication.Canal.INTERNE:
            comptes = Utilisateur.objects.filter(
                id_ecole_id=communication.id_ecole_id,
                statut=Utilisateur.Statut.ACTIF,
            )
            if cible == models.Communication.Cible.ROLE:
                comptes = comptes.filter(role=communication.role_cible)
            elif cible == models.Communication.Cible.CLASSE:
                ids_parents = models.EleveParent.objects.filter(
                    id_eleve_id__in=ids_eleves
                ).values_list("id_parent_id", flat=True)
                comptes = comptes.filter(
                    Q(eleve_profil__id__in=ids_eleves) | Q(parent_profil__id__in=ids_parents)
                ).distinct()
            return [
                Destinataire(id_communication=communication, id_utilisateur=u, contact=u.email)
                for u in comptes
            ]

        # --- Canaux SMS / e-mail : contacts des parents et tuteurs. ---
        parents = models.ParentTuteur.objects.filter(id_ecole_id=communication.id_ecole_id)
        if cible == models.Communication.Cible.CLASSE:
            ids_parents = models.EleveParent.objects.filter(
                id_eleve_id__in=ids_eleves
            ).values_list("id_parent_id", flat=True)
            parents = parents.filter(id__in=ids_parents)
        elif cible == models.Communication.Cible.ROLE:
            # Un rôle autre que "parent" n'a pas de contact famille : on retombe
            # sur les comptes correspondants et leur e-mail / téléphone.
            if communication.role_cible != Utilisateur.Role.PARENT:
                comptes = Utilisateur.objects.filter(
                    id_ecole_id=communication.id_ecole_id,
                    statut=Utilisateur.Statut.ACTIF,
                    role=communication.role_cible,
                )
                champ = "telephone" if canal == models.Communication.Canal.SMS else "email"
                return [
                    Destinataire(id_communication=communication, id_utilisateur=u,
                                 contact=getattr(u, champ))
                    for u in comptes if getattr(u, champ)
                ]

        champ = "telephone" if canal == models.Communication.Canal.SMS else "email"
        return [
            Destinataire(id_communication=communication, id_parent=p, contact=getattr(p, champ))
            for p in parents.distinct() if getattr(p, champ)
        ]


# Volontairement hors journalisation : marquer un message comme lu ou ajouter
# un destinataire n'a pas à saturer le journal d'audit.
class CommunicationDestinataireViewSet(CoherenceEcoleMixin, viewsets.ModelViewSet):
    queryset = models.CommunicationDestinataire.objects.select_related("id_utilisateur").all()
    serializer_class = serializers.CommunicationDestinataireSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset().filter(
            id_communication__id_ecole_id=self.request.user.id_ecole_id
        )
        # Un non-admin ne voit que ce qui lui est adressé.
        if self.request.user.role != "admin":
            qs = qs.filter(id_utilisateur=self.request.user)
        return qs

    @action(detail=True, methods=["post"], url_path="marquer-lu")
    def marquer_lu(self, request, pk=None):
        """POST /api/communication-destinataires/{id}/marquer-lu/"""
        destinataire = self.get_object()
        if destinataire.id_utilisateur_id != request.user.id:
            return Response({"detail": "Vous ne pouvez marquer comme lu que vos propres messages."},
                            status=status.HTTP_403_FORBIDDEN)
        if not destinataire.lu:
            destinataire.lu = True
            destinataire.date_lecture = timezone.now()
            destinataire.save(update_fields=["lu", "date_lecture"])
        return Response(self.get_serializer(destinataire).data)


# ---------- Module documents & rapports ----------

class DocumentViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Document.objects.select_related("id_utilisateur_depot").all()
    serializer_class = serializers.DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["categorie", "visibilite"]
    search_fields = ["titre", "description"]

    # Qui a le droit de voir quoi, par niveau de visibilité.
    VISIBILITE_PAR_ROLE = {
        "admin": None,  # aucun filtre
        "enseignant": [models.Document.Visibilite.ENSEIGNANTS, models.Document.Visibilite.TOUS],
        "caissier": [models.Document.Visibilite.TOUS],
        "parent": [models.Document.Visibilite.PARENTS, models.Document.Visibilite.TOUS],
        "eleve": [models.Document.Visibilite.TOUS],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        visibilites = self.VISIBILITE_PAR_ROLE.get(self.request.user.role, [])
        if visibilites is None:
            return qs
        return qs.filter(visibilite__in=visibilites)

    def perform_create(self, serializer):
        serializer.save(
            id_ecole=self.request.user.id_ecole,
            id_utilisateur_depot=self.request.user,
        )
        self._journaliser_objet(models.JournalActivite.Action.CREATION, serializer.instance)


class RapportViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    """
    Historique des exports demandés. La génération effective du fichier
    (PDF/Excel) n'est pas implémentée : la demande est enregistrée en
    "en_attente" et attend le service de génération.
    """
    queryset = models.Rapport.objects.select_related("id_utilisateur").all()
    serializer_class = serializers.RapportSerializer
    permission_classes = [LectureSeuleOuAdmin]
    filterset_fields = ["type_rapport", "statut", "format_export"]

    def perform_create(self, serializer):
        serializer.save(
            id_ecole=self.request.user.id_ecole,
            id_utilisateur=self.request.user,
        )
        journaliser(
            self.request,
            models.JournalActivite.Action.EXPORT,
            entite="rapport",
            id_entite=serializer.instance.pk,
            description=str(serializer.instance),
        )


# ---------- Module paramètres ----------

class ParametreEcoleViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.ParametreEcole.objects.all()
    serializer_class = serializers.ParametreEcoleSerializer
    permission_classes = [EstAdmin]
    filterset_fields = ["categorie"]

    def perform_create(self, serializer):
        serializer.save(id_ecole=self.request.user.id_ecole)
        self._journaliser_objet(models.JournalActivite.Action.CREATION, serializer.instance)


# ---------- Journal d'activité ----------

class JournalActiviteViewSet(EcoleFiltreeMixin, viewsets.ReadOnlyModelViewSet):
    """Lecture seule et réservé aux admins : un journal d'audit ne se modifie pas."""
    queryset = models.JournalActivite.objects.select_related("id_utilisateur").all()
    serializer_class = serializers.JournalActiviteSerializer
    permission_classes = [EstAdmin]
    filterset_fields = ["action", "entite", "id_utilisateur"]
    search_fields = ["description", "entite"]
