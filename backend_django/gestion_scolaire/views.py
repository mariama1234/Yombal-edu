from rest_framework import mixins, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models import Model as DjangoModel
from django.db.models.deletion import ProtectedError, RestrictedError
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.crypto import get_random_string
from datetime import date as date_type

from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema

from . import (corbeille, finances, finances_documents, imports_eleves, models, pedagogie,
               pedagogie_documents, photos, scolarite, serializers)
from .audit import JournalisationMixin, journaliser
from .permissions import (ECRITURE, AccesModule, EstAdmin, Profil, acces_du_compte,
                          classes_de_lenseignant, eleves_de_la_famille, est_direction,
                          peut_lire, profil_de)

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


def _reference(modele, brut, ecole, nom):
    """
    Résout un identifiant passé dans l'URL, en refusant ce qui n'est pas de
    l'école appelante.

    Un identifiant inconnu est refusé plutôt qu'ignoré : filtrer sur une classe
    d'une autre école doit dire « inconnue », pas afficher toute l'école.
    """
    if not brut:
        return None
    try:
        return modele.objects.get(pk=brut, id_ecole=ecole)
    except (modele.DoesNotExist, ValueError, TypeError):
        raise ValidationError({nom: f"{nom.capitalize()} inconnu(e) pour cette école."})


def _pagination(request, taille_defaut=20, taille_max=200):
    """Lit `?page=` et `?page_size=`, en refusant les valeurs illisibles."""
    def entier(nom, defaut):
        brut = request.query_params.get(nom)
        if not brut:
            return defaut
        try:
            valeur = int(brut)
        except (TypeError, ValueError):
            raise ValidationError({nom: "Nombre entier attendu."})
        if valeur < 1:
            raise ValidationError({nom: "Valeur minimale : 1."})
        return valeur

    return entier("page", 1), min(entier("page_size", taille_defaut), taille_max)


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


def _annee_de_leleve(request, ecole, eleve):
    """
    Année d'une facture : celle de l'URL, sinon celle de la dernière
    inscription de l'élève, sinon l'année active.
    """
    if request.query_params.get("id_annee_scolaire"):
        return _annee_demandee(request, ecole)
    inscription = (models.Inscription.objects
                   .filter(id_eleve=eleve)
                   .exclude(statut=models.Inscription.Statut.ANNULEE)
                   .select_related("id_annee_scolaire")
                   .order_by("-id_annee_scolaire__date_debut").first())
    return inscription.id_annee_scolaire if inscription else _annee_demandee(request, ecole)


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


