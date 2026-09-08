from rest_framework import mixins, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models import Model as DjangoModel
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema

from . import (finances, finances_documents, imports_eleves, models, pedagogie,
               pedagogie_documents, scolarite, serializers)
from .audit import JournalisationMixin, journaliser
from .permissions import EstAdmin, EstAdminOuCaissier, EstAdminOuEnseignant, LectureSeuleOuAdmin

Utilisateur = get_user_model()

# Les listes d'école pèsent quelques dizaines de Ko ; au-delà de 5 Mo il ne
# s'agit plus d'une liste nominative, et le fichier serait lu entièrement en
# mémoire avant d'être rejeté.
TAILLE_MAX_IMPORT = 5 * 1024 * 1024

FORMATS_EXPORT = ("xlsx", "pdf")

# Le paramètre ne s'appelle pas `format` : DRF réserve ce nom à la négociation
# de contenu et répond 404 dès qu'il ne reconnaît pas la valeur — un export
# `?format=xlsx` n'atteignait jamais la vue.
PARAM_FORMAT = "format_export"


def _format_export(request):
    demande = (request.query_params.get(PARAM_FORMAT) or "xlsx").lower()
    if demande not in FORMATS_EXPORT:
        raise ValidationError({
            PARAM_FORMAT: f"Format inconnu : {demande}. "
                          f"Attendu : {' ou '.join(FORMATS_EXPORT)}."
        })
    return demande


def _date_param(request, nom):
    """Lit une date de l'URL, en refusant explicitement les valeurs illisibles."""
    brut = request.query_params.get(nom)
    if not brut:
        return None
    valeur = parse_date(brut)
    if valeur is None:
        raise ValidationError({nom: "Date attendue au format AAAA-MM-JJ."})
    return valeur


def _annee_demandee(request, ecole):
    """
    Année visée par une vue de synthèse : celle de l'URL, sinon l'année active.

    Renvoie `None` quand l'école n'a aucune année active — un tableau de bord
    doit l'annoncer, pas échouer sur une école qui démarre.
    """
    brut = request.query_params.get("id_annee_scolaire")
    if brut:
        try:
            return models.AnneeScolaire.objects.get(pk=brut, id_ecole=ecole)
        except (models.AnneeScolaire.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"id_annee_scolaire": "Année inconnue pour cette école."})
    return (models.AnneeScolaire.objects
            .filter(id_ecole=ecole, statut=models.AnneeScolaire.Statut.ACTIVE)
            .order_by("-date_debut").first())


def _fichier(request, document, description=None, entite="finances"):
    """
    Renvoie un export en téléchargement, et le trace.

    Un export sort des données nominatives de l'établissement : il a autant sa
    place au journal d'activité qu'une modification.
    """
    contenu, nom, type_mime = document
    if description:
        journaliser(request, models.JournalActivite.Action.EXPORT,
                    entite=entite, description=description)
    reponse = HttpResponse(contenu, content_type=type_mime)
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    reponse["Content-Length"] = str(len(contenu))
    return reponse


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


class FiltreParAnneeMixin:
    """
    Restreint une liste à une année scolaire, via `?id_annee_scolaire=`.

    L'application affiche un sélecteur d'année en tête de page : consulter une
    promotion passée doit être aussi simple que de changer d'année. Toutes les
    listes ne portent pas la colonne — une note dépend de son évaluation, une
    évaluation de sa période — d'où `chemin_annee`, qui nomme la relation à
    suivre pour chaque modèle.

    Le paramètre porte le même nom partout : le client ne se demande pas quelle
    liste attend quel nom. Une valeur illisible est refusée plutôt qu'ignorée,
    sinon l'écran afficherait toute l'école en croyant montrer une année.
    """
    chemin_annee = "id_annee_scolaire"

    def get_queryset(self):
        qs = super().get_queryset()
        brut = self.request.query_params.get("id_annee_scolaire")
        if not brut:
            return qs
        try:
            annee = int(brut)
        except (TypeError, ValueError):
            raise ValidationError({"id_annee_scolaire": "Identifiant d'année attendu."})

        qs = qs.filter(**{self.chemin_annee: annee})
        # Un chemin qui traverse une relation multiple (les inscriptions d'un
        # élève) duplique les lignes : une par inscription correspondante.
        return qs.distinct() if "__" in self.chemin_annee else qs


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


class ClasseViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                    EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Classe.objects.select_related("id_niveau", "id_enseignant_principal").all()
    serializer_class = serializers.ClasseSerializer
    permission_classes = [LectureSeuleOuAdmin]
    filterset_fields = ["id_niveau"]
    search_fields = ["nom", "id_niveau__libelle"]


class ParentTuteurViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.ParentTuteur.objects.all()
    serializer_class = serializers.ParentTuteurSerializer
    permission_classes = [permissions.IsAuthenticated]


class EleveViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                   EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Eleve.objects.all()
    serializer_class = serializers.EleveSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["statut"]
    search_fields = ["nom", "prenom", "matricule"]
    # Un élève n'appartient pas à une année : ce sont ses inscriptions qui la
    # portent. Filtrer par année revient donc à ne garder que les élèves
    # inscrits cette année-là.
    chemin_annee = "inscriptions__id_annee_scolaire"

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
        # L'effectif de la classe est dénormalisé : sans ce recalage, il
        # n'était mis à jour que par l'import de listes.
        scolarite.recaler_effectif(serializer.validated_data["id_classe"])
        journaliser(
            request,
            models.JournalActivite.Action.CREATION,
            entite="eleve",
            id_entite=eleve.pk,
            description=f"Inscription de {eleve.prenom} {eleve.nom} ({eleve.matricule})",
        )
        return Response(serializers.EleveSerializer(eleve).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request={"multipart/form-data": {
            "type": "object",
            "properties": {
                "fichier": {"type": "string", "format": "binary",
                            "description": "Liste des élèves (.xlsx, .csv ou .docx)."},
                "classe": {"type": "string",
                           "description": "Force la classe de tout le fichier. "
                                          "Utile quand l'en-tête ne la mentionne pas."},
                "valider": {"type": "boolean",
                            "description": "false (défaut) : aperçu seul. true : écrit en base."},
            },
            "required": ["fichier"],
        }},
        responses={200: None, 201: None},
    )
    @action(detail=False, methods=["post"], url_path="importer",
            parser_classes=[MultiPartParser, FormParser],
            permission_classes=[permissions.IsAuthenticated, EstAdmin])
    def importer(self, request):
        """
        Import d'une liste d'élèves : POST /api/eleves/importer/

        L'appel se fait deux fois avec le même fichier — d'abord sans
        `valider` pour obtenir l'aperçu, puis avec `valider=true` pour écrire.
        Le fichier est ré-analysé à la validation plutôt que de faire revenir
        le plan par le client : un plan renvoyé par le navigateur serait une
        entrée modifiable, donc un moyen d'écrire des élèves qu'aucun fichier
        ne contenait — ou de forcer une autre école.
        """
        fichier = request.FILES.get("fichier")
        if fichier is None:
            raise ValidationError({"fichier": "Aucun fichier reçu."})
        if fichier.size > TAILLE_MAX_IMPORT:
            raise ValidationError({
                "fichier": f"Fichier trop volumineux (maximum "
                           f"{TAILLE_MAX_IMPORT // (1024 * 1024)} Mo)."
            })

        ecole = request.user.id_ecole
        annee = (models.AnneeScolaire.objects
                 .filter(id_ecole=ecole, statut=models.AnneeScolaire.Statut.ACTIVE)
                 .order_by("-date_debut").first())
        if annee is None:
            raise ValidationError({
                "annee_scolaire": "Aucune année scolaire active : créez-la avant d'importer."
            })

        try:
            lignes = imports_eleves.analyser_fichier(
                fichier.name, fichier.read(), classe_defaut=request.data.get("classe", "").strip(),
            )
        except imports_eleves.ErreurImport as exc:
            raise ValidationError({"fichier": str(exc)})

        plan = imports_eleves.construire_plan(ecole, annee, lignes)
        plan["fichier"] = fichier.name

        if str(request.data.get("valider", "")).lower() not in ("true", "1", "oui"):
            plan["valide"] = False
            return Response(plan)

        if not plan["a_creer"]:
            raise ValidationError({
                "fichier": "Aucun élève à créer : tout le fichier est déjà en base ou illisible."
            })

        resultat = imports_eleves.appliquer_plan(ecole, annee, plan)
        journaliser(
            request,
            models.JournalActivite.Action.CREATION,
            entite="eleve",
            description=f"Import de {resultat['eleves_crees']} élèves depuis {fichier.name}",
        )
        plan.update(valide=True, resultat=resultat)
        return Response(plan, status=status.HTTP_201_CREATED)

    # -- Dossier et mouvements -------------------------------------------
    #
    # Les trois mouvements passent par `scolarite` plutôt que par un PATCH sur
    # l'inscription : eux seuls écrivent la trace au bon type d'événement,
    # recalent l'effectif de la classe quittée et mettent le statut de l'élève
    # en accord avec celui de son inscription.

    def _mouvement(self, request, serializer_class, operation, action_journal,
                   description):
        entree = serializer_class(data=request.data)
        entree.is_valid(raise_exception=True)
        eleve = self.get_object()
        try:
            operation(entree.validated_data, eleve)
        except scolarite.ErreurScolarite as exc:
            raise ValidationError({"detail": str(exc)})
        eleve.refresh_from_db()
        journaliser(request, action_journal, entite="eleve", id_entite=eleve.pk,
                    description=description(eleve, entree.validated_data))
        return Response(scolarite.dossier(request.user.id_ecole, eleve))

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def dossier(self, request, pk=None):
        """
        Dossier numérique complet : GET /api/eleves/{id}/dossier/

        Identité, parents, parcours, pièces, résultats, situation financière
        et assiduité en une réponse — l'écran les affiche ensemble.
        """
        return Response(scolarite.dossier(request.user.id_ecole, self.get_object()))

    def _periode(self, request):
        try:
            return models.PeriodeScolaire.objects.select_related("id_annee_scolaire").get(
                pk=request.query_params.get("id_periode"),
                id_annee_scolaire__id_ecole=request.user.id_ecole)
        except (models.PeriodeScolaire.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"id_periode": "Période inconnue pour cette école."})

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def releve(self, request, pk=None):
        """
        Relevé de notes détaillé : GET /api/eleves/{id}/releve/?id_periode=

        Le bulletin donne les moyennes, le relevé donne les notes qui les
        composent — ce qu'une famille demande quand elle conteste.
        """
        try:
            return Response(pedagogie.releve_de_notes(
                request.user.id_ecole, self.get_object(), self._periode(request)))
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"], url_path="releve/export")
    def exporter_releve(self, request, pk=None):
        """GET /api/eleves/{id}/releve/export/?id_periode="""
        eleve = self.get_object()
        ecole = request.user.id_ecole
        try:
            releve = pedagogie.releve_de_notes(ecole, eleve, self._periode(request))
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})
        return _fichier(
            request,
            pedagogie_documents.releve_de_notes(ecole, releve),
            description=f"Relevé de notes de {eleve.prenom} {eleve.nom}",
            entite="note",
        )

    @extend_schema(request=serializers.ChangementClasseSerializer, responses=dict)
    @action(detail=True, methods=["post"], url_path="changer-classe",
            permission_classes=[permissions.IsAuthenticated, EstAdmin])
    def changer_classe(self, request, pk=None):
        """Changement de classe : POST /api/eleves/{id}/changer-classe/"""
        return self._mouvement(
            request, serializers.ChangementClasseSerializer,
            lambda d, eleve: scolarite.changer_de_classe(
                request.user.id_ecole, eleve, d["id_classe"],
                date_effet=d.get("date_effet"), motif=d.get("motif", ""),
                commentaire=d.get("commentaire", "")),
            models.JournalActivite.Action.MODIFICATION,
            lambda eleve, d: f"Changement de classe de {eleve.prenom} {eleve.nom} "
                             f"vers {d['id_classe'].nom}",
        )

    @extend_schema(request=serializers.RadiationSerializer, responses=dict)
    @action(detail=True, methods=["post"],
            permission_classes=[permissions.IsAuthenticated, EstAdmin])
    def radier(self, request, pk=None):
        """Radiation : POST /api/eleves/{id}/radier/"""
        return self._mouvement(
            request, serializers.RadiationSerializer,
            lambda d, eleve: scolarite.radier(
                request.user.id_ecole, eleve, date_effet=d.get("date_effet"),
                motif=d["motif"], commentaire=d.get("commentaire", "")),
            models.JournalActivite.Action.MODIFICATION,
            lambda eleve, d: f"Radiation de {eleve.prenom} {eleve.nom} — {d['motif']}",
        )

    @extend_schema(request=serializers.TransfertSerializer, responses=dict)
    @action(detail=True, methods=["post"],
            permission_classes=[permissions.IsAuthenticated, EstAdmin])
    def transferer(self, request, pk=None):
        """Transfert vers un autre établissement : POST /api/eleves/{id}/transferer/"""
        return self._mouvement(
            request, serializers.TransfertSerializer,
            lambda d, eleve: scolarite.transferer(
                request.user.id_ecole, eleve, d["etablissement"],
                date_effet=d.get("date_effet"), commentaire=d.get("commentaire", "")),
            models.JournalActivite.Action.MODIFICATION,
            lambda eleve, d: f"Transfert de {eleve.prenom} {eleve.nom} "
                             f"vers {d['etablissement']}",
        )


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
    def perform_create(self, serializer):
        super().perform_create(serializer)
        scolarite.recaler_effectif(serializer.instance.id_classe)

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
        # Le statut peut aussi avoir changé (annulation) : on recale dans tous
        # les cas, l'effectif dénormalisé se lit sur le tableau de bord.
        scolarite.recaler_effectif(classe_avant, inscription.id_classe)

    def perform_destroy(self, instance):
        classe = instance.id_classe
        super().perform_destroy(instance)
        scolarite.recaler_effectif(classe)

    @extend_schema(request=serializers.ReinscriptionSerializer, responses=dict)
    @action(detail=False, methods=["post"], permission_classes=[EstAdmin])
    def reinscrire(self, request):
        """
        Réinscription d'une promotion : POST /api/inscriptions/reinscrire/

        Comme la génération des frais et l'import d'élèves, l'appel se fait
        deux fois — d'abord sans `valider` pour voir combien d'élèves
        passeraient et dans quelle classe, puis avec `valider=true` pour
        écrire. Réinscrire une classe entière ne se défait pas à la main.
        """
        entree = serializers.ReinscriptionSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data
        valider = donnees["valider"]

        try:
            resultat = scolarite.reinscrire(
                request.user.id_ecole,
                donnees["id_annee_source"],
                donnees["id_annee_cible"],
                donnees["id_classe_cible"],
                classe_source=donnees.get("id_classe_source"),
                date_inscription=donnees.get("date_inscription"),
                appliquer=valider,
            )
        except scolarite.ErreurScolarite as exc:
            raise ValidationError({"detail": str(exc)})

        if valider:
            journaliser(
                request, models.JournalActivite.Action.CREATION, entite="inscription",
                description=f"Réinscription de {resultat['reinscrits']} élèves "
                            f"({resultat['classe_source']} → {resultat['classe_cible']})",
            )
        statut = status.HTTP_201_CREATED if valider else status.HTTP_200_OK
        return Response(resultat, status=statut)


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


class ConseilClasseViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                           viewsets.ModelViewSet):
    queryset = models.ConseilClasse.objects.select_related("id_classe", "id_periode").all()
    serializer_class = serializers.ConseilClasseSerializer
    permission_classes = [EstAdminOuEnseignant]
    filterset_fields = ["id_classe", "id_periode"]
    chemin_annee = "id_periode__id_annee_scolaire"

    def get_queryset(self):
        return super().get_queryset().filter(
            id_classe__id_ecole_id=self.request.user.id_ecole_id
        )


class EvaluationViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                        viewsets.ModelViewSet):
    queryset = models.Evaluation.objects.select_related(
        "id_matiere", "id_classe", "id_periode").all()
    serializer_class = serializers.EvaluationSerializer
    permission_classes = [EstAdminOuEnseignant]
    filterset_fields = ["id_classe", "id_matiere", "id_periode", "type"]
    chemin_annee = "id_periode__id_annee_scolaire"

    def get_queryset(self):
        return super().get_queryset().filter(id_classe__id_ecole_id=self.request.user.id_ecole_id)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def grille(self, request, pk=None):
        """
        Grille de saisie préremplie : GET /api/evaluations/{id}/grille/

        Tous les inscrits de la classe, notés ou non — sans quoi l'enseignant
        devrait deviner qui reste à saisir.
        """
        try:
            return Response(pedagogie.grille_de_saisie(
                request.user.id_ecole, self.get_object()))
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(request=serializers.SaisieNotesSerializer, responses=dict)
    @action(detail=True, methods=["post"], url_path="notes")
    def saisir_notes(self, request, pk=None):
        """
        Saisie en masse : POST /api/evaluations/{id}/notes/

        Transactionnel : une note hors barème et rien n'est écrit, plutôt
        qu'une grille à moitié saisie.
        """
        entree = serializers.SaisieNotesSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        evaluation = self.get_object()
        try:
            resultat = pedagogie.saisir_notes(
                request.user.id_ecole, evaluation,
                entree.validated_data["notes"], request.user)
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

        journaliser(
            request, models.JournalActivite.Action.MODIFICATION, entite="note",
            id_entite=evaluation.pk,
            description=f"Saisie de {resultat['creees'] + resultat['modifiees']} notes "
                        f"({resultat['matiere']}, {resultat['classe']})",
        )
        return Response(resultat)


class NoteViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Note.objects.all()
    serializer_class = serializers.NoteSerializer
    permission_classes = [EstAdminOuEnseignant]
    filterset_fields = ["id_eleve", "id_evaluation"]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class BulletinViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                      viewsets.ModelViewSet):
    queryset = models.Bulletin.objects.select_related(
        "id_eleve", "id_classe", "id_periode").all()
    serializer_class = serializers.BulletinSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["id_eleve", "id_classe", "id_periode"]
    chemin_annee = "id_periode__id_annee_scolaire"

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)

    # -- Contexte commun aux vues de calcul -------------------------------

    def _classe_et_periode(self, request):
        """Lit `id_classe` et `id_periode` de l'URL, en refusant l'à-peu-près."""
        ecole = request.user.id_ecole
        try:
            classe = models.Classe.objects.get(
                pk=request.query_params.get("id_classe"), id_ecole=ecole)
        except (models.Classe.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"id_classe": "Classe inconnue pour cette école."})
        try:
            periode = models.PeriodeScolaire.objects.select_related("id_annee_scolaire").get(
                pk=request.query_params.get("id_periode"),
                id_annee_scolaire__id_ecole=ecole)
        except (models.PeriodeScolaire.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"id_periode": "Période inconnue pour cette école."})
        return ecole, classe, periode

    @extend_schema(request=serializers.GenerationBulletinsSerializer, responses=dict)
    @action(detail=False, methods=["post"], permission_classes=[EstAdminOuEnseignant])
    def generer(self, request):
        """
        Génération des bulletins d'une classe : POST /api/bulletins/generer/

        Comme la génération des frais, l'appel se fait deux fois : sans
        `valider` pour l'aperçu des moyennes, puis avec pour écrire.
        Régénérer met à jour les bulletins existants — une note corrigée
        après coup doit pouvoir se répercuter.
        """
        entree = serializers.GenerationBulletinsSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data
        valider = donnees["valider"]

        try:
            resultat = pedagogie.generer_bulletins(
                request.user.id_ecole, donnees["id_classe"], donnees["id_periode"],
                appliquer=valider)
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

        if valider:
            journaliser(
                request, models.JournalActivite.Action.CREATION, entite="bulletin",
                description=f"Génération de {resultat['crees'] + resultat['mis_a_jour']} "
                            f"bulletins ({resultat['classe']}, {resultat['periode']})",
            )
        statut = status.HTTP_201_CREATED if valider else status.HTTP_200_OK
        return Response(resultat, status=statut)

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def moyennes(self, request):
        """
        Tableau des moyennes d'une classe :
        GET /api/bulletins/moyennes/?id_classe=&id_periode=

        Calculé à la volée, sans écrire de bulletin : c'est la vue de travail
        avant le conseil de classe.
        """
        ecole, classe, periode = self._classe_et_periode(request)
        try:
            return Response({
                "classe": classe.nom,
                "periode": periode.libelle,
                "lignes": pedagogie.moyennes_classe(ecole, classe, periode),
            })
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="moyennes/export")
    def exporter_moyennes(self, request):
        """GET /api/bulletins/moyennes/export/?id_classe=&id_periode=&format_export=xlsx|pdf"""
        ecole, classe, periode = self._classe_et_periode(request)
        try:
            resultat = pedagogie.generer_bulletins(ecole, classe, periode, appliquer=False)
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})
        return _fichier(
            request,
            pedagogie_documents.moyennes_classe(ecole, resultat, format=_format_export(request)),
            description=f"Export des moyennes ({classe.nom}, {periode.libelle})",
            entite="bulletin",
        )

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def palmares(self, request):
        """GET /api/bulletins/palmares/?id_classe=&id_periode=&limite="""
        ecole, classe, periode = self._classe_et_periode(request)
        limite = request.query_params.get("limite")
        try:
            limite = int(limite) if limite else None
        except ValueError:
            raise ValidationError({"limite": "Nombre entier attendu."})
        try:
            return Response(pedagogie.palmares(ecole, classe, periode, limite=limite))
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="palmares/export")
    def exporter_palmares(self, request):
        """GET /api/bulletins/palmares/export/?id_classe=&id_periode=&format_export=xlsx|pdf"""
        ecole, classe, periode = self._classe_et_periode(request)
        try:
            donnees = pedagogie.palmares(ecole, classe, periode)
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})
        return _fichier(
            request,
            pedagogie_documents.palmares(ecole, donnees, format=_format_export(request)),
            description=f"Export du palmarès ({classe.nom}, {periode.libelle})",
            entite="bulletin",
        )

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def statistiques(self, request):
        """GET /api/bulletins/statistiques/?id_classe=&id_periode="""
        ecole, classe, periode = self._classe_et_periode(request)
        try:
            return Response(pedagogie.statistiques(ecole, classe, periode))
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def synthese(self, request):
        """
        Résultats de toute l'école : GET /api/bulletins/synthese/

        Sans paramètre, la réponse porte sur la période en cours de l'année
        active — c'est la lecture attendue quand un directeur ouvre son
        tableau de bord. `id_periode` recadre sur une autre période,
        `id_annee_scolaire` sur une autre année.
        """
        ecole = request.user.id_ecole

        brut = request.query_params.get("id_periode")
        if brut:
            try:
                periode = models.PeriodeScolaire.objects.select_related(
                    "id_annee_scolaire").get(
                        pk=brut, id_annee_scolaire__id_ecole=ecole)
            except (models.PeriodeScolaire.DoesNotExist, ValueError, TypeError):
                raise ValidationError({"id_periode": "Période inconnue pour cette école."})
        else:
            annee = None
            brut_annee = request.query_params.get("id_annee_scolaire")
            if brut_annee:
                try:
                    annee = models.AnneeScolaire.objects.get(pk=brut_annee, id_ecole=ecole)
                except (models.AnneeScolaire.DoesNotExist, ValueError, TypeError):
                    raise ValidationError({
                        "id_annee_scolaire": "Année inconnue pour cette école."})
            periode = pedagogie.periode_courante(ecole, annee)

        # Pas de période créée : le tableau de bord doit l'annoncer plutôt que
        # d'échouer sur une école qui démarre.
        if periode is None:
            return Response({
                "periode": None,
                "detail": "Aucune période scolaire définie pour cette année.",
            })

        try:
            return Response(pedagogie.synthese(ecole, periode))
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """Bulletin imprimable : GET /api/bulletins/{id}/pdf/"""
        bulletin = self.get_object()
        ecole = request.user.id_ecole
        try:
            releve = pedagogie.releve_de_notes(ecole, bulletin.id_eleve, bulletin.id_periode)
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

        conseil = (models.ConseilClasse.objects
                   .filter(id_classe=bulletin.id_classe, id_periode=bulletin.id_periode)
                   .order_by("-date_conseil").first())
        return _fichier(
            request,
            pedagogie_documents.bulletin(ecole, releve,
                                         conseil=conseil.decisions if conseil else None),
            description=f"Bulletin de {bulletin.id_eleve.prenom} {bulletin.id_eleve.nom} "
                        f"({bulletin.id_periode.libelle})",
            entite="bulletin",
        )


class AbsenceViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                     viewsets.ModelViewSet):
    queryset = models.Absence.objects.all()
    serializer_class = serializers.AbsenceSerializer
    permission_classes = [EstAdminOuEnseignant]
    filterset_fields = ["id_eleve", "id_classe", "statut", "justifiee"]
    chemin_annee = "id_classe__id_annee_scolaire"

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def synthese(self, request):
        """
        Assiduité récente : GET /api/absences/synthese/?jours=30

        Sur une fenêtre glissante et non sur l'année entière : une absence de
        novembre ne dit rien d'utile en juin.
        """
        ecole = request.user.id_ecole
        annee = _annee_demandee(request, ecole)
        if annee is None:
            return Response({"annee": None, "classes": [], "eleves": [],
                             "detail": "Aucune année scolaire active."})
        try:
            jours = int(request.query_params.get("jours") or 30)
        except ValueError:
            raise ValidationError({"jours": "Nombre de jours attendu."})
        try:
            return Response(pedagogie.assiduite(ecole, annee, jours=jours))
        except pedagogie.ErreurPedagogie as exc:
            raise ValidationError({"detail": str(exc)})

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


# ---------- Emploi du temps ----------

class SalleViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Salle.objects.all()
    serializer_class = serializers.SalleSerializer
    permission_classes = [LectureSeuleOuAdmin]


class EmploiDuTempsViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                           viewsets.ModelViewSet):
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


class FraisEleveViewSet(CoherenceEcoleMixin, JournalisationMixin, FiltreParAnneeMixin,
                        viewsets.ModelViewSet):
    queryset = models.FraisEleve.objects.select_related(
        "id_eleve", "id_type_frais", "id_annee_scolaire").all()
    serializer_class = serializers.FraisEleveSerializer
    permission_classes = [EstAdminOuCaissier]

    def get_queryset(self):
        qs = super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)
        params = self.request.query_params
        for parametre, champ in (("eleve", "id_eleve"), ("statut", "statut"),
                                 ("type_frais", "id_type_frais"),
                                 ("annee", "id_annee_scolaire")):
            valeur = params.get(parametre)
            if valeur:
                qs = qs.filter(**{champ: valeur})
        if params.get("classe"):
            qs = qs.filter(id_eleve__inscriptions__id_classe=params["classe"])
        if str(params.get("echus", "")).lower() in ("true", "1", "oui"):
            qs = qs.filter(echeance__lt=timezone.localdate())
        return qs.distinct()

    def _impayes(self):
        return self.get_queryset().exclude(statut=models.FraisEleve.Statut.PAYE)

    @action(detail=False, methods=["get"])
    def impayes(self, request):
        """GET /api/frais-eleves/impayes/ — écran Finances (admin) et parcours caissier."""
        qs = self._impayes()
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page or qs, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @extend_schema(request=serializers.GenerationFraisSerializer, responses=dict)
    @action(detail=False, methods=["post"], permission_classes=[EstAdmin])
    def generer(self, request):
        """
        Génération en masse des frais : POST /api/frais-eleves/generer/

        Comme l'import d'élèves, l'appel se fait deux fois — d'abord sans
        `valider` pour obtenir l'aperçu (combien de frais seraient créés, pour
        quel total), puis avec `valider=true` pour écrire. Facturer une année
        entière est irréversible à la main : mieux vaut voir le nombre avant.
        """
        entree = serializers.GenerationFraisSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data
        valider = donnees["valider"]

        try:
            resultat = finances.generer_frais(
                request.user.id_ecole,
                donnees["id_annee_scolaire"],
                donnees["id_type_frais"],
                donnees["echeances"],
                montant=donnees.get("montant"),
                classe=donnees.get("id_classe"),
                appliquer=valider,
            )
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

        if valider:
            journaliser(
                request, models.JournalActivite.Action.CREATION, entite="frais_eleve",
                description=f"Génération de {resultat['crees']} frais "
                            f"({resultat['type_frais']}, {resultat['classe']})",
            )
        statut = status.HTTP_201_CREATED if valider else status.HTTP_200_OK
        return Response(resultat, status=statut)

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="impayes/par-classe")
    def impayes_par_classe(self, request):
        """
        Reste à recouvrer par classe :
        GET /api/frais-eleves/impayes/par-classe/?id_annee_scolaire=

        Un montant global ne dit pas où aller chercher ; la classe donne la
        liste d'appel et le professeur principal par qui passer.
        """
        ecole = request.user.id_ecole
        annee = _annee_demandee(request, ecole)
        if annee is None:
            return Response({"annee": None, "classes": [], "total_reste": 0,
                             "detail": "Aucune année scolaire active."})
        try:
            return Response(finances.impayes_par_classe(ecole, annee))
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

    @action(detail=False, methods=["get"], url_path="impayes/export")
    def exporter_impayes(self, request):
        """GET /api/frais-eleves/impayes/export/?format=xlsx|pdf"""
        return _fichier(
            request,
            finances_documents.impayes(
                request.user.id_ecole,
                self._impayes().select_related("id_eleve", "id_type_frais"),
                format=_format_export(request),
            ),
            description="Export de l'état des impayés",
        )


class PaiementViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """
    Cœur du parcours caissier : POST /api/paiements/ encaisse un paiement,
    met à jour le frais concerné, génère le reçu et le mouvement de caisse
    automatiquement (voir PaiementSerializer.create).
    """
    queryset = models.Paiement.objects.select_related(
        "id_frais_eleve", "id_frais_eleve__id_eleve", "id_frais_eleve__id_type_frais",
        "id_utilisateur_caissier", "recu").all()
    serializer_class = serializers.PaiementSerializer
    permission_classes = [EstAdminOuCaissier]

    def get_queryset(self):
        qs = super().get_queryset().filter(
            id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)
        params = self.request.query_params
        if params.get("methode"):
            qs = qs.filter(methode=params["methode"])
        if params.get("debut"):
            qs = qs.filter(date_paiement__date__gte=params["debut"])
        if params.get("fin"):
            qs = qs.filter(date_paiement__date__lte=params["fin"])
        return qs

    @action(detail=False, methods=["get"], url_path="export")
    def exporter(self, request):
        """GET /api/paiements/export/?format=xlsx|pdf — journal des recettes."""
        return _fichier(
            request,
            finances_documents.paiements(request.user.id_ecole, self.get_queryset(),
                                         format=_format_export(request)),
            description="Export du journal des recettes",
        )


class RecuViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Recu.objects.select_related(
        "id_paiement", "id_paiement__id_frais_eleve",
        "id_paiement__id_frais_eleve__id_eleve").all()
    serializer_class = serializers.RecuSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(id_paiement__id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        """GET /api/recus/{id}/pdf/ — le justificatif remis à la famille."""
        return _fichier(request, finances_documents.recu(self.get_object()),
                        description=f"Édition du reçu {self.get_object().numero_recu}")


class RelanceViewSet(CoherenceEcoleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Relance.objects.select_related(
        "id_frais_eleve", "id_frais_eleve__id_eleve", "id_frais_eleve__id_type_frais").all()
    serializer_class = serializers.RelanceSerializer
    permission_classes = [EstAdminOuCaissier]

    def get_queryset(self):
        return super().get_queryset().filter(id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)

    @extend_schema(request=serializers.GenerationRelancesSerializer, responses=dict)
    @action(detail=False, methods=["post"])
    def generer(self, request):
        """
        Relances automatiques : POST /api/relances/generer/

        Sans `valider`, l'appel ne fait que compter les familles concernées —
        on voit qui serait relancé avant d'envoyer quoi que ce soit.
        """
        entree = serializers.GenerationRelancesSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data

        try:
            resultat = finances.generer_relances(
                request.user.id_ecole,
                donnees["canal"],
                jours_retard=donnees["jours_retard"],
                delai_min=donnees["delai_min"],
                appliquer=donnees["valider"],
            )
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

        if donnees["valider"]:
            journaliser(
                request, models.JournalActivite.Action.CREATION, entite="relance",
                description=f"{resultat['relances']} relances envoyées par {resultat['canal']}",
            )
        statut = status.HTTP_201_CREATED if donnees["valider"] else status.HTTP_200_OK
        return Response(resultat, status=statut)


class TypeDepenseViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.TypeDepense.objects.all()
    serializer_class = serializers.TypeDepenseSerializer
    permission_classes = [EstAdmin]


class DepenseViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    """
    Une dépense n'est pas qu'une ligne de saisie : elle sort de la caisse.
    Création, modification et suppression tiennent donc le journal et le
    budget à jour, sinon le bilan afficherait des recettes sans les charges.
    """
    queryset = models.Depense.objects.select_related("id_type_depense").all()
    serializer_class = serializers.DepenseSerializer
    permission_classes = [EstAdmin]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("debut"):
            qs = qs.filter(date_depense__gte=params["debut"])
        if params.get("fin"):
            qs = qs.filter(date_depense__lte=params["fin"])
        if params.get("type_depense"):
            qs = qs.filter(id_type_depense=params["type_depense"])
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        finances.enregistrer_depense(serializer.instance, utilisateur=self.request.user)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        depense = serializer.instance
        mouvements = models.JournalCaisse.objects.filter(id_depense=depense)
        mouvements.update(
            date_mouvement=depense.date_depense,
            debit=depense.montant,
            libelle=f"Dépense {depense.id_type_depense.libelle} — {depense.motif}"[:255],
        )
        finances.imputer_au_budget(depense)

    def perform_destroy(self, instance):
        # Capturés avant la suppression : ensuite la dépense n'existe plus et
        # le budget ne saurait plus quelles lignes recompter.
        temoin = models.Depense(id_ecole=instance.id_ecole,
                                id_type_depense=instance.id_type_depense,
                                date_depense=instance.date_depense)
        models.JournalCaisse.objects.filter(id_depense=instance).delete()
        super().perform_destroy(instance)
        finances.imputer_au_budget(temoin)

    @action(detail=False, methods=["get"], url_path="export")
    def exporter(self, request):
        """GET /api/depenses/export/?format=xlsx|pdf"""
        return _fichier(
            request,
            finances_documents.depenses(request.user.id_ecole, self.get_queryset(),
                                        format=_format_export(request)),
            description="Export des dépenses",
        )


class BudgetViewSet(CoherenceEcoleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Budget.objects.select_related("id_type_depense", "id_annee_scolaire").all()
    serializer_class = serializers.BudgetSerializer
    permission_classes = [EstAdmin]

    def perform_create(self, serializer):
        super().perform_create(serializer)
        # Un budget créé après coup doit refléter les dépenses déjà engagées.
        budget = serializer.instance
        finances.imputer_au_budget(models.Depense(
            id_ecole=budget.id_ecole, id_type_depense=budget.id_type_depense,
            date_depense=budget.id_annee_scolaire.date_debut))


class JournalCaisseViewSet(EcoleFiltreeMixin, viewsets.ReadOnlyModelViewSet):
    queryset = models.JournalCaisse.objects.select_related("id_utilisateur").order_by(
        "-date_mouvement", "-id")
    serializer_class = serializers.JournalCaisseSerializer
    permission_classes = [EstAdminOuCaissier]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("journal"):
            qs = qs.filter(type_journal=params["journal"])
        if params.get("debut"):
            qs = qs.filter(date_mouvement__gte=params["debut"])
        if params.get("fin"):
            qs = qs.filter(date_mouvement__lte=params["fin"])
        return qs

    @action(detail=False, methods=["get"])
    def soldes(self, request):
        """GET /api/journal-caisse/soldes/ — bandeau caisse / banque."""
        ecole = request.user.id_ecole
        return Response({
            "caisse": finances.montant_json(
                finances.solde(ecole, models.JournalCaisse.TypeJournal.CAISSE)),
            "banque": finances.montant_json(
                finances.solde(ecole, models.JournalCaisse.TypeJournal.BANQUE)),
            "total": finances.montant_json(finances.solde(ecole)),
        })

    @action(detail=False, methods=["get"], url_path="export")
    def exporter(self, request):
        """GET /api/journal-caisse/export/?format=xlsx|pdf&journal=caisse|banque"""
        journal = request.query_params.get("journal")
        titre = {"caisse": "Journal de caisse", "banque": "Journal de banque"}.get(
            journal, "Journal de caisse et de banque")
        return _fichier(
            request,
            finances_documents.journal(request.user.id_ecole,
                                       self.get_queryset().order_by("date_mouvement", "id"),
                                       format=_format_export(request), titre=titre),
            description=f"Export du {titre.lower()}",
        )


# ---------- Bilan financier ----------

class BilanFinancierView(APIView):
    """
    GET /api/finances/bilan/ — la synthèse « Budget, Dépenses & Bilan » du TDR.

    Sans paramètre, le bilan porte sur l'année scolaire active : c'est la
    lecture attendue quand un directeur ouvre l'écran. `annee`, `debut` et
    `fin` permettent de recadrer, et `format_export=xlsx|pdf` de récupérer le
    même contenu en fichier plutôt qu'en JSON.
    """
    permission_classes = [EstAdminOuCaissier]

    def _contexte(self, request):
        ecole = request.user.id_ecole
        annee = None
        id_annee = request.query_params.get("annee")
        if id_annee:
            annee = models.AnneeScolaire.objects.filter(
                pk=id_annee, id_ecole=ecole).first()
            if annee is None:
                raise ValidationError({"annee": "Année scolaire inconnue pour cette école."})
        elif not request.query_params.get("debut") and not request.query_params.get("fin"):
            annee = (models.AnneeScolaire.objects
                     .filter(id_ecole=ecole, statut=models.AnneeScolaire.Statut.ACTIVE)
                     .order_by("-date_debut").first())

        return ecole, annee, _date_param(request, "debut"), _date_param(request, "fin")

    @extend_schema(responses=dict, summary="Bilan financier", tags=["finances"])
    def get(self, request):
        ecole, annee, debut, fin = self._contexte(request)
        donnees = finances.bilan(ecole, annee=annee, debut=debut, fin=fin)

        if request.query_params.get(PARAM_FORMAT):
            return _fichier(
                request,
                finances_documents.bilan(ecole, donnees, format=_format_export(request)),
                description="Export du bilan financier",
            )
        return Response(donnees)


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
