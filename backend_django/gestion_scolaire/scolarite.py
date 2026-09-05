"""
Cœur du module « Gestion des élèves » (Module 2 du TDR, ÉPIC 1 de la
priorisation fonctionnelle).

Les vues restent minces, comme pour les finances : tout ce qui fait bouger un
élève dans l'établissement — changement de classe, radiation, transfert,
réinscription d'une classe entière pour l'année suivante — vit ici.

Trois invariants sont tenus à cet endroit plutôt que dans les vues :

* **Aucun mouvement sans trace.** Chaque opération écrit une ligne
  d'historique scolaire. L'inscription initiale est déjà tracée par
  `Inscription.save()` ; les mouvements ultérieurs le sont ici, parce qu'eux
  seuls connaissent la classe quittée.
* **L'effectif suit le mouvement.** `Classe.effectif` est dénormalisé. Jusqu'à
  présent seul l'import de listes le recalait : une radiation ou un
  changement de classe laissait donc un effectif faux, et c'est un chiffre
  que la direction lit sur le tableau de bord. Toute opération qui déplace un
  élève repasse par `recaler_effectif()`.
* **L'école du compte fait foi.** Comme dans `finances`, chaque fonction
  vérifie que l'élève, la classe et l'année visés relèvent bien de l'école
  appelante — l'isolation multi-écoles se vérifie en écriture, pas seulement
  au filtrage des listes.

Convention de statuts, pour lever une ambiguïté récurrente :

* radiation → l'inscription en cours passe à `annulee` : elle n'ira pas à son
  terme, et l'élève passe en `radie`.
* transfert → l'inscription passe à `terminee` : le départ est organisé, la
  scolarité chez nous s'arrête proprement, l'élève passe en `transfere`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum

from . import models

ZERO = Decimal("0")


class ErreurScolarite(Exception):
    """Message destiné à être affiché tel quel à l'utilisateur."""


# ---------------------------------------------------------------------------
# Utilitaires partagés
# ---------------------------------------------------------------------------

# Une inscription annulée ne compte ni dans l'effectif, ni dans les élèves à
# réinscrire, ni dans les listes de classe.
INSCRIPTIONS_VIVES = ~Q(statut=models.Inscription.Statut.ANNULEE)


def recaler_effectif(*classes):
    """
    Remet `Classe.effectif` en accord avec les inscriptions réellement en base.

    Recalculer plutôt qu'incrémenter : un compteur qu'on incrémente dérive dès
    qu'un chemin d'écriture l'oublie, et il y en a plusieurs (import, endpoint
    composite, CRUD, réinscription en masse).
    """
    for classe in classes:
        if classe is None:
            continue
        classe.effectif = (
            models.Inscription.objects
            .filter(id_classe=classe)
            .filter(INSCRIPTIONS_VIVES)
            .count()
        )
        classe.save(update_fields=["effectif"])


def inscription_courante(eleve, annee=None):
    """
    Inscription active de l'élève, la plus récente si l'année n'est pas
    précisée. Renvoie `None` pour un élève jamais inscrit.
    """
    qs = (models.Inscription.objects
          .filter(id_eleve=eleve)
          .filter(INSCRIPTIONS_VIVES)
          .select_related("id_classe", "id_annee_scolaire"))
    if annee is not None:
        qs = qs.filter(id_annee_scolaire=annee)
    return qs.order_by("-date_inscription", "-pk").first()


def _verifier_ecole(ecole, **objets):
    """Refuse tout objet qui ne relève pas de l'école appelante."""
    for nom, objet in objets.items():
        if objet is None:
            continue
        proprietaire = getattr(objet, "id_ecole_id", None)
        if proprietaire is None:  # inscription, historique : on passe par l'élève
            proprietaire = objet.id_eleve.id_ecole_id
        if proprietaire != ecole.pk:
            raise ErreurScolarite(f"{nom.capitalize()} : cet élément appartient à une autre école.")


def _mouvement(eleve, type_evenement, date_effet, ancienne=None, nouvelle=None,
               motif="", commentaire=""):
    return models.HistoriqueScolaire.objects.create(
        id_eleve=eleve,
        type_evenement=type_evenement,
        id_ancienne_classe=ancienne,
        id_nouvelle_classe=nouvelle,
        date_evenement=date_effet,
        motif=motif or None,
        commentaire=commentaire or None,
    )


# ---------------------------------------------------------------------------
# Mouvements individuels (1.3 de la priorisation)
# ---------------------------------------------------------------------------