class PerimetreMixin:
    """
    Ce qu'un compte voit dans un module qu'il a le droit d'ouvrir.

    La matrice dit « un parent lit les notes » ; reste à dire lesquelles. Un
    parent ne voit que ses enfants, un élève que lui-même : `perimetre_famille`
    nomme le chemin de la ligne vers l'élève. `famille_exclue` ferme la liste
    aux familles quand elle ne porte pas sur un élève en particulier (un
    conseil de classe). Un enseignant ne voit et ne saisit que dans ses
    classes : `perimetre_enseignant` nomme le chemin vers la classe.

    Placé en tête des bases : ses `perform_*` vérifient avant tous les autres.
    """
    perimetre_famille = None
    famille_exclue = False
    perimetre_enseignant = None

    def get_queryset(self):
        qs = super().get_queryset()
        utilisateur = self.request.user
        if getattr(self, "swagger_fake_view", False) or not utilisateur.is_authenticated:
            return qs
        eleves = eleves_de_la_famille(utilisateur)
        if eleves is not None:
            if self.famille_exclue:
                return qs.none()
            if self.perimetre_famille:
                qs = qs.filter(**{f"{self.perimetre_famille}__in": eleves.values("pk")}).distinct()
        classes = classes_de_lenseignant(utilisateur)
        if classes is not None and self.perimetre_enseignant:
            qs = qs.filter(**{f"{self.perimetre_enseignant}__in": classes.values("pk")}).distinct()
        return qs

    def _verifier_perimetre(self, serializer):
        """Un enseignant n'écrit que dans ses classes."""
        classes = classes_de_lenseignant(self.request.user)
        if classes is None or not self.perimetre_enseignant:
            return
        donnees = serializer.validated_data
        classe = donnees.get("id_classe")
        if classe is None and donnees.get("id_evaluation") is not None:
            classe = donnees["id_evaluation"].id_classe
        if classe is not None and not classes.filter(pk=classe.pk).exists():
            raise PermissionDenied("Cette classe ne fait pas partie des vôtres.")

    def perform_create(self, serializer):
        self._verifier_perimetre(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._verifier_perimetre(serializer)
        super().perform_update(serializer)


def _classe_de_lenseignant_ou_refus(utilisateur, classe):
    """Refuse (403) à un enseignant une classe qui n'est pas la sienne."""
    classes = classes_de_lenseignant(utilisateur)
    if classes is not None and not classes.filter(pk=classe.pk).exists():
        raise PermissionDenied("Cette classe ne fait pas partie des vôtres.")


class CreationAccesMixin:
    """
    « Créer son accès » : le compte de connexion d'une fiche.

    Un compte parent, élève ou enseignant ne sert à rien tant qu'il n'est pas
    rattaché à sa fiche — sans ce lien, un parent ne voit aucun enfant et un
    enseignant aucune classe. Créer le compte et le rattacher sont donc une
    seule opération, réservée à la direction comme la gestion des comptes.

    La vue déclare le rôle donné au compte (`role_acces`).
    """
    role_acces = None

    def identite_pour_acces(self, fiche):
        """(nom, prénom, e-mail, téléphone) portés par la fiche, à défaut vides."""
        return (getattr(fiche, "nom", "") or "", getattr(fiche, "prenom", "") or "",
                getattr(fiche, "email", "") or "", getattr(fiche, "telephone", "") or "")

    def fiche_pour_acces(self, request, pk):
        return self.get_object()

    def libelle_de_la_fiche(self, fiche):
        return str(fiche)

    @extend_schema(request=serializers.CreationAccesSerializer, responses=dict)
    @action(detail=True, methods=["post"], url_path="creer-acces")
    def creer_acces(self, request, pk=None):
        """
        Compte de connexion d'une fiche : POST /api/<fiches>/{id}/creer-acces/

        Renvoie le mot de passe provisoire — c'est la seule fois où il est
        lisible, ensuite il n'est plus stocké qu'en empreinte.
        """
        fiche = self.fiche_pour_acces(request, pk)
        if fiche is None:
            return Response({"detail": "Fiche introuvable."}, status=status.HTTP_404_NOT_FOUND)
        if fiche.id_utilisateur_id is not None:
            raise ValidationError({
                "detail": f"Cette fiche a déjà un accès : {fiche.id_utilisateur.email}."})

        entree = serializers.CreationAccesSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data
        nom_fiche, prenom_fiche, email_fiche, telephone_fiche = self.identite_pour_acces(fiche)

        email = (donnees.get("email") or email_fiche).strip().lower()
        nom = (donnees.get("nom") or nom_fiche).strip()
        prenom = (donnees.get("prenom") or prenom_fiche).strip()
        if not email:
            raise ValidationError({"email": "Indiquez l'adresse e-mail de connexion."})
        if not nom:
            raise ValidationError({"nom": "Indiquez le nom de la personne."})
        if Utilisateur.objects.filter(email__iexact=email).exists():
            raise ValidationError({"email": "Un compte utilise déjà cette adresse e-mail."})

        # Tiré au sort sans les caractères qu'on confond en le recopiant (0/O, 1/l).
        mot_de_passe = donnees.get("mot_de_passe") or get_random_string(
            10, "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789")

        with transaction.atomic():
            compte = Utilisateur.objects.create_user(
                email=email, id_ecole=request.user.id_ecole, nom=nom, prenom=prenom,
                role=self.role_acces, password=mot_de_passe,
                telephone=(donnees.get("telephone") or telephone_fiche or None),
            )
            fiche.id_utilisateur = compte
            fiche.save(update_fields=["id_utilisateur"])

        libelle = self.libelle_de_la_fiche(fiche)
        journaliser(request, models.JournalActivite.Action.CREATION, entite="utilisateur",
                    id_entite=compte.pk,
                    description=f"Création de l'accès {compte.get_role_display().lower()} "
                                f"de {libelle} ({email})")
        return Response({
            "id_utilisateur": compte.pk,
            "email": compte.email,
            "role": compte.role,
            "role_libelle": compte.get_role_display(),
            "nom_complet": f"{prenom} {nom}".strip(),
            # Affiché une seule fois : à transmettre, puis à changer.
            "mot_de_passe_provisoire": mot_de_passe,
            "detail": f"Accès créé pour {libelle}. Identifiant : {email} — "
                      f"mot de passe provisoire : {mot_de_passe}",
        }, status=status.HTTP_201_CREATED)


class CorbeilleMixin:
    """
    Toute suppression passe par la corbeille.

    Placé avant `JournalisationMixin` : l'instantané, la suppression, son
    entrée au journal et la ligne de corbeille tiennent dans une seule
    transaction — une corbeille qui échoue ne doit pas laisser une suppression
    sans retour possible.
    """

    def perform_destroy(self, instance):
        with transaction.atomic():
            try:
                entree = corbeille.instantane(instance)
            except (ProtectedError, RestrictedError) as exc:
                # Sans ce rattrapage, la suppression refusée remontait en 500.
                raise ValidationError({"detail": corbeille.message_blocage(exc)})
            super().perform_destroy(instance)
            entree.id_ecole_id = self.request.user.id_ecole_id
            entree.id_utilisateur = self.request.user
            entree.save()


# ---------- Comptes ----------

class UtilisateurViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
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

class EcoleViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
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
    permission_classes = [AccesModule]
    module = "parametres"

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return self.queryset.none()
        return self.queryset.filter(pk=self.request.user.id_ecole_id)


# ---------- Module élèves ----------

class AnneeScolaireViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.AnneeScolaire.objects.all()
    serializer_class = serializers.AnneeScolaireSerializer
    permission_classes = [AccesModule]
    module = "annees_scolaires"


class PeriodeScolaireViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Trimestres ou semestres. Isolés via l'année scolaire, qui porte l'école."""
    queryset = models.PeriodeScolaire.objects.select_related("id_annee_scolaire").all()
    serializer_class = serializers.PeriodeScolaireSerializer
    permission_classes = [AccesModule]
    module = "annees_scolaires"
    filterset_fields = ["id_annee_scolaire"]

    def get_queryset(self):
        return super().get_queryset().filter(
            id_annee_scolaire__id_ecole_id=self.request.user.id_ecole_id
        ).order_by("date_debut")


class NiveauViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Niveau.objects.prefetch_related("tarifs").all()
    serializer_class = serializers.NiveauSerializer
    permission_classes = [AccesModule]
    module = "classes"


class ClasseViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                    EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Classe.objects.select_related("id_niveau", "id_enseignant_principal").all()
    serializer_class = serializers.ClasseSerializer
    permission_classes = [AccesModule]
    module = "classes"
    filterset_fields = ["id_niveau"]
    search_fields = ["nom", "id_niveau__libelle"]


class ParentTuteurViewSet(PerimetreMixin, CreationAccesMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.ParentTuteur.objects.all()
    serializer_class = serializers.ParentTuteurSerializer
    permission_classes = [AccesModule]
    module = "parents"
    perimetre_famille = "liens_eleves__id_eleve"
    role_acces = "parent"
    modules_par_action = {"creer_acces": "comptes"}


class EleveViewSet(PerimetreMixin, CreationAccesMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                   EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Eleve.objects.all()
    serializer_class = serializers.EleveSerializer
    permission_classes = [AccesModule]
    module = "eleves"
    perimetre_famille = "pk"
    # Les mouvements et la réinscription relèvent des inscriptions ; ce qui
    # touche à l'argent, des frais ou de l'encaissement. Les écrans qui
    # préparent une écriture exigent déjà le droit d'écrire.
    modules_par_action = {
        "a_reinscrire": ("inscriptions", ECRITURE),
        "contexte_reinscription": ("inscriptions", ECRITURE),
        "reinscrire": "inscriptions",
        "inscrire": "inscriptions",
        "importer": "inscriptions",
        "changer_classe": "inscriptions",
        "radier": "inscriptions",
        "transferer": "inscriptions",
        "autoriser_en_classe": "inscriptions",
        "retirer_autorisation": "inscriptions",
        "finances": "frais",
        "facture": "frais",
        "par_matricule": "frais",
        "frais_disponibles": ("encaissement", ECRITURE),
        "ajouter_frais": "encaissement",
        "releve": "notes",
        "exporter_releve": "notes",
        "creer_acces": "comptes",
    }
    actions_personnel = ("par_matricule",)
    role_acces = "eleve"
    filterset_fields = ["statut"]
    search_fields = ["nom", "prenom", "matricule"]
    # Un élève n'appartient pas à une année : ce sont ses inscriptions qui la
    # portent. Filtrer par année revient donc à ne garder que les élèves
    # inscrits cette année-là.
    chemin_annee = "inscriptions__id_annee_scolaire"

    # Trois façons de lire « les élèves de l'année », via ?inscription=
    ETATS_INSCRIPTION = ("confirmee", "en_attente", "toutes")

    def get_queryset(self):
        """
        Restreint la liste d'une année aux inscriptions réellement confirmées.

        Une place réservée n'est pas une inscription : tant que rien n'a été
        encaissé, l'inscription reste « pré-inscrite » et l'élève n'a pas à
        figurer parmi les inscrits — sinon les effectifs, les listes d'appel
        et les bulletins comptent des élèves qui ne sont peut-être jamais
        venus. `?inscription=en_attente` donne justement ceux-là, et
        `?inscription=toutes` retrouve l'ancien comportement.

        `?classe=` restreint la liste aux élèves inscrits dans cette classe. Il
        entre dans la même sous-requête que l'année et l'état, pour la même
        raison : les trois critères doivent porter sur la même inscription.

        Le filtre passe par un `pk__in` plutôt qu'un second `.filter()` sur la
        relation : deux filtres sur `inscriptions__` visent deux lignes
        différentes, et un élève inscrit ailleurs cette année ressortirait.
        """
        qs = super().get_queryset()
        if not self.request.user.is_authenticated:
            return qs
        params = self.request.query_params
        brut = params.get("id_annee_scolaire")
        classe = _reference(models.Classe, params.get("classe"),
                            self.request.user.id_ecole, "classe")
        if not brut and classe is None:
            return qs

        inscriptions = models.Inscription.objects.all()
        if classe is not None:
            inscriptions = inscriptions.filter(id_classe=classe)
        if brut:
            etat = params.get("inscription") or "confirmee"
            if etat not in self.ETATS_INSCRIPTION:
                raise ValidationError({
                    "inscription": "Attendu : " + ", ".join(self.ETATS_INSCRIPTION) + "."
                })
            inscriptions = inscriptions.filter(id_annee_scolaire=brut)
            if etat == "confirmee":
                # Les élèves en classe : les inscrits, et les inscrits incomplets
                # que l'administration a autorisés à suivre les cours.
                inscriptions = inscriptions.filter(models.Inscription.filtre_en_classe())
            elif etat == "en_attente":
                inscriptions = inscriptions.filter(statut=models.Inscription.Statut.EN_ATTENTE)
        return qs.filter(pk__in=inscriptions.values("id_eleve"))

    def get_serializer_class(self):
        if self.action == "inscrire":
            return serializers.EleveInscriptionSerializer
        return super().get_serializer_class()

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="a-reinscrire")
    def a_reinscrire(self, request):
        """
        File d'attente de la rentrée :
        GET /api/eleves/a-reinscrire/?id_annee_scolaire=&classe=&niveau=&q=

        Les élèves déjà passés par l'école et qui n'ont aucune inscription sur
        l'année visée. Sans cet écran ils disparaissaient de la liste dès
        qu'on basculait sur la nouvelle année, sans que rien ne signale qu'ils
        attendaient d'être réinscrits.
        """
        ecole = request.user.id_ecole
        annee = _annee_demandee(request, ecole)
        if annee is None:
            return Response({"annee": None, "eleves": [],
                             "detail": "Aucune année scolaire active."})
        params = request.query_params
        try:
            eleves = scolarite.eleves_sans_inscription(
                ecole, annee,
                classe_source=_reference(models.Classe, params.get("classe"), ecole, "classe"),
                niveau_source=_reference(models.Niveau, params.get("niveau"), ecole, "niveau"),
                recherche=params.get("q"),
            )
        except scolarite.ErreurScolarite as exc:
            raise ValidationError({"detail": str(exc)})

        # Cinq cents lignes d'un coup ne se traitent pas : on réinscrit classe
        # par classe, une page à la fois. La pagination est faite ici plutôt
        # que par DRF parce que la liste est construite en mémoire — c'est le
        # regroupement « une seule ligne par élève, la plus récente » qui
        # l'exige, et il ne s'exprime pas en SQL sans fenêtrage.
        page, taille = _pagination(request)
        debut = (page - 1) * taille
        tranche = eleves[debut:debut + taille]
        pages = max(1, -(-len(eleves) // taille))
        return Response({
            "annee": annee.libelle,
            "id_annee_scolaire": annee.pk,
            "total": len(eleves),
            "page": page,
            "pages": pages,
            "taille_page": taille,
            "precedent": page > 1,
            "suivant": page < pages,
            "eleves": tranche,
        })

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"], url_path="reinscription")
    def contexte_reinscription(self, request, pk=None):
        """
        De quoi pré-remplir l'écran de réinscription d'un élève :
        GET /api/eleves/{id}/reinscription/?id_annee_scolaire=

        L'écran ne doit demander qu'une chose — la classe d'arrivée. D'où il
        vient, l'année visée et les classes possibles se déduisent, et les
        faire ressaisir serait une invitation à l'erreur.
        """
        ecole = request.user.id_ecole
        try:
            eleve = self._eleves_accessibles(request).get(pk=pk)
        except (models.Eleve.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Élève introuvable."},
                            status=status.HTTP_404_NOT_FOUND)

        annee = _annee_demandee(request, ecole)
        if annee is None:
            return Response({"detail": "Aucune année scolaire active."},
                            status=status.HTTP_400_BAD_REQUEST)

        precedente = (models.Inscription.objects
                      .filter(id_eleve=eleve)
                      .exclude(statut=models.Inscription.Statut.ANNULEE)
                      .exclude(id_annee_scolaire=annee)
                      .select_related("id_classe", "id_classe__id_niveau",
                                      "id_annee_scolaire")
                      .order_by("-id_annee_scolaire__date_debut").first())
        existante = (models.Inscription.objects
                     .filter(id_eleve=eleve, id_annee_scolaire=annee)
                     .select_related("id_classe").first())

        # Le montant de chaque frais selon le niveau : l'écran l'affiche pour la
        # classe d'arrivée choisie.
        tarifs_par_type = {}
        for tarif in models.TarifNiveau.objects.filter(id_type_frais__id_ecole=ecole):
            tarifs_par_type.setdefault(tarif.id_type_frais_id, {})[str(tarif.id_niveau_id)] = (
                finances.montant_json(tarif.montant))

        reconduits = set() if precedente is None else {
            t.pk for t in finances.types_optionnels_de(eleve, precedente.id_annee_scolaire)}

        classes = (models.Classe.objects
                   .filter(id_ecole=ecole, id_annee_scolaire=annee)
                   .select_related("id_niveau").order_by("nom"))

        return Response({
            "eleve": {
                "id": eleve.pk,
                "matricule": eleve.matricule,
                "nom": eleve.nom,
                "prenom": eleve.prenom,
                "nom_complet": f"{eleve.prenom} {eleve.nom}".strip(),
                "sexe": eleve.sexe,
                "statut": eleve.statut,
                "statut_libelle": eleve.get_statut_display(),
            },
            "precedente": None if precedente is None else {
                "annee": precedente.id_annee_scolaire.libelle,
                "id_annee_scolaire": precedente.id_annee_scolaire_id,
                "classe": precedente.id_classe.nom,
                "id_classe": precedente.id_classe_id,
                "niveau": precedente.id_classe.libelle_niveau,
                "id_niveau": precedente.id_classe.id_niveau_id,
            },
            "annee_cible": {
                "id": annee.pk,
                "libelle": annee.libelle,
                "date_debut": annee.date_debut.isoformat(),
            },
            # Renseigné quand l'élève est déjà passé : l'écran l'annonce au
            # lieu de laisser valider pour échouer ensuite.
            "deja_inscrit": None if existante is None else {
                "classe": existante.id_classe.nom,
                "statut": existante.statut,
                "statut_libelle": existante.get_statut_display(),
            },
            # Le catalogue des frais : ceux d'office sont imposés, ceux que la
            # famille avait l'an passé arrivent pré-cochés.
            "types_frais": [{
                "id": t.pk,
                "libelle": t.libelle,
                "libelle_affiche": t.libelle,
                "nature": t.nature,
                "montant_defaut": finances.montant_json(t.montant_defaut),
                "mensuel": finances.est_mensuel(t),
                "d_office": t.nature in finances.TYPES_D_OFFICE,
                "reconduit": t.pk in reconduits,
                "tarifs": tarifs_par_type.get(t.pk, {}),
            } for t in models.TypeFrais.objects.filter(id_ecole=ecole).order_by("libelle", "pk")],
            "classes": [{
                "id": c.pk, "nom": c.nom, "niveau": c.libelle_niveau,
                "id_niveau": c.id_niveau_id,
                "effectif": c.effectif, "capacite_max": c.capacite_max,
            } for c in classes],
        })

    @extend_schema(request=serializers.ReinscriptionEleveSerializer, responses=dict)
    @action(detail=True, methods=["post"])
    def reinscrire(self, request, pk=None):
        """
        Réinscription d'un élève seul : POST /api/eleves/{id}/reinscrire/

        La réinscription en masse fait monter une classe entière ; celle-ci
        traite le cas courant du redoublant, du changement de série, ou des
        quelques retardataires qui arrivent un par un.
        """
        ecole = request.user.id_ecole
        # Comme pour l'onglet Finances : `get_object()` filtrerait sur l'année
        # affichée, et l'élève à réinscrire n'y est justement pas inscrit.
        try:
            eleve = self._eleves_accessibles(request).get(pk=pk)
        except (models.Eleve.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Élève introuvable."},
                            status=status.HTTP_404_NOT_FOUND)

        entree = serializers.ReinscriptionEleveSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        classe = entree.validated_data["id_classe"]
        if classe.id_ecole_id != ecole.pk:
            raise ValidationError({"id_classe": "Cette classe appartient à une autre école."})

        try:
            resultat = scolarite.reinscrire_eleve(
                ecole, eleve, classe,
                date_inscription=entree.validated_data.get("date_inscription"),
                frais_optionnels=entree.validated_data.get("frais_optionnels"))
        except scolarite.ErreurScolarite as exc:
            raise ValidationError({"detail": str(exc)})

        journaliser(
            request, models.JournalActivite.Action.CREATION, entite="inscription",
            id_entite=resultat["id_inscription"],
            description=f"Réinscription de {resultat['eleve']} en {classe.nom} "
                        f"({resultat['annee']})",
        )
        return Response(resultat, status=status.HTTP_201_CREATED)

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
        donnees = serializers.EleveSerializer(eleve).data
        # La facture à remettre à la famille, qui la présentera à la caisse.
        donnees["facture"] = finances.facture(
            eleve, serializer.validated_data["id_annee_scolaire"])
        return Response(donnees, status=status.HTTP_201_CREATED)

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
            parser_classes=[MultiPartParser, FormParser])
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
        return Response(self._dossier(request, eleve))

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def dossier(self, request, pk=None):
        """
        Dossier numérique complet : GET /api/eleves/{id}/dossier/

        Identité, parents, parcours, pièces, résultats, situation financière
        et assiduité en une réponse — l'écran les affiche ensemble.
        """
        return Response(self._dossier(request, self.get_object()))

    # Section du dossier → module qu'il faut pouvoir lire pour la recevoir.
    SECTIONS_DU_DOSSIER = {"parents": "parents", "finances": "frais",
                           "resultats": "bulletins", "assiduite": "presences"}

    def _dossier(self, request, eleve):
        """
        Le dossier, amputé des sections que le profil n'a pas le droit de lire :
        un enseignant ouvre la fiche d'un élève sans voir ce que doit la famille.
        """
        donnees = scolarite.dossier(request.user.id_ecole, eleve)
        for section, module in self.SECTIONS_DU_DOSSIER.items():
            if not peut_lire(request.user, module):
                donnees[section] = None
        return donnees

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def finances(self, request, pk=None):
        """
        Situation financière de l'élève :
        GET /api/eleves/{id}/finances/?id_annee_scolaire=

        Résumé, détail des frais, échéancier mensuel et historique des
        paiements — pour **une** année scolaire, jamais toutes confondues.
        Sans paramètre, l'année active ; à défaut, la plus récente où l'élève
        a laissé une trace, pour qu'un ancien élève garde un dossier lisible.
        """
        ecole = request.user.id_ecole
        # `get_object()` passerait par `FiltreParAnneeMixin`, qui ne garde que
        # les élèves *inscrits* l'année demandée : consulter l'ardoise d'un
        # ancien élève radié en cours d'année répondrait 404. L'école reste,
        # elle, vérifiée — c'est la seule cloison qui compte ici.
        try:
            eleve = self._eleves_accessibles(request).get(pk=pk)
        except (models.Eleve.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Élève introuvable."},
                            status=status.HTTP_404_NOT_FOUND)
        annee = None
        if request.query_params.get("id_annee_scolaire"):
            annee = _annee_demandee(request, ecole)
        try:
            return Response(finances.situation_eleve(ecole, eleve, annee))
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(
        request={"multipart/form-data": {
            "type": "object",
            "properties": {"photo": {"type": "string", "format": "binary",
                                     "description": "JPEG, PNG ou WebP, 3 Mo au plus."}},
        }},
        responses={200: None, 204: None},
    )
    @action(detail=True, methods=["get", "post", "delete"],
            parser_classes=[MultiPartParser, FormParser])
    def photo(self, request, pk=None):
        """
        Photo de l'élève : GET pour la lire, POST (champ `photo`) pour la déposer
        ou la remplacer, DELETE pour la retirer.

        Lue par l'API plutôt que servie à une URL publique : c'est la photo d'un
        enfant, elle reste derrière l'authentification et le cloisonnement par
        école. Tout compte qui lit l'élève la voit ; le modifier demande le droit
        d'écrire sur les élèves.
        """
        try:
            eleve = self._eleves_accessibles(request).get(pk=pk)
        except (models.Eleve.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Élève introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "GET":
            fichier = photos.chemin(eleve)
            if fichier is None:
                return Response({"detail": "Aucune photo pour cet élève."},
                                status=status.HTTP_404_NOT_FOUND)
            reponse = FileResponse(open(fichier, "rb"), content_type=photos.type_mime(fichier))
            reponse["Cache-Control"] = "private, no-store"
            return reponse

        if request.method == "DELETE":
            photos.supprimer(eleve)
            journaliser(request, models.JournalActivite.Action.MODIFICATION, entite="eleve",
                        id_entite=eleve.pk,
                        description=f"Photo retirée du dossier de {eleve.prenom} {eleve.nom}")
            return Response(status=status.HTTP_204_NO_CONTENT)

        envoye = request.FILES.get("photo")
        if envoye is None:
            raise ValidationError({"photo": "Aucune photo reçue."})
        try:
            photos.enregistrer(eleve, envoye)
        except photos.ErreurPhoto as exc:
            raise ValidationError({"photo": str(exc)})
        journaliser(request, models.JournalActivite.Action.MODIFICATION, entite="eleve",
                    id_entite=eleve.pk,
                    description=f"Photo ajoutée au dossier de {eleve.prenom} {eleve.nom}")
        return Response({"a_photo": True})

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="par-matricule")
    def par_matricule(self, request):
        """
        Guichet : GET /api/eleves/par-matricule/?matricule=ELV-2026-0001

        La caissière saisit le matricule imprimé sur la facture ; l'écran
        affiche l'élève, sa classe et ce qu'il reste à payer.
        """
        ecole = request.user.id_ecole
        matricule = (request.query_params.get("matricule") or "").strip()
        if not matricule:
            raise ValidationError({"matricule": "Saisissez le matricule figurant sur la facture."})
        eleve = self._eleves_accessibles(request).filter(matricule__iexact=matricule).first()
        if eleve is None:
            return Response({"detail": f"Aucun élève ne porte le matricule « {matricule} »."},
                            status=status.HTTP_404_NOT_FOUND)
        annee = _annee_de_leleve(request, ecole, eleve)
        if annee is None:
            return Response({"detail": "Aucune année scolaire à facturer pour cet élève."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(finances.facture(eleve, annee))

    @extend_schema(responses={200: None})
    @action(detail=True, methods=["get"])
    def facture(self, request, pk=None):
        """Facture remise à l'inscription : GET /api/eleves/{id}/facture/?id_annee_scolaire="""
        ecole = request.user.id_ecole
        try:
            eleve = self._eleves_accessibles(request).get(pk=pk)
        except (models.Eleve.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Élève introuvable."}, status=status.HTTP_404_NOT_FOUND)
        annee = _annee_de_leleve(request, ecole, eleve)
        if annee is None:
            return Response({"detail": "Aucune année scolaire à facturer pour cet élève."},
                            status=status.HTTP_400_BAD_REQUEST)
        return _fichier(
            request,
            finances_documents.facture(ecole, finances.facture(eleve, annee)),
            description=f"Facture de {eleve.prenom} {eleve.nom} ({annee.libelle})",
            entite="frais_eleve",
        )

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
    @action(detail=True, methods=["post"], url_path="changer-classe")
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
    @action(detail=True, methods=["post"])
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
    @action(detail=True, methods=["post"])
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

    @extend_schema(request=serializers.AutorisationClasseSerializer, responses=dict)
    @action(detail=True, methods=["post"], url_path="autoriser-en-classe")
    def autoriser_en_classe(self, request, pk=None):
        """
        Exception : POST /api/eleves/{id}/autoriser-en-classe/

        L'inscrit incomplet suit les cours ; son inscription reste incomplète.
        """
        return self._mouvement(
            request, serializers.AutorisationClasseSerializer,
            lambda d, eleve: scolarite.autoriser_en_classe(
                request.user.id_ecole, eleve, d["motif"], utilisateur=request.user),
            models.JournalActivite.Action.MODIFICATION,
            lambda eleve, d: f"Autorisation d'entrer en classe pour {eleve.prenom} {eleve.nom} "
                             f"(inscription incomplète) — {d['motif']}",
        )

    @extend_schema(request=serializers.RetraitAutorisationSerializer, responses=dict)
    @action(detail=True, methods=["post"], url_path="retirer-autorisation")
    def retirer_autorisation(self, request, pk=None):
        """Retrait de l'exception : POST /api/eleves/{id}/retirer-autorisation/"""
        return self._mouvement(
            request, serializers.RetraitAutorisationSerializer,
            lambda d, eleve: scolarite.retirer_autorisation(request.user.id_ecole, eleve),
            models.JournalActivite.Action.MODIFICATION,
            lambda eleve, d: f"Retrait de l'autorisation d'entrer en classe de "
                             f"{eleve.prenom} {eleve.nom}",
        )

    def _eleve_de_lecole(self, request, pk):
        """
        L'élève par son identifiant, dans l'école connectée.

        Pas `get_object()` : le filtre de liste par année (`?id_annee_scolaire=`)
        écarterait un inscrit incomplet, précisément celui qu'on veut servir.
        """
        try:
            return self._eleves_accessibles(request).get(pk=pk)
        except (models.Eleve.DoesNotExist, ValueError, TypeError):
            return None

    def fiche_pour_acces(self, request, pk):
        # Pas `get_object()` : il filtrerait sur l'année affichée.
        return self._eleve_de_lecole(request, pk)

    def _eleves_accessibles(self, request):
        """
        Les élèves de l'école que le compte peut ouvrir, sans le filtre de liste.

        Les actions qui ne passent pas par `get_object()` — pour échapper au
        filtre par année — doivent garder le périmètre famille : sans lui, un
        parent lirait la situation de n'importe quel élève par son identifiant.
        """
        qs = models.Eleve.objects.filter(id_ecole=request.user.id_ecole)
        famille = eleves_de_la_famille(request.user)
        return qs if famille is None else qs.filter(pk__in=famille.values("pk"))

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"], url_path="frais-disponibles")
    def frais_disponibles(self, request, pk=None):
        """Frais au choix qu'on peut ajouter : GET /api/eleves/{id}/frais-disponibles/"""
        ecole = request.user.id_ecole
        eleve = self._eleve_de_lecole(request, pk)
        if eleve is None:
            return Response({"detail": "Élève introuvable."}, status=status.HTTP_404_NOT_FOUND)
        annee = _reference(models.AnneeScolaire, request.query_params.get("id_annee_scolaire"),
                           ecole, "id_annee_scolaire")
        try:
            return Response(finances.frais_disponibles(ecole, eleve, annee))
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

    @extend_schema(request=serializers.AjoutFraisSerializer, responses=dict)
    @action(detail=True, methods=["post"], url_path="ajouter-frais")
    def ajouter_frais(self, request, pk=None):
        """
        Frais au choix souscrits en cours d'année : POST /api/eleves/{id}/ajouter-frais/

        Renvoie les lignes émises et la situation financière recalculée.
        """
        ecole = request.user.id_ecole
        eleve = self._eleve_de_lecole(request, pk)
        if eleve is None:
            return Response({"detail": "Élève introuvable."}, status=status.HTTP_404_NOT_FOUND)
        entree = serializers.AjoutFraisSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data

        ids = set(donnees["frais"])
        types = list(models.TypeFrais.objects.filter(id_ecole=ecole, pk__in=ids).order_by("libelle"))
        if len(types) != len(ids):
            raise ValidationError({"frais": "Frais inconnu pour cette école."})
        annee = _reference(models.AnneeScolaire, donnees.get("id_annee_scolaire"),
                           ecole, "id_annee_scolaire")
        try:
            resultat = finances.ajouter_frais_optionnels(
                ecole, eleve, types, annee=annee, date_debut=donnees.get("date_debut"))
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

        journaliser(request, models.JournalActivite.Action.CREATION, entite="eleve",
                    id_entite=eleve.pk,
                    description=f"Ajout de frais pour {eleve.prenom} {eleve.nom} : "
                                + ", ".join(f["libelle"] for f in resultat["frais"]))
        annee = models.AnneeScolaire.objects.get(pk=resultat["id_annee_scolaire"])
        resultat["situation"] = finances.situation_eleve(ecole, eleve, annee)
        return Response(resultat, status=status.HTTP_201_CREATED)


class InscriptionViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Inscription.objects.select_related("id_eleve", "id_classe").all()
    serializer_class = serializers.InscriptionSerializer
    permission_classes = [AccesModule]
    module = "inscriptions"
    perimetre_famille = "id_eleve"
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
    @action(detail=False, methods=["post"])
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
                eleves=donnees.get("eleves"),
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


class EleveParentViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Rattache un parent à un élève, en dehors du formulaire d'inscription."""
    queryset = models.EleveParent.objects.select_related("id_eleve", "id_parent").all()
    serializer_class = serializers.EleveParentSerializer
    permission_classes = [AccesModule]
    module = "parents"
    perimetre_famille = "id_eleve"
    filterset_fields = ["id_eleve", "id_parent", "lien_parente"]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class DossierAdministratifViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """
    Pièces du dossier d'un élève. Distinct de /documents/, qui porte les
    documents de l'établissement.
    """
    queryset = models.DossierAdministratif.objects.select_related("id_eleve").all()
    serializer_class = serializers.DossierAdministratifSerializer
    permission_classes = [AccesModule]
    module = "eleves"
    perimetre_famille = "id_eleve"
    filterset_fields = ["id_eleve", "type_piece"]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class HistoriqueScolaireViewSet(PerimetreMixin, EcoleFiltreeMixin, viewsets.ReadOnlyModelViewSet):
    """
    Parcours d'un élève, en lecture seule : les entrées sont écrites
    automatiquement par les inscriptions, pas saisies à la main.
    """
    queryset = models.HistoriqueScolaire.objects.select_related(
        "id_eleve", "id_ancienne_classe", "id_nouvelle_classe"
    ).all().order_by("-date_evenement")
    serializer_class = serializers.HistoriqueScolaireSerializer
    permission_classes = [AccesModule]
    module = "eleves"
    perimetre_famille = "id_eleve"
    ecole_field = "id_eleve__id_ecole_id"
    filterset_fields = ["id_eleve", "type_evenement"]


# ---------- Module pédagogique ----------

class EnseignantViewSet(CreationAccesMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Enseignant.objects.all()
    serializer_class = serializers.EnseignantSerializer
    permission_classes = [AccesModule]
    module = "personnel"
    role_acces = "enseignant"
    modules_par_action = {"creer_acces": "comptes"}

    def libelle_de_la_fiche(self, fiche):
        return f"l'enseignant {fiche.matricule}"


class MatiereViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Matiere.objects.all()
    serializer_class = serializers.MatiereSerializer
    permission_classes = [AccesModule]
    module = "matieres"


class CoefficientMatiereViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Poids d'une matière dans une classe — base du calcul des moyennes."""
    queryset = models.CoefficientMatiere.objects.select_related("id_matiere", "id_classe").all()
    serializer_class = serializers.CoefficientMatiereSerializer
    permission_classes = [AccesModule]
    module = "matieres"
    filterset_fields = ["id_classe", "id_matiere", "id_annee_scolaire"]

    def get_queryset(self):
        return super().get_queryset().filter(
            id_classe__id_ecole_id=self.request.user.id_ecole_id
        )


class AffectationEnseignantViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """Qui enseigne quelle matière, dans quelle classe, pour quel volume horaire."""
    queryset = models.AffectationEnseignant.objects.select_related(
        "id_enseignant", "id_matiere", "id_classe"
    ).all()
    serializer_class = serializers.AffectationEnseignantSerializer
    permission_classes = [AccesModule]
    module = "matieres"
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


class ConseilClasseViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                           viewsets.ModelViewSet):
    queryset = models.ConseilClasse.objects.select_related("id_classe", "id_periode").all()
    serializer_class = serializers.ConseilClasseSerializer
    permission_classes = [AccesModule]
    module = "bulletins"
    famille_exclue = True
    perimetre_enseignant = "id_classe"
    filterset_fields = ["id_classe", "id_periode"]
    chemin_annee = "id_periode__id_annee_scolaire"

    def get_queryset(self):
        return super().get_queryset().filter(
            id_classe__id_ecole_id=self.request.user.id_ecole_id
        )


class EvaluationViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                        viewsets.ModelViewSet):
    queryset = models.Evaluation.objects.select_related(
        "id_matiere", "id_classe", "id_periode").all()
    serializer_class = serializers.EvaluationSerializer
    permission_classes = [AccesModule]
    module = "notes"
    perimetre_famille = "id_classe__inscriptions__id_eleve"
    perimetre_enseignant = "id_classe"
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


class NoteViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Note.objects.all()
    serializer_class = serializers.NoteSerializer
    permission_classes = [AccesModule]
    module = "notes"
    perimetre_famille = "id_eleve"
    perimetre_enseignant = "id_evaluation__id_classe"
    filterset_fields = ["id_eleve", "id_evaluation"]

    def get_queryset(self):
        return super().get_queryset().filter(id_eleve__id_ecole_id=self.request.user.id_ecole_id)


class BulletinViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                      viewsets.ModelViewSet):
    queryset = models.Bulletin.objects.select_related(
        "id_eleve", "id_classe", "id_periode").all()
    serializer_class = serializers.BulletinSerializer
    permission_classes = [AccesModule]
    module = "bulletins"
    perimetre_famille = "id_eleve"
    perimetre_enseignant = "id_classe"
    actions_personnel = ("generer", "moyennes", "exporter_moyennes", "palmares",
                         "exporter_palmares", "statistiques", "synthese")
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
        _classe_de_lenseignant_ou_refus(request.user, classe)
        return ecole, classe, periode

    @extend_schema(request=serializers.GenerationBulletinsSerializer, responses=dict)
    @action(detail=False, methods=["post"])
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
        _classe_de_lenseignant_ou_refus(request.user, donnees["id_classe"])

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


class AbsenceViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                     viewsets.ModelViewSet):
    queryset = models.Absence.objects.all()
    serializer_class = serializers.AbsenceSerializer
    permission_classes = [AccesModule]
    module = "presences"
    perimetre_famille = "id_eleve"
    perimetre_enseignant = "id_classe"
    actions_personnel = ("synthese",)
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

class SalleViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Salle.objects.all()
    serializer_class = serializers.SalleSerializer
    permission_classes = [AccesModule]
    module = "classes"


class EmploiDuTempsViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                           viewsets.ModelViewSet):
    queryset = models.EmploiDuTemps.objects.select_related("id_matiere", "id_enseignant", "id_salle").all()
    serializer_class = serializers.EmploiDuTempsSerializer
    permission_classes = [AccesModule]
    module = "emploi_du_temps"

    def get_queryset(self):
        qs = super().get_queryset().filter(id_classe__id_ecole_id=self.request.user.id_ecole_id)
        id_classe = self.request.query_params.get("classe")
        if id_classe:
            qs = qs.filter(id_classe=id_classe)
        return qs


# ---------- Module financier ----------

class TypeFraisViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.TypeFrais.objects.all()
    serializer_class = serializers.TypeFraisSerializer
    permission_classes = [AccesModule]
    module = "types_frais"


class FraisEleveViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, FiltreParAnneeMixin,
                        viewsets.ModelViewSet):
    queryset = models.FraisEleve.objects.select_related(
        "id_eleve", "id_type_frais", "id_annee_scolaire").all()
    serializer_class = serializers.FraisEleveSerializer
    permission_classes = [AccesModule]
    module = "frais"
    perimetre_famille = "id_eleve"
    actions_personnel = ("generer", "impayes_par_classe", "situation", "exporter_impayes")

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
    @action(detail=False, methods=["post"])
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

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="situation")
    def situation(self, request):
        """
        Une ligne par élève :
        GET /api/frais-eleves/situation/?id_annee_scolaire=&classe=&niveau=&statut=&q=

        La liste des frais répond « quelles échéances sont ouvertes » ; celle-ci
        répond « qui est à jour », qui est la question de la direction.
        """
        ecole = request.user.id_ecole
        annee = _annee_demandee(request, ecole)
        if annee is None:
            return Response({"annee": None, "eleves": [],
                             "totaux": {"eleves": 0, "du": 0, "paye": 0, "reste": 0},
                             "repartition": {},
                             "detail": "Aucune année scolaire active."})
        params = request.query_params
        statut = params.get("statut") or None
        if statut and statut not in finances.StatutFinancier.VALEURS:
            raise ValidationError({
                "statut": "Attendu : " + ", ".join(finances.StatutFinancier.VALEURS) + "."
            })
        try:
            return Response(finances.situation_globale(
                ecole, annee,
                classe=_reference(models.Classe, params.get("classe"), ecole, "classe"),
                niveau=_reference(models.Niveau, params.get("niveau"), ecole, "niveau"),
                statut=statut,
                recherche=params.get("q"),
            ))
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


class PaiementViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    """
    Cœur du parcours caissier : POST /api/paiements/ encaisse un paiement,
    met à jour le frais concerné, génère le reçu et le mouvement de caisse
    automatiquement (voir PaiementSerializer.create).
    """
    queryset = models.Paiement.objects.select_related(
        "id_frais_eleve", "id_frais_eleve__id_eleve", "id_frais_eleve__id_type_frais",
        "id_utilisateur_caissier", "recu").all()
    serializer_class = serializers.PaiementSerializer
    permission_classes = [AccesModule]
    module = "encaissement"
    perimetre_famille = "id_frais_eleve__id_eleve"
    actions_personnel = ("exporter",)

    def get_queryset(self):
        qs = super().get_queryset().filter(
            id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)
        params = self.request.query_params
        # Les filtres suivent ce que l'écran de recherche propose : un élève,
        # une classe, une année, un type de frais, une référence de transaction.
        for parametre, champ in (
            ("methode", "methode"),
            ("eleve", "id_frais_eleve__id_eleve"),
            ("annee", "id_frais_eleve__id_annee_scolaire"),
            ("id_annee_scolaire", "id_frais_eleve__id_annee_scolaire"),
            ("type_frais", "id_frais_eleve__id_type_frais"),
            ("frais", "id_frais_eleve"),
            ("classe", "id_frais_eleve__id_eleve__inscriptions__id_classe"),
        ):
            if params.get(parametre):
                qs = qs.filter(**{champ: params[parametre]})
        if params.get("reference"):
            qs = qs.filter(reference_transaction__icontains=params["reference"])
        if params.get("mois"):
            # « 2026-10 » : le mois de l'échéance réglée, pas celui du versement.
            try:
                an, numero = str(params["mois"]).split("-")[:2]
                qs = qs.filter(id_frais_eleve__echeance__year=int(an),
                               id_frais_eleve__echeance__month=int(numero))
            except (ValueError, TypeError):
                raise ValidationError({"mois": "Mois attendu au format AAAA-MM."})
        if params.get("debut"):
            qs = qs.filter(date_paiement__date__gte=params["debut"])
        if params.get("fin"):
            qs = qs.filter(date_paiement__date__lte=params["fin"])
        return qs.distinct()

    @extend_schema(request=serializers.EncaissementSerializer, responses=dict)
    @action(detail=False, methods=["post"])
    def encaisser(self, request):
        """
        Encaissement depuis le dossier de l'élève :
        POST /api/paiements/encaisser/

        On saisit un élève et un montant ; le serveur retrouve l'inscription,
        l'année et les échéances ouvertes, et impute de la plus ancienne à la
        plus récente. Un versement qui couvre deux mensualités produit deux
        paiements, chacun avec son reçu — la répartition n'est pas laissée à
        l'appréciation de la personne au guichet.

        C'est le chemin qu'emprunterait aussi un encaissement automatique :
        la caisse ne doit pas dépendre de la porte d'entrée du règlement.
        """
        entree = serializers.EncaissementSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data
        ecole = request.user.id_ecole

        for champ in ("id_eleve", "id_annee_scolaire", "id_type_frais"):
            objet = donnees.get(champ)
            if objet is not None and ecole_de(objet) != ecole.pk:
                raise ValidationError({champ: "Cet élément appartient à une autre école."})

        # Les inscriptions encore incomplètes avant ce versement : celle qu'il
        # confirme est annoncée à l'écran (« … est maintenant inscrit »).
        en_attente = list(models.Inscription.objects
                          .filter(id_eleve=donnees["id_eleve"],
                                  statut=models.Inscription.Statut.EN_ATTENTE)
                          .values_list("pk", flat=True))

        try:
            paiements = finances.affecter_paiement(
                ecole,
                donnees["id_eleve"],
                donnees["montant"],
                donnees["methode"],
                annee=donnees.get("id_annee_scolaire"),
                type_frais=donnees.get("id_type_frais"),
                mois=donnees.get("mois"),
                frais=donnees.get("frais"),
                reference=donnees.get("reference_transaction"),
                commentaire=donnees.get("commentaire"),
                date_paiement=donnees.get("date_paiement"),
                utilisateur=request.user,
            )
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

        eleve = donnees["id_eleve"]
        journaliser(
            request, models.JournalActivite.Action.CREATION, entite="paiement",
            id_entite=paiements[0].pk,
            description=f"Encaissement de {donnees['montant']} F pour "
                        f"{eleve.prenom} {eleve.nom} "
                        f"({len(paiements)} échéance(s) soldée(s) ou entamée(s))",
        )
        annee = (donnees.get("id_annee_scolaire")
                 or paiements[0].id_frais_eleve.id_annee_scolaire)
        confirmee = (models.Inscription.objects
                     .filter(pk__in=en_attente, statut__in=models.Inscription.STATUTS_CONFIRMES)
                     .select_related("id_classe", "id_annee_scolaire")
                     .first())
        return Response({
            "paiements": serializers.PaiementSerializer(paiements, many=True).data,
            "situation": finances.situation_eleve(ecole, eleve, annee),
            "inscription_confirmee": None if confirmee is None else {
                "eleve": f"{eleve.prenom} {eleve.nom}".strip(),
                "classe": confirmee.id_classe.nom,
                "annee": confirmee.id_annee_scolaire.libelle,
            },
        }, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], url_path="recu")
    def recu_encaissement(self, request):
        """
        Reçu d'un encaissement au guichet : GET /api/paiements/recu/?ids=12,13

        Un versement réparti sur plusieurs frais produit plusieurs paiements ;
        la famille, elle, repart avec un seul reçu.
        """
        try:
            ids = {int(x) for x in (request.query_params.get("ids") or "").split(",") if x.strip()}
        except ValueError:
            raise ValidationError({"ids": "Identifiants de paiements attendus, séparés par des virgules."})
        paiements = list(self.get_queryset().filter(pk__in=ids).order_by("pk")) if ids else []
        if not paiements or len(paiements) != len(ids):
            return Response({"detail": "Paiement introuvable."}, status=status.HTTP_404_NOT_FOUND)
        if len({p.id_frais_eleve.id_eleve_id for p in paiements}) > 1:
            raise ValidationError({"ids": "Un reçu ne regroupe que les paiements d'un même élève."})
        eleve = paiements[0].id_frais_eleve.id_eleve
        return _fichier(
            request,
            finances_documents.recu_encaissement(request.user.id_ecole, paiements),
            description=f"Reçu de paiement de {eleve.prenom} {eleve.nom}",
        )

    @action(detail=False, methods=["get"], url_path="export")
    def exporter(self, request):
        """GET /api/paiements/export/?format=xlsx|pdf — journal des recettes."""
        return _fichier(
            request,
            finances_documents.paiements(request.user.id_ecole, self.get_queryset(),
                                         format=_format_export(request)),
            description="Export du journal des recettes",
        )


