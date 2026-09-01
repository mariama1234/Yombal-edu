"""
Journal d'activité (audit).

Un journal qui n'est jamais alimenté ne sert à rien : `JournalisationMixin`
s'ajoute aux ViewSets pour tracer automatiquement création / modification /
suppression, et `journaliser()` couvre les cas hors CRUD (connexion, export).
"""
from .models import JournalActivite


def _adresse_ip(request):
    # Derrière un reverse proxy, X-Forwarded-For contient la vraie IP client en tête.
    transmise = request.META.get("HTTP_X_FORWARDED_FOR")
    if transmise:
        return transmise.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def journaliser(request, action, id_ecole=None, entite=None, id_entite=None, description=None,
                utilisateur=None):
    """
    Écrit une entrée dans le journal. Ne lève jamais : une erreur d'audit ne
    doit pas faire échouer l'opération métier qu'elle accompagne.

    `utilisateur` sert aux cas où request.user n'est pas encore renseigné —
    typiquement la connexion, journalisée avant que l'authentification n'ait eu lieu.
    """
    try:
        if utilisateur is None:
            utilisateur = getattr(request, "user", None)
        if utilisateur is not None and not utilisateur.is_authenticated:
            utilisateur = None

        if id_ecole is None and utilisateur is not None:
            id_ecole = utilisateur.id_ecole_id
        if id_ecole is None:
            return None

        return JournalActivite.objects.create(
            id_ecole_id=id_ecole,
            id_utilisateur=utilisateur,
            action=action,
            entite=entite,
            id_entite=id_entite,
            description=(description or "")[:255] or None,
            adresse_ip=_adresse_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255] or None,
        )
    except Exception:  # noqa: BLE001 — l'audit ne doit jamais casser la requête
        return None


class JournalisationMixin:
    """
    À ajouter aux ViewSets en écriture. `entite_journal` nomme l'entité dans
    le journal ; par défaut le nom de la table du modèle.
    """
    entite_journal = None

    def _entite(self, obj):
        if self.entite_journal:
            return self.entite_journal
        return obj._meta.db_table

    def _journaliser_objet(self, action, obj):
        journaliser(
            self.request,
            action,
            id_ecole=getattr(obj, "id_ecole_id", None),
            entite=self._entite(obj),
            id_entite=obj.pk,
            description=str(obj),
        )

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._journaliser_objet(JournalActivite.Action.CREATION, serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._journaliser_objet(JournalActivite.Action.MODIFICATION, serializer.instance)

    def perform_destroy(self, instance):
        # Capturé avant la suppression : ensuite la pk et le __str__ sont perdus.
        entite, pk, libelle = self._entite(instance), instance.pk, str(instance)
        id_ecole = getattr(instance, "id_ecole_id", None)
        super().perform_destroy(instance)
        journaliser(
            self.request,
            JournalActivite.Action.SUPPRESSION,
            id_ecole=id_ecole,
            entite=entite,
            id_entite=pk,
            description=libelle,
        )