@transaction.atomic
def changer_de_classe(ecole, eleve, nouvelle_classe, date_effet=None, motif="",
                      commentaire=""):
    """
    Déplace un élève d'une classe à l'autre au sein de la même année.

    Le CRUD `/inscriptions/` sait déjà le faire par un PATCH, mais sans
    contrôle de capacité ni recalage d'effectif, et sans qu'on puisse joindre
    un motif au mouvement. Le passage par ici est le chemin nommé.
    """
    date_effet = date_effet or date.today()
    _verifier_ecole(ecole, eleve=eleve, classe=nouvelle_classe)

    inscription = inscription_courante(eleve)
    if inscription is None:
        raise ErreurScolarite("Cet élève n'a pas d'inscription active.")
    if inscription.id_classe_id == nouvelle_classe.pk:
        raise ErreurScolarite(f"{eleve.prenom} {eleve.nom} est déjà en {nouvelle_classe.nom}.")
    if nouvelle_classe.id_annee_scolaire_id != inscription.id_annee_scolaire_id:
        raise ErreurScolarite(
            "Cette classe relève d'une autre année scolaire : pour changer "
            "d'année, passez par la réinscription."
        )

    ancienne = inscription.id_classe
    inscription.id_classe = nouvelle_classe
    inscription.save(update_fields=["id_classe"])

    _mouvement(
        eleve, models.HistoriqueScolaire.TypeEvenement.CHANGEMENT_CLASSE, date_effet,
        ancienne=ancienne, nouvelle=nouvelle_classe,
        motif=motif or f"Passage de {ancienne.nom} à {nouvelle_classe.nom}",
        commentaire=commentaire,
    )
    recaler_effectif(ancienne, nouvelle_classe)
    return inscription


@transaction.atomic
def radier(ecole, eleve, date_effet=None, motif="", commentaire=""):
    """
    Radie un élève : il quitte l'établissement, son inscription est annulée.

    Le motif est exigé — une radiation sans motif écrit est ingérable quand
    la famille la conteste trois mois plus tard.
    """
    date_effet = date_effet or date.today()
    _verifier_ecole(ecole, eleve=eleve)
    if not (motif or "").strip():
        raise ErreurScolarite("Indiquez le motif de la radiation.")
    if eleve.statut == models.Eleve.Statut.RADIE:
        raise ErreurScolarite(f"{eleve.prenom} {eleve.nom} est déjà radié.")

    inscription = inscription_courante(eleve)
    ancienne = inscription.id_classe if inscription else None
    if inscription is not None:
        inscription.statut = models.Inscription.Statut.ANNULEE
        inscription.save(update_fields=["statut"])

    eleve.statut = models.Eleve.Statut.RADIE
    eleve.save(update_fields=["statut"])

    _mouvement(eleve, models.HistoriqueScolaire.TypeEvenement.RADIATION, date_effet,
               ancienne=ancienne, motif=motif, commentaire=commentaire)
    recaler_effectif(ancienne)
    return eleve


@transaction.atomic
def transferer(ecole, eleve, etablissement, date_effet=None, commentaire=""):
    """
    Transfère un élève vers un autre établissement.

    L'établissement d'accueil n'a pas de table dédiée : il est conservé dans le
    motif du mouvement, seul endroit où l'information sera relue (certificat
    de transfert, dossier de l'élève).
    """
    date_effet = date_effet or date.today()
    _verifier_ecole(ecole, eleve=eleve)
    if not (etablissement or "").strip():
        raise ErreurScolarite("Indiquez l'établissement d'accueil.")
    if eleve.statut == models.Eleve.Statut.TRANSFERE:
        raise ErreurScolarite(f"{eleve.prenom} {eleve.nom} a déjà été transféré.")

    inscription = inscription_courante(eleve)
    ancienne = inscription.id_classe if inscription else None
    if inscription is not None:
        inscription.statut = models.Inscription.Statut.TERMINEE
        inscription.save(update_fields=["statut"])

    eleve.statut = models.Eleve.Statut.TRANSFERE
    eleve.save(update_fields=["statut"])

    _mouvement(eleve, models.HistoriqueScolaire.TypeEvenement.TRANSFERT, date_effet,
               ancienne=ancienne, motif=f"Transfert vers {etablissement.strip()}",
               commentaire=commentaire)
    recaler_effectif(ancienne)
    return eleve


# ---------------------------------------------------------------------------
# Réinscription en masse (1.1 « réinscription »)
# ---------------------------------------------------------------------------

def eleves_a_reinscrire(ecole, annee_source, classe_source=None):
    """
    Élèves actifs dont l'inscription de l'année source n'a pas été annulée.

    Un élève radié ou transféré ne remonte pas : le sortir de la liste ici
    évite d'avoir à le décocher un par un dans l'écran.
    """
    qs = (models.Inscription.objects
          .filter(id_annee_scolaire=annee_source,
                  id_eleve__id_ecole=ecole,
                  id_eleve__statut=models.Eleve.Statut.ACTIF)
          .filter(INSCRIPTIONS_VIVES)
          .select_related("id_eleve", "id_classe"))
    if classe_source is not None:
        qs = qs.filter(id_classe=classe_source)
    return qs.order_by("id_eleve__nom", "id_eleve__prenom")