class RecuViewSet(PerimetreMixin, viewsets.ReadOnlyModelViewSet):
    queryset = models.Recu.objects.select_related(
        "id_paiement", "id_paiement__id_frais_eleve",
        "id_paiement__id_frais_eleve__id_eleve").all()
    serializer_class = serializers.RecuSerializer
    permission_classes = [AccesModule]
    module = "frais"
    perimetre_famille = "id_paiement__id_frais_eleve__id_eleve"

    def get_queryset(self):
        return super().get_queryset().filter(id_paiement__id_frais_eleve__id_eleve__id_ecole_id=self.request.user.id_ecole_id)

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        """GET /api/recus/{id}/pdf/ — le justificatif remis à la famille."""
        return _fichier(request, finances_documents.recu(self.get_object()),
                        description=f"Édition du reçu {self.get_object().numero_recu}")


class RelanceViewSet(PerimetreMixin, CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, viewsets.ModelViewSet):
    queryset = models.Relance.objects.select_related(
        "id_frais_eleve", "id_frais_eleve__id_eleve", "id_frais_eleve__id_type_frais").all()
    serializer_class = serializers.RelanceSerializer
    permission_classes = [AccesModule]
    module = "relances"
    perimetre_famille = "id_frais_eleve__id_eleve"
    actions_personnel = ("generer",)

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


class TypeDepenseViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    """
    Catégories de dépenses, chacune avec son budget alloué et son disponible.

    Tout est rapporté à l'année consultée — celle du bandeau haut quand
    l'écran la transmet, l'année active sinon. C'est elle qui désigne la ligne
    de budget à lire et à écrire, et la fenêtre sur laquelle compter les
    dépenses : une enveloppe annuelle qui compterait les dépenses des années
    passées serait vide d'office.
    """
    queryset = models.TypeDepense.objects.all()
    serializer_class = serializers.TypeDepenseSerializer
    permission_classes = [AccesModule]
    module = "depenses"

    def _annee(self):
        if not self.request.user.is_authenticated:
            return None
        return _annee_demandee(self.request, self.request.user.id_ecole)

    def get_serializer_context(self):
        # L'année dit au serializer quelle ligne de budget porte l'enveloppe.
        return {**super().get_serializer_context(), "annee": self._annee()}

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.is_authenticated:
            return qs
        # L'agrégat pose un GROUP BY, et Django écarte l'ordre par défaut du
        # modèle sur ces requêtes : sans ce tri explicite, la liste des
        # catégories sortirait dans l'ordre que la base veut bien donner.
        return finances.annoter_consommation(qs, self._annee()).order_by("libelle")


class DepenseViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    """
    Une dépense n'est pas qu'une ligne de saisie : elle sort de la caisse.
    Création, modification et suppression tiennent donc le journal et le
    budget à jour, sinon le bilan afficherait des recettes sans les charges.
    """
    queryset = models.Depense.objects.select_related("id_type_depense").all()
    serializer_class = serializers.DepenseSerializer
    permission_classes = [AccesModule]
    module = "depenses"

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
        depense = serializer.instance
        depense.id_utilisateur = self.request.user
        depense.save(update_fields=["id_utilisateur"])
        finances.enregistrer_depense(depense, utilisateur=self.request.user)

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


class BudgetViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Budget.objects.select_related("id_type_depense", "id_annee_scolaire").all()
    serializer_class = serializers.BudgetSerializer
    permission_classes = [AccesModule]
    module = "budget"

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
    permission_classes = [AccesModule]
    module = "journal_caisse"

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
    permission_classes = [AccesModule]
    module = "budget"

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

    def patch(self, request):
        """Correction de son propre nom ou de son téléphone."""
        entree = serializers.MonProfilSerializer(request.user, data=request.data, partial=True)
        entree.is_valid(raise_exception=True)
        entree.save()
        journaliser(request, models.JournalActivite.Action.MODIFICATION, entite="utilisateur",
                    id_entite=request.user.pk, description="Mise à jour de son profil")
        return self.get(request)

    def get(self, request):
        donnees = serializers.UtilisateurSerializer(request.user).data
        # Les modules ouverts au compte, lus sur la matrice du serveur : le
        # client n'en garde pas de copie qui pourrait diverger.
        donnees["role_libelle"] = request.user.get_role_display()
        donnees["profil"] = profil_de(request.user)
        donnees["acces"] = acces_du_compte(request.user)
        return Response(donnees)


@extend_schema(request=serializers.ChangementMotDePasseSerializer, responses=dict,
               summary="Changer son mot de passe", tags=["auth"])
class ChangementMotDePasseView(APIView):
    """
    POST /api/auth/mot-de-passe/ — chacun change le sien.

    Le compte reste connecté : les jetons déjà émis ne portent pas le mot de
    passe. C'est à la direction de désactiver un compte dont on veut couper
    l'accès tout de suite.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        entree = serializers.ChangementMotDePasseSerializer(
            data=request.data, context={"utilisateur": request.user})
        entree.is_valid(raise_exception=True)
        request.user.set_password(entree.validated_data["nouveau_mot_de_passe"])
        request.user.save(update_fields=["password"])
        journaliser(request, models.JournalActivite.Action.MODIFICATION, entite="utilisateur",
                    id_entite=request.user.pk, description="Changement de son mot de passe")
        return Response({"detail": "Votre mot de passe a été changé."})


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

class CommunicationViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Communication.objects.select_related("id_utilisateur_auteur", "id_classe").all()
    serializer_class = serializers.CommunicationSerializer
    permission_classes = [AccesModule]
    module = "communications"
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
        # Hors direction, on ne voit que ce qui vous est adressé.
        if not est_direction(self.request.user):
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

class DocumentViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.Document.objects.select_related("id_utilisateur_depot").all()
    serializer_class = serializers.DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["categorie", "visibilite"]
    search_fields = ["titre", "description"]

    # Qui a le droit de voir quoi, par profil. La direction voit tout ; les
    # autres profils du personnel, les documents ouverts à tous.
    VISIBILITE_PAR_PROFIL = {
        Profil.DIRECTION: None,  # aucun filtre
        Profil.ENSEIGNANT: [models.Document.Visibilite.ENSEIGNANTS, models.Document.Visibilite.TOUS],
        Profil.PARENT: [models.Document.Visibilite.PARENTS, models.Document.Visibilite.TOUS],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        visibilites = self.VISIBILITE_PAR_PROFIL.get(
            profil_de(self.request.user), [models.Document.Visibilite.TOUS])
        if visibilites is None:
            return qs
        return qs.filter(visibilite__in=visibilites)

    def perform_create(self, serializer):
        serializer.save(
            id_ecole=self.request.user.id_ecole,
            id_utilisateur_depot=self.request.user,
        )
        self._journaliser_objet(models.JournalActivite.Action.CREATION, serializer.instance)


class RapportViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    """
    Historique des exports demandés. La génération effective du fichier
    (PDF/Excel) n'est pas implémentée : la demande est enregistrée en
    "en_attente" et attend le service de génération.
    """
    queryset = models.Rapport.objects.select_related("id_utilisateur").all()
    serializer_class = serializers.RapportSerializer
    permission_classes = [AccesModule]
    # Même règle que les communications : la direction écrit, tous lisent.
    module = "communications"
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

class ParametreEcoleViewSet(CoherenceEcoleMixin, CorbeilleMixin, JournalisationMixin, EcoleFiltreeMixin, viewsets.ModelViewSet):
    queryset = models.ParametreEcole.objects.all()
    serializer_class = serializers.ParametreEcoleSerializer
    permission_classes = [AccesModule]
    module = "parametres"
    filterset_fields = ["categorie"]

    def perform_create(self, serializer):
        serializer.save(id_ecole=self.request.user.id_ecole)
        self._journaliser_objet(models.JournalActivite.Action.CREATION, serializer.instance)


# ---------- Journal d'activité ----------

class JournalActiviteViewSet(EcoleFiltreeMixin, viewsets.ReadOnlyModelViewSet):
    """Lecture seule et réservé aux admins : un journal d'audit ne se modifie pas."""
    queryset = models.JournalActivite.objects.select_related("id_utilisateur").all()
    serializer_class = serializers.JournalActiviteSerializer
    permission_classes = [AccesModule]
    module = "comptes"
    filterset_fields = ["action", "entite", "id_utilisateur"]
    search_fields = ["description", "entite"]