@transaction.atomic
def reinscrire(ecole, annee_source, annee_cible, classe_cible, classe_source=None,
               date_inscription=None, appliquer=True):
    """
    Réinscrit en bloc les élèves d'une classe dans une classe de l'année
    suivante.

    Même contrat que la génération des frais : `appliquer=False` renvoie le
    décompte sans rien écrire, pour que l'écran montre « 42 élèves passeraient
    de 6ème A en 5ème A » avant qu'on valide. Réinscrire une promotion entière
    est irréversible à la main.

    L'appel est idempotent : un élève déjà inscrit sur l'année cible est
    compté « ignoré », jamais dupliqué — la contrainte d'unicité
    (élève, année) l'interdirait de toute façon, mais mieux vaut un décompte
    lisible qu'une erreur d'intégrité.
    """
    date_inscription = date_inscription or annee_cible.date_debut or date.today()
    _verifier_ecole(ecole, annee_source=annee_source, annee_cible=annee_cible,
                    classe_cible=classe_cible, classe_source=classe_source)

    if annee_source.pk == annee_cible.pk:
        raise ErreurScolarite("L'année d'arrivée doit être différente de l'année de départ.")
    if classe_cible.id_annee_scolaire_id != annee_cible.pk:
        raise ErreurScolarite(
            f"La classe {classe_cible.nom} ne relève pas de l'année {annee_cible.libelle}."
        )
    if classe_source is not None and classe_source.id_annee_scolaire_id != annee_source.pk:
        raise ErreurScolarite(
            f"La classe {classe_source.nom} ne relève pas de l'année {annee_source.libelle}."
        )

    inscriptions = list(eleves_a_reinscrire(ecole, annee_source, classe_source))
    if not inscriptions:
        raise ErreurScolarite(
            "Aucun élève actif à réinscrire"
            + (f" en {classe_source.nom}." if classe_source is not None else
               f" pour {annee_source.libelle}.")
        )

    deja = set(
        models.Inscription.objects
        .filter(id_annee_scolaire=annee_cible,
                id_eleve__in=[i.id_eleve_id for i in inscriptions])
        .values_list("id_eleve_id", flat=True)
    )

    a_creer = [i.id_eleve for i in inscriptions if i.id_eleve_id not in deja]
    ignores = len(inscriptions) - len(a_creer)

    # L'effectif visé dépasse-t-il la capacité ? On avertit sans bloquer :
    # une classe surchargée en début d'année est une réalité, pas une erreur
    # de saisie, et la direction préfère être prévenue qu'empêchée.
    effectif_vise = classe_cible.effectif + len(a_creer)
    avertissement = None
    if classe_cible.capacite_max and effectif_vise > classe_cible.capacite_max:
        avertissement = (
            f"{classe_cible.nom} atteindrait {effectif_vise} élèves pour une "
            f"capacité de {classe_cible.capacite_max}."
        )

    if appliquer and a_creer:
        for eleve in a_creer:
            # Passe par save() plutôt que bulk_create : c'est Inscription.save()
            # qui écrit la trace « réinscription » dans l'historique.
            models.Inscription.objects.create(
                id_eleve=eleve,
                id_classe=classe_cible,
                id_annee_scolaire=annee_cible,
                date_inscription=date_inscription,
            )
        recaler_effectif(classe_cible)
        classe_cible.refresh_from_db(fields=["effectif"])

    return {
        "annee_source": annee_source.libelle,
        "annee_cible": annee_cible.libelle,
        "classe_source": classe_source.nom if classe_source is not None else "Toutes les classes",
        "classe_cible": classe_cible.nom,
        "date_inscription": date_inscription.isoformat(),
        "concernes": len(inscriptions),
        "reinscrits": len(a_creer),
        "ignores": ignores,
        "effectif_cible": classe_cible.effectif if appliquer else effectif_vise,
        "avertissement": avertissement,
        "applique": bool(appliquer),
    }


# ---------------------------------------------------------------------------
# Dossier numérique (1.2 de la priorisation)
# ---------------------------------------------------------------------------

def _nom(personne):
    return f"{personne.prenom} {personne.nom}".strip()