# ---------- Corbeille ----------

def _modele_du_endpoint(endpoint):
    """Label du modèle servi par un endpoint (« classes » → « gestion_scolaire.Classe »)."""
    # Import tardif : urls importe ce module.
    from .urls import router
    for prefixe, viewset, _nom in router.registry:
        queryset = getattr(viewset, "queryset", None)
        if prefixe == endpoint and queryset is not None:
            return queryset.model._meta.label
    return None


class CorbeilleViewSet(EcoleFiltreeMixin, mixins.ListModelMixin, mixins.DestroyModelMixin,
                       viewsets.GenericViewSet):
    """
    Éléments supprimés : GET /api/corbeille/?endpoint=classes

    Un administrateur voit toute la corbeille de son école ; les autres comptes
    ne voient que ce qu'ils ont eux-mêmes supprimé — un enseignant qui efface
    une note par erreur doit pouvoir la récupérer, pas parcourir ce que la
    direction a supprimé. La suppression définitive est réservée aux
    administrateurs.
    """
    queryset = models.Corbeille.objects.select_related("id_utilisateur").defer("donnees")
    serializer_class = serializers.CorbeilleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action == "destroy":
            return [EstAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        utilisateur = self.request.user
        if not utilisateur.is_authenticated:
            return qs
        if not est_direction(utilisateur):
            qs = qs.filter(id_utilisateur=utilisateur)
        endpoint = self.request.query_params.get("endpoint")
        if endpoint:
            modele = _modele_du_endpoint(endpoint)
            qs = qs.filter(modele=modele) if modele else qs.none()
        return qs

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"])
    def restaurer(self, request, pk=None):
        """Remet l'élément en place : POST /api/corbeille/{id}/restaurer/"""
        entree = self.get_object()
        try:
            corbeille.restaurer(entree, utilisateur=request.user)
        except corbeille.ErreurCorbeille as exc:
            raise ValidationError({"detail": str(exc)})
        journaliser(
            request, models.JournalActivite.Action.RESTAURATION,
            entite=entree.entite, id_entite=entree.id_entite,
            description=f"Restauration de « {entree.libelle} » depuis la corbeille",
        )
        return Response({"detail": f"« {entree.libelle} » a été restauré.",
                         "entite": entree.entite, "id_entite": entree.id_entite})

    def perform_destroy(self, instance):
        libelle, entite, id_entite = instance.libelle, instance.entite, instance.id_entite
        super().perform_destroy(instance)
        journaliser(
            self.request, models.JournalActivite.Action.SUPPRESSION,
            entite=entite, id_entite=id_entite,
            description=f"Suppression définitive de « {libelle} » (corbeille)",
        )


# ---------- Grille des tarifs ----------

def _jour_demande(request, champ="date", source=None):
    """Le jour visé par la requête, aujourd'hui à défaut."""
    donnees = request.query_params if source is None else source
    brut = donnees.get(champ)
    if not brut:
        return timezone.localdate()
    if isinstance(brut, date_type):
        return brut
    try:
        jour = parse_date(brut)
    except ValueError:
        jour = None
    if jour is None:
        raise ValidationError({champ: "Date attendue au format AAAA-MM-JJ."})
    return jour


class ClotureCaisseView(APIView):
    """
    Arrêté de caisse : POST /api/caisse/cloturer/

    Fige les totaux de la journée et les signe. Réservé à la caisse ; la
    journée reste consultable et les encaissements restent possibles ensuite,
    l'écart apparaissant dans l'historique.
    """
    permission_classes = [AccesModule]
    module = "encaissement"

    def post(self, request):
        ecole = request.user.id_ecole
        entree = serializers.ClotureCaisseSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        donnees = entree.validated_data
        jour = _jour_demande(request, source=donnees)
        try:
            cloture = finances.cloturer_journee(
                ecole, jour, utilisateur=request.user,
                commentaire=donnees.get("commentaire"))
        except finances.ErreurFinance as exc:
            raise ValidationError({"detail": str(exc)})

        journaliser(request, models.JournalActivite.Action.CREATION, entite="cloture_caisse",
                    id_entite=cloture.pk,
                    description=f"Clôture de la caisse du {jour:%d/%m/%Y} — "
                                f"{cloture.total_encaisse} F encaissés, "
                                f"{cloture.total_depense} F dépensés")
        return Response(finances.historique_caisse(ecole, jour), status=status.HTTP_201_CREATED)


class HistoriqueCaisseView(APIView):
    """
    Historique de caisse d'une journée : GET /api/caisse/historique/?date=AAAA-MM-JJ

    La direction et les profils financiers voient toute la caisse, et peuvent
    isoler un caissier avec `?caissier=<id>` ; un caissier ne voit que ses
    propres opérations, quoi qu'il demande.
    """
    permission_classes = [AccesModule]
    module = "journal_caisse"

    def get(self, request):
        ecole = request.user.id_ecole
        jour = _jour_demande(request)

        caissier = None
        if request.user.role == "caissier":
            caissier = request.user
        elif request.query_params.get("caissier"):
            try:
                caissier = get_user_model().objects.get(
                    pk=request.query_params["caissier"], id_ecole=ecole)
            except (get_user_model().DoesNotExist, ValueError, TypeError):
                raise ValidationError({"caissier": "Caissier inconnu pour cette école."})
        return Response(finances.historique_caisse(ecole, jour, caissier))


class GrilleTarifsView(APIView):
    """
    Grille des tarifs : GET /api/tarifs/grille/ la lit, POST l'enregistre.

    Une ligne par niveau, une colonne par type de frais. Une case vide applique
    le montant par défaut du frais ; seules les cases renseignées sont stockées.
    """
    permission_classes = [AccesModule]
    module = "types_frais"
    ORDRE_NATURES = {"inscription": 0, "mensualite": 1, "autre": 2}

    def _grille(self, ecole):
        niveaux = models.Niveau.objects.filter(id_ecole=ecole).order_by("libelle")
        # Les frais dus d'office en premier : ce sont les colonnes qu'on remplit toujours.
        types = sorted(models.TypeFrais.objects.filter(id_ecole=ecole),
                       key=lambda t: (self.ORDRE_NATURES.get(t.nature, 9), t.libelle.lower()))
        tarifs = models.TarifNiveau.objects.filter(id_niveau__id_ecole=ecole)
        return {
            "niveaux": [{"id": n.pk, "libelle": n.libelle} for n in niveaux],
            "types": [{
                "id": t.pk,
                "libelle": t.libelle,
                "nature": t.nature,
                "nature_libelle": t.get_nature_display(),
                "montant_defaut": finances.montant_json(t.montant_defaut),
                "frequence": t.frequence,
                "mensuel": finances.est_mensuel(t),
            } for t in types],
            "tarifs": {f"{t.id_niveau_id}-{t.id_type_frais_id}": finances.montant_json(t.montant)
                       for t in tarifs},
        }

    @extend_schema(responses=dict, summary="Grille des tarifs par niveau", tags=["finances"])
    def get(self, request):
        return Response(self._grille(request.user.id_ecole))

    @extend_schema(request=serializers.GrilleTarifsSerializer, responses=dict,
                   summary="Enregistrer la grille des tarifs", tags=["finances"])
    def post(self, request):
        entree = serializers.GrilleTarifsSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        ecole = request.user.id_ecole
        lignes = entree.validated_data["tarifs"]

        niveaux = set(models.Niveau.objects.filter(id_ecole=ecole).values_list("pk", flat=True))
        types = set(models.TypeFrais.objects.filter(id_ecole=ecole).values_list("pk", flat=True))
        if any(l["id_niveau"] not in niveaux or l["id_type_frais"] not in types for l in lignes):
            raise ValidationError({"tarifs": "Niveau ou frais inconnu pour cette école."})

        with transaction.atomic():
            for ligne in lignes:
                cle = {"id_niveau_id": ligne["id_niveau"], "id_type_frais_id": ligne["id_type_frais"]}
                if ligne.get("montant") is None:
                    models.TarifNiveau.objects.filter(**cle).delete()
                else:
                    models.TarifNiveau.objects.update_or_create(
                        **cle, defaults={"montant": ligne["montant"]})

        journaliser(request, models.JournalActivite.Action.MODIFICATION, entite="tarif_niveau",
                    description=f"Grille des tarifs : {len(lignes)} case(s) mise(s) à jour")
        return Response(self._grille(ecole))