def dossier(ecole, eleve):
    """
    Fiche complète d'un élève, assemblée en une réponse.

    L'écran « dossier » a besoin de l'identité, des parents, du parcours, des
    pièces, de la situation financière et des résultats. Les récupérer par six
    appels distincts obligerait le front à recoudre lui-même des données qui
    n'ont de sens qu'ensemble, et multiplierait les allers-retours sur une
    connexion d'école. La vue reste mince, l'assemblage est ici.
    """
    _verifier_ecole(ecole, eleve=eleve)

    inscriptions = (models.Inscription.objects
                    .filter(id_eleve=eleve)
                    .select_related("id_classe", "id_annee_scolaire")
                    .order_by("-id_annee_scolaire__date_debut", "-date_inscription"))
    courante = inscription_courante(eleve)

    liens = (models.EleveParent.objects
             .filter(id_eleve=eleve)
             .select_related("id_parent")
             .order_by("-contact_principal", "id_parent__nom"))

    historique = (models.HistoriqueScolaire.objects
                  .filter(id_eleve=eleve)
                  .select_related("id_ancienne_classe", "id_nouvelle_classe")
                  .order_by("-date_evenement", "-pk"))

    pieces = models.DossierAdministratif.objects.filter(id_eleve=eleve).order_by("-date_ajout")

    bulletins = (models.Bulletin.objects
                 .filter(id_eleve=eleve)
                 .select_related("id_classe", "id_periode")
                 .order_by("-id_periode__date_debut"))

    # `FraisEleve.montant_paye` est une propriété Python, pas une colonne : la
    # somme des encaissements se prend sur les paiements rattachés. En deux
    # requêtes et non un `aggregate` unique — la jointure sur les paiements
    # dupliquerait les lignes de frais et gonflerait le total dû d'autant.
    du = (models.FraisEleve.objects.filter(id_eleve=eleve)
          .aggregate(total=Sum("montant_du"))["total"]) or ZERO
    paye = (models.Paiement.objects.filter(id_frais_eleve__id_eleve=eleve)
            .aggregate(total=Sum("montant"))["total"]) or ZERO

    absences = models.Absence.objects.filter(id_eleve=eleve).aggregate(
        absences=Count("pk", filter=Q(statut=models.Absence.Statut.ABSENT)),
        retards=Count("pk", filter=Q(statut=models.Absence.Statut.RETARD)),
        non_justifiees=Count("pk", filter=Q(justifiee=False)),
    )

    return {
        "eleve": {
            "id": eleve.pk,
            "matricule": eleve.matricule,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "sexe": eleve.get_sexe_display() if eleve.sexe else None,
            "date_naissance": eleve.date_naissance,
            "adresse": eleve.adresse,
            "photo_url": eleve.photo_url,
            "date_premiere_inscription": eleve.date_premiere_inscription,
            "statut": eleve.statut,
            "statut_libelle": eleve.get_statut_display(),
        },
        "scolarite_courante": None if courante is None else {
            "id_inscription": courante.pk,
            "classe": courante.id_classe.nom,
            "id_classe": courante.id_classe_id,
            "annee": courante.id_annee_scolaire.libelle,
            "id_annee_scolaire": courante.id_annee_scolaire_id,
            "date_inscription": courante.date_inscription,
            "statut": courante.get_statut_display(),
        },
        "parents": [{
            "id": lien.id_parent_id,
            "nom_complet": _nom(lien.id_parent),
            "lien": lien.get_lien_parente_display(),
            "telephone": lien.id_parent.telephone,
            "email": lien.id_parent.email,
            "contact_principal": lien.contact_principal,
        } for lien in liens],
        "inscriptions": [{
            "id": i.pk,
            "annee": i.id_annee_scolaire.libelle,
            "classe": i.id_classe.nom,
            "date_inscription": i.date_inscription,
            "statut": i.get_statut_display(),
        } for i in inscriptions],
        "historique": [{
            "date": h.date_evenement,
            "evenement": h.get_type_evenement_display(),
            "ancienne_classe": h.id_ancienne_classe.nom if h.id_ancienne_classe else None,
            "nouvelle_classe": h.id_nouvelle_classe.nom if h.id_nouvelle_classe else None,
            "motif": h.motif,
            "commentaire": h.commentaire,
        } for h in historique],
        "pieces": [{
            "id": p.pk,
            "type_piece": p.type_piece,
            "fichier_url": p.fichier_url,
            "date_ajout": p.date_ajout,
        } for p in pieces],
        "resultats": [{
            "periode": b.id_periode.libelle,
            "classe": b.id_classe.nom,
            "moyenne_generale": b.moyenne_generale,
            "rang_classe": b.rang_classe,
            "appreciation": b.appreciation_generale,
        } for b in bulletins],
        "finances": {
            "total_du": du,
            "total_paye": paye,
            "solde": du - paye,
            "frais_impayes": models.FraisEleve.objects.filter(
                id_eleve=eleve,
                statut__in=[models.FraisEleve.Statut.IMPAYE,
                            models.FraisEleve.Statut.PARTIEL],
            ).count(),
        },
        "assiduite": absences,
    }
