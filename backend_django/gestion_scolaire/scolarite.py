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

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import APIException

from . import finances, models, photos


class ErreurScolarite(Exception):
    """Message destiné à être affiché tel quel à l'utilisateur."""


# ---------------------------------------------------------------------------
# Utilitaires partagés
# ---------------------------------------------------------------------------

# Une inscription annulée ne compte ni dans l'effectif, ni dans les élèves à
# réinscrire, ni dans les listes de classe.
INSCRIPTIONS_VIVES = ~Q(statut=models.Inscription.Statut.ANNULEE)

# Un pré-inscrit a bien une inscription vive — sa place est réservée, ses frais
# sont émis — mais il ne compte pas encore parmi les inscrits de l'année. La
# distinction ne sert qu'aux listes d'élèves : l'effectif d'une classe, lui,
# compte les places réservées, sinon on les réattribuerait à quelqu'un d'autre.
INSCRIPTIONS_CONFIRMEES = Q(statut__in=models.Inscription.STATUTS_CONFIRMES)


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


# ---------------------------------------------------------------------------
# Âge par niveau
# ---------------------------------------------------------------------------

# Grille validée avec le client (fichier « Contraintes d'âge par niveau ») :
# âge minimum et maximum, en années révolues à la date de référence. Elle ne
# sert qu'à préremplir les niveaux ; chaque école ajuste ensuite les siens.
GRILLE_AGES = {
    "petite section": (2, 4), "moyenne section": (3, 5), "grande section": (4, 5),
    "ci": (5, 7), "cp": (6, 8), "ce1": (7, 9), "ce2": (8, 10), "cm1": (9, 11), "cm2": (10, 12),
    "6eme": (12, 13), "5eme": (12, 14), "4eme": (13, 15), "3eme": (14, 16),
    "seconde": (15, 17), "premiere": (16, 19), "terminale": (17, 20),
}

# Les mêmes niveaux sous les noms qu'on leur donne couramment.
_SYNONYMES_NIVEAU = {
    "ps": "petite section", "ms": "moyenne section", "gs": "grande section",
    "6e": "6eme", "5e": "5eme", "4e": "4eme", "3e": "3eme",
    "2nde": "seconde", "2nd": "seconde", "1ere": "premiere", "tle": "terminale", "terminal": "terminale",
}


def _cle_niveau(libelle):
    import unicodedata

    texte = unicodedata.normalize("NFKD", libelle or "").encode("ascii", "ignore").decode()
    texte = " ".join(texte.lower().replace("-", " ").split())
    return _SYNONYMES_NIVEAU.get(texte, texte)


def fourchette_par_defaut(libelle):
    """(âge min, âge max) de la grille pour ce libellé, ou `None` s'il n'y figure pas."""
    return GRILLE_AGES.get(_cle_niveau(libelle))


class ControleAge:
    """Réglage de l'école (paramètre `controle_age_inscription`)."""
    PREVENIR = "prevenir"
    BLOQUER = "bloquer"
    DESACTIVE = "desactive"
    CHOIX = (PREVENIR, BLOQUER, DESACTIVE)


def _parametre(ecole, cle):
    return (models.ParametreEcole.objects.filter(id_ecole=ecole, cle=cle)
            .values_list("valeur", flat=True).first())


def mode_controle_age(ecole):
    valeur = (_parametre(ecole, "controle_age_inscription") or "").strip().lower()
    return valeur if valeur in ControleAge.CHOIX else ControleAge.PREVENIR


def date_reference_age(ecole, annee):
    """
    Le jour où l'on mesure l'âge, dans l'année civile où commence l'année
    scolaire. Paramètre `date_reference_age` au format JJ/MM, 31/12 par défaut.
    """
    jour, mois = 31, 12
    valeur = (_parametre(ecole, "date_reference_age") or "").strip()
    try:
        j, m = (int(x) for x in valeur.replace("-", "/").split("/")[:2])
        date(2000, m, j)  # 29/02 accepté, le reste validé
        jour, mois = j, m
    except (TypeError, ValueError):
        pass
    annee_civile = annee.date_debut.year
    if (jour, mois) == (29, 2) and not (annee_civile % 4 == 0 and (annee_civile % 100 or annee_civile % 400 == 0)):
        jour = 28
    return date(annee_civile, mois, jour)


def age_au(date_naissance, reference):
    """Âge en années révolues."""
    return (reference.year - date_naissance.year
            - ((reference.month, reference.day) < (date_naissance.month, date_naissance.day)))


def _fourchette_lisible(niveau):
    if niveau.age_min is not None and niveau.age_max is not None:
        return f"de {niveau.age_min} à {niveau.age_max} ans"
    if niveau.age_min is not None:
        return f"à partir de {niveau.age_min} ans"
    return f"jusqu'à {niveau.age_max} ans"


def controle_age(ecole, classe, date_naissance, prenom=""):
    """
    Confronte l'âge de l'élève à la fourchette du niveau de `classe`.

    Renvoie `None` si rien n'est à signaler — contrôle désactivé, classe sans
    niveau, niveau sans fourchette, âge dans la fourchette — sinon un dict :

    * `code` : `date_naissance_manquante` ou `age_hors_fourchette` ;
    * `message` : la phrase à montrer telle quelle ;
    * `bloquant` : vrai si l'école bloque, ou s'il manque la date de naissance
      (on ne peut rien vérifier : on la demande, quelle que soit l'école).
    """
    mode = mode_controle_age(ecole)
    niveau = classe.id_niveau if classe.id_niveau_id else None
    if (mode == ControleAge.DESACTIVE or niveau is None
            or (niveau.age_min is None and niveau.age_max is None)):
        return None

    qui = prenom.strip() or "L'élève"
    if date_naissance is None:
        return {
            "code": "date_naissance_manquante",
            "message": f"Renseignez la date de naissance de {qui} : "
                       f"le niveau {niveau.libelle} accepte {_fourchette_lisible(niveau)}.",
            "bloquant": True,
        }

    reference = date_reference_age(ecole, classe.id_annee_scolaire)
    age = age_au(date_naissance, reference)
    if ((niveau.age_min is None or age >= niveau.age_min)
            and (niveau.age_max is None or age <= niveau.age_max)):
        return None
    return {
        "code": "age_hors_fourchette",
        "message": f"{qui} a {age} ans au {reference:%d/%m/%Y}. "
                   f"Le niveau {niveau.libelle} accepte {_fourchette_lisible(niveau)}.",
        "bloquant": mode == ControleAge.BLOQUER,
        "age": age,
    }


def exiger_age_conforme(ecole, classe, date_naissance, prenom="", derogation=False):
    """
    Applique `controle_age` à une inscription individuelle.

    Lève `ErreurAge` quand il faut s'arrêter : date manquante, école qui
    bloque, ou avertissement que la personne n'a pas encore confirmé. Renvoie
    le message de la dérogation à inscrire dans l'historique, ou `None`.
    """
    alerte = controle_age(ecole, classe, date_naissance, prenom)
    if alerte is None:
        return None
    if alerte["bloquant"] or not derogation:
        raise ErreurAge(alerte)
    return alerte["message"]


class ErreurAge(ErreurScolarite, APIException):
    """
    Âge à vérifier : une réponse 400 dont le `code` dit au client quoi faire.

    * `age_a_confirmer` : avertissement, renvoyer la requête avec
      `derogation_age` pour passer outre ;
    * `age_hors_fourchette` : l'école bloque, rien à confirmer ;
    * `date_naissance_manquante` : la saisir (message sous ce champ).

    `APIException` plutôt que `ValidationError` : levée depuis un serializer,
    elle traverse `is_valid()` sans être mise en listes, et le corps reste le
    même quel que soit le chemin d'inscription.
    """
    status_code = 400

    def __init__(self, alerte):
        ErreurScolarite.__init__(self, alerte["message"])
        self.alerte = alerte
        if alerte["code"] == "date_naissance_manquante":
            corps = {"date_naissance": alerte["message"], "code": alerte["code"]}
        else:
            corps = {"detail": alerte["message"],
                     "code": "age_hors_fourchette" if alerte["bloquant"] else "age_a_confirmer"}
        APIException.__init__(self, corps)


def tracer_derogation_age(eleve, classe, date_effet, message, utilisateur=None):
    """La dérogation reste lisible dans l'historique de l'élève, avec son auteur."""
    auteur = (f"Confirmée par {utilisateur.prenom} {utilisateur.nom}".strip()
              if utilisateur is not None and getattr(utilisateur, "is_authenticated", False) else None)
    return _mouvement(eleve, models.HistoriqueScolaire.TypeEvenement.DEROGATION_AGE, date_effet,
                      nouvelle=classe, motif=message[:255], commentaire=auteur)


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
               date_inscription=None, appliquer=True, eleves=None, derogation_age=False,
               utilisateur=None):
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

    C'est un cas d'exception : une classe ne revient pas entière d'une année
    sur l'autre — des élèves partent, redoublent, changent de classe. `eleves`
    restreint donc la réinscription aux identifiants cochés à l'aperçu. Les
    autres restent dans la file « à réinscrire », d'où l'on peut les
    réinscrire ailleurs ou enregistrer leur départ.

    L'aperçu porte, élève par élève, l'alerte d'âge du niveau visé. À la
    validation, un élève bloqué (date de naissance manquante, ou école qui
    bloque) arrête tout : on le décoche ou on complète sa fiche. Un simple
    avertissement exige `derogation_age`, et chaque dérogation est tracée.
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

    candidats = {i.id_eleve_id for i in inscriptions}
    selection = None if eleves is None else {int(e) for e in eleves}
    if selection is not None and selection - candidats:
        raise ErreurScolarite(
            f"{len(selection - candidats)} élève(s) coché(s) ne font pas partie des élèves "
            "à réinscrire" + (f" de {classe_source.nom}." if classe_source is not None else ".")
        )

    def retenu(inscription):
        return (inscription.id_eleve_id not in deja
                and (selection is None or inscription.id_eleve_id in selection))

    a_creer = [i.id_eleve for i in inscriptions if retenu(i)]
    ignores = sum(1 for i in inscriptions if i.id_eleve_id in deja)
    non_retenus = len(inscriptions) - ignores - len(a_creer)
    if appliquer and selection is not None and not a_creer:
        raise ErreurScolarite("Aucun élève coché : rien à réinscrire.")

    alertes = {i.id_eleve_id: controle_age(ecole, classe_cible, i.id_eleve.date_naissance,
                                           i.id_eleve.prenom)
               for i in inscriptions if i.id_eleve_id not in deja}
    en_alerte = [e for e in a_creer if alertes.get(e.pk)]
    if appliquer and en_alerte:
        bloques = [e for e in en_alerte if alertes[e.pk]["bloquant"]]
        if bloques:
            raise ErreurScolarite(
                f"{len(bloques)} élève(s) ne peuvent pas passer en {classe_cible.nom} : "
                + " ; ".join(alertes[e.pk]["message"] for e in bloques[:5])
                + (" …" if len(bloques) > 5 else "")
                + " Décochez-les ou complétez leur fiche.")
        if not derogation_age:
            raise ErreurAge({
                "code": "age_hors_fourchette",
                "message": f"{len(en_alerte)} élève(s) hors de la fourchette d'âge : "
                           + " ; ".join(alertes[e.pk]["message"] for e in en_alerte[:5])
                           + (" …" if len(en_alerte) > 5 else ""),
                "bloquant": False,
            })

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

    frais_emis = 0
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
            if alertes.get(eleve.pk):
                tracer_derogation_age(eleve, classe_cible, date_inscription,
                                      alertes[eleve.pk]["message"], utilisateur)
            # Même règle qu'à l'inscription : droits et mensualités d'office,
            # et les frais au choix de l'an passé reconduits.
            frais_emis += len(finances.emettre_frais_inscription(
                eleve, annee_cible, date_inscription,
                finances.types_optionnels_de(eleve, annee_source), classe=classe_cible))
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
        "frais_emis": frais_emis,
        "ignores": ignores,
        "non_retenus": non_retenus,
        # La liste à cocher de l'écran : qui suit la classe, qui ne revient pas.
        "eleves": [{
            "id": i.id_eleve_id,
            "matricule": i.id_eleve.matricule,
            "nom_complet": f"{i.id_eleve.prenom} {i.id_eleve.nom}".strip(),
            "deja_inscrit": i.id_eleve_id in deja,
            "retenu": retenu(i),
            "alerte_age": alertes.get(i.id_eleve_id),
        } for i in inscriptions],
        "alertes_age": sum(1 for e in a_creer if alertes.get(e.pk)),
        "effectif_cible": classe_cible.effectif if appliquer else effectif_vise,
        "avertissement": avertissement,
        "applique": bool(appliquer),
    }


# ---------------------------------------------------------------------------
# Réinscription d'un élève seul
# ---------------------------------------------------------------------------

def eleves_sans_inscription(ecole, annee, classe_source=None, niveau_source=None,
                            recherche=None):
    """
    Élèves déjà passés par l'école mais qui n'ont aucune inscription sur
    `annee` — la file d'attente de la rentrée.

    La réinscription en masse traite une classe entière d'un coup ; elle ne
    répond pas au cas courant de « il en reste douze, et chacun va dans une
    classe différente ». Sans cette liste, ces douze-là disparaissaient
    simplement de l'écran Élèves dès qu'on affichait la nouvelle année, sans
    que rien ne signale qu'ils attendaient.

    Chaque ligne porte la dernière classe connue : c'est elle qui dit vers
    quoi l'élève doit monter.
    """
    _verifier_ecole(ecole, annee=annee)

    deja = models.Inscription.objects.filter(id_annee_scolaire=annee).values("id_eleve")
    precedentes = (models.Inscription.objects
                   .filter(id_eleve__id_ecole=ecole,
                           id_annee_scolaire__date_debut__lt=annee.date_debut)
                   .filter(INSCRIPTIONS_VIVES)
                   .exclude(id_eleve__in=deja)
                   .select_related("id_eleve", "id_classe", "id_classe__id_niveau",
                                   "id_annee_scolaire"))
    if classe_source is not None:
        precedentes = precedentes.filter(id_classe=classe_source)
    if niveau_source is not None:
        precedentes = precedentes.filter(id_classe__id_niveau=niveau_source)
    if recherche:
        terme = str(recherche).strip()
        precedentes = precedentes.filter(
            Q(id_eleve__nom__icontains=terme)
            | Q(id_eleve__prenom__icontains=terme)
            | Q(id_eleve__matricule__icontains=terme)
        )

    # Un élève peut avoir plusieurs années derrière lui : on ne garde que la
    # plus récente, c'est la seule qui renseigne sur son niveau actuel.
    derniere = {}
    for inscription in precedentes.order_by("id_annee_scolaire__date_debut"):
        derniere[inscription.id_eleve_id] = inscription

    lignes = []
    for inscription in derniere.values():
        eleve = inscription.id_eleve
        # Un élève radié ou transféré n'attend pas une réinscription : il est
        # parti. Le proposer reviendrait à demander douze fois par jour
        # pourquoi il est encore là.
        if eleve.statut != models.Eleve.Statut.ACTIF:
            continue
        lignes.append({
            "id": eleve.pk,
            "matricule": eleve.matricule,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "nom_complet": f"{eleve.prenom} {eleve.nom}".strip(),
            "sexe": eleve.sexe,
            "derniere_annee": inscription.id_annee_scolaire.libelle,
            "id_derniere_classe": inscription.id_classe_id,
            "derniere_classe": inscription.id_classe.nom,
            "id_dernier_niveau": inscription.id_classe.id_niveau_id,
            "dernier_niveau": inscription.id_classe.libelle_niveau,
        })
    lignes.sort(key=lambda l: (l["derniere_classe"], l["nom"], l["prenom"]))
    return lignes


@transaction.atomic
def reinscrire_eleve(ecole, eleve, classe_cible, date_inscription=None, frais_optionnels=None,
                     derogation_age=False, utilisateur=None):
    """
    Réinscrit **un** élève dans une classe de l'année suivante.

    L'inscription naît « incomplète » : la place est retenue et les frais sont
    émis, mais l'élève n'entre dans la liste des inscrits qu'une fois les
    droits d'inscription et le premier mois réglés
    (voir `finances.confirmer_inscription`).

    Le dépassement de capacité avertit sans bloquer, comme pour la
    réinscription en masse : une classe surchargée à la rentrée est une
    réalité, pas une faute de saisie.

    L'âge, lui, est contrôlé selon le réglage de l'école : hors fourchette, la
    réinscription attend la confirmation `derogation_age`, tracée ensuite.
    """
    _verifier_ecole(ecole, eleve=eleve, classe=classe_cible)

    annee_cible = classe_cible.id_annee_scolaire
    date_inscription = date_inscription or annee_cible.date_debut or date.today()

    if eleve.statut != models.Eleve.Statut.ACTIF:
        raise ErreurScolarite(
            f"{eleve.prenom} {eleve.nom} n'est plus actif "
            f"({eleve.get_statut_display().lower()}) : il ne peut pas être réinscrit."
        )

    existante = (models.Inscription.objects
                 .filter(id_eleve=eleve, id_annee_scolaire=annee_cible)
                 .select_related("id_classe").first())
    if existante is not None:
        raise ErreurScolarite(
            f"{eleve.prenom} {eleve.nom} est déjà inscrit en "
            f"{existante.id_classe.nom} pour {annee_cible.libelle}."
        )

    precedente = (models.Inscription.objects
                  .filter(id_eleve=eleve)
                  .filter(INSCRIPTIONS_VIVES)
                  .exclude(id_annee_scolaire=annee_cible)
                  .order_by("-id_annee_scolaire__date_debut").first())
    if precedente is None:
        raise ErreurScolarite(
            f"{eleve.prenom} {eleve.nom} n'a aucune scolarité antérieure : "
            "passez par « Inscrire un élève »."
        )

    # Sans choix explicite, les frais au choix de l'an passé sont reconduits.
    if frais_optionnels is None:
        frais_optionnels = finances.types_optionnels_de(eleve, precedente.id_annee_scolaire)
    elif any(t.id_ecole_id != ecole.pk for t in frais_optionnels):
        raise ErreurScolarite("Ce type de frais appartient à une autre école.")

    derogation = exiger_age_conforme(ecole, classe_cible, eleve.date_naissance, eleve.prenom,
                                     derogation=derogation_age)

    inscription = models.Inscription.objects.create(
        id_eleve=eleve,
        id_classe=classe_cible,
        id_annee_scolaire=annee_cible,
        date_inscription=date_inscription,
    )
    if derogation:
        tracer_derogation_age(eleve, classe_cible, date_inscription, derogation, utilisateur)
    recaler_effectif(classe_cible)
    classe_cible.refresh_from_db(fields=["effectif"])
    frais = finances.emettre_frais_inscription(
        eleve, annee_cible, date_inscription, frais_optionnels, classe=classe_cible)

    avertissement = None
    if classe_cible.capacite_max and classe_cible.effectif > classe_cible.capacite_max:
        avertissement = (
            f"{classe_cible.nom} compte {classe_cible.effectif} élèves pour une "
            f"capacité de {classe_cible.capacite_max}."
        )

    return {
        "id_inscription": inscription.pk,
        "id_eleve": eleve.pk,
        "eleve": f"{eleve.prenom} {eleve.nom}".strip(),
        "annee_precedente": precedente.id_annee_scolaire.libelle,
        "classe_precedente": precedente.id_classe.nom,
        "annee": annee_cible.libelle,
        "id_annee_scolaire": annee_cible.pk,
        "classe": classe_cible.nom,
        "id_classe": classe_cible.pk,
        "date_inscription": date_inscription.isoformat(),
        "statut": inscription.statut,
        "statut_libelle": inscription.get_statut_display(),
        "frais_emis": len(frais),
        "effectif_classe": classe_cible.effectif,
        "avertissement": avertissement,
    }


# ---------------------------------------------------------------------------
# Dossier numérique (1.2 de la priorisation)
# ---------------------------------------------------------------------------

def _droits_regles(inscription):
    """Les droits d'inscription de l'année sont émis et entièrement réglés."""
    reste = finances.reste_dinscription(inscription.id_eleve_id, inscription.id_annee_scolaire_id)
    return reste is not None and reste <= 0


def autoriser_en_classe(ecole, eleve, motif, utilisateur=None):
    """
    Exception : un inscrit incomplet est autorisé à suivre les cours.

    Le cas visé est celui d'une famille en difficulté passagère : les droits
    d'inscription sont réglés, le premier mois ne l'est pas encore. Sans droits
    réglés, pas d'exception — ce serait une inscription gratuite.

    L'inscription garde son statut « Inscrit incomplet » : l'autorisation ne
    vaut pas règlement, et la confirmation viendra d'elle-même au paiement.
    Motif, date et auteur sont conservés : une exception qu'on ne sait plus
    justifier ne tient pas longtemps.
    """
    _verifier_ecole(ecole, eleve=eleve)
    inscription = inscription_courante(eleve)
    if inscription is None:
        raise ErreurScolarite("Cet élève n'a aucune inscription en cours.")
    if inscription.statut != models.Inscription.Statut.EN_ATTENTE:
        raise ErreurScolarite(
            f"{_nom(eleve)} est déjà inscrit : aucune autorisation n'est nécessaire.")
    if not _droits_regles(inscription):
        raise ErreurScolarite(
            "Les droits d'inscription doivent être réglés avant d'autoriser l'entrée en classe.")
    motif = (motif or "").strip()
    if len(motif) < 3:
        raise ErreurScolarite("Indiquez le motif de l'autorisation.")

    inscription.autorise_en_classe = True
    inscription.motif_autorisation = motif[:255]
    inscription.date_autorisation = timezone.now()
    inscription.id_utilisateur_autorisation = (
        utilisateur if utilisateur is not None and utilisateur.is_authenticated else None)
    inscription.save(update_fields=["autorise_en_classe", "motif_autorisation",
                                    "date_autorisation", "id_utilisateur_autorisation"])
    return inscription


def retirer_autorisation(ecole, eleve):
    """Revient sur l'exception : l'inscrit incomplet ne suit plus les cours."""
    _verifier_ecole(ecole, eleve=eleve)
    inscription = inscription_courante(eleve)
    if inscription is None or not inscription.autorise_en_classe:
        raise ErreurScolarite("Cet élève n'a pas d'autorisation d'entrer en classe à retirer.")
    inscription.autorise_en_classe = False
    inscription.motif_autorisation = None
    inscription.date_autorisation = None
    inscription.id_utilisateur_autorisation = None
    inscription.save(update_fields=["autorise_en_classe", "motif_autorisation",
                                    "date_autorisation", "id_utilisateur_autorisation"])
    return inscription


def _nom(personne):
    return f"{personne.prenom} {personne.nom}".strip()


CHAMPS_SANTE = ("groupe_sanguin", "allergies", "maladies_chroniques", "traitements",
                "besoins_particuliers", "vaccinations_a_jour", "medecin_nom", "medecin_telephone",
                "contact_urgence_nom", "contact_urgence_telephone", "remarques")


def fiche_sanitaire(eleve):
    """La fiche de santé, vide (champs à `None`) tant que personne ne l'a remplie."""
    fiche = models.FicheSanitaire.objects.filter(id_eleve=eleve).select_related("id_utilisateur_maj").first()
    donnees = {champ: getattr(fiche, champ, None) for champ in CHAMPS_SANTE}
    donnees["date_maj"] = fiche.date_maj if fiche else None
    donnees["maj_par"] = (_nom(fiche.id_utilisateur_maj)
                          if fiche and fiche.id_utilisateur_maj_id else None)
    return donnees


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

    # Situation financière de l'année en cours, et d'elle seule.
    #
    # Le dossier agrégeait auparavant toutes les années confondues : l'ardoise
    # de CE2 s'additionnait à celle de CM2, et le total affiché ne
    # correspondait à aucune facture réelle. Le détail complet — frais,
    # échéancier, paiements — vit dans `finances.situation_eleve`, appelé par
    # l'onglet Finances ; ici on n'en garde que le résumé.
    annee_financiere = courante.id_annee_scolaire if courante is not None else None
    situation = finances.situation_eleve(ecole, eleve, annee_financiere)
    resume = situation["resume"]

    absences = models.Absence.objects.filter(id_eleve=eleve).aggregate(
        absences=Count("pk", filter=Q(statut=models.Absence.Statut.ABSENT)),
        retards=Count("pk", filter=Q(statut=models.Absence.Statut.RETARD)),
        non_justifiees=Count("pk", filter=Q(justifiee=False)),
    )

    return {
        "eleve": {
            "id": eleve.pk,
            "matricule": eleve.matricule,
            "numero_registre": eleve.numero_registre,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "sexe": eleve.get_sexe_display() if eleve.sexe else None,
            "date_naissance": eleve.date_naissance,
            "adresse": eleve.adresse,
            "photo_url": eleve.photo_url,
            # La photo se lit par GET /api/eleves/{id}/photo/, jamais par une URL publique.
            "a_photo": photos.chemin(eleve) is not None,
            # Compte de connexion de l'élève : l'écran propose de le créer.
            "a_un_acces": eleve.id_utilisateur_id is not None,
            "acces_email": eleve.id_utilisateur.email if eleve.id_utilisateur_id else None,
            "date_premiere_inscription": eleve.date_premiere_inscription,
            "statut": eleve.statut,
            "statut_libelle": eleve.get_statut_display(),
        },
        # Section financière : la vue la retire à qui ne lit pas les frais.
        "cas_social": {
            "actif": eleve.cas_social,
            "taux": eleve.taux_reduction_sociale,
            "motif": eleve.motif_cas_social,
        },
        # Idem pour la santé, réservée au module « sante ».
        "sante": fiche_sanitaire(eleve),
        "scolarite_courante": None if courante is None else {
            "id_inscription": courante.pk,
            "classe": courante.id_classe.nom,
            "id_classe": courante.id_classe_id,
            "annee": courante.id_annee_scolaire.libelle,
            "id_annee_scolaire": courante.id_annee_scolaire_id,
            "date_inscription": courante.date_inscription,
            "statut": courante.get_statut_display(),
            "statut_code": courante.statut,
            "autorise_en_classe": courante.autorise_en_classe,
            "motif_autorisation": courante.motif_autorisation,
            "date_autorisation": courante.date_autorisation,
            "autorise_par": (_nom(courante.id_utilisateur_autorisation)
                             if courante.id_utilisateur_autorisation_id else None),
            "droits_regles": _droits_regles(courante),
        },
        "parents": [{
            "id": lien.id_parent_id,
            "nom_complet": _nom(lien.id_parent),
            "lien": lien.get_lien_parente_display(),
            "telephone": lien.id_parent.telephone,
            "email": lien.id_parent.email,
            "adresse": lien.id_parent.adresse,
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
            "id_annee_scolaire": (situation["annee"]["id"] if situation["annee"] else None),
            "annee": (situation["annee"]["libelle"] if situation["annee"] else None),
            "frais_initial": resume["frais_initial"],
            "remise": resume["remise"],
            "total_du": resume["total_du"],
            "total_paye": resume["total_paye"],
            "solde": resume["reste"],
            "statut": resume["statut"],
            "statut_libelle": resume["statut_libelle"],
            "frais_impayes": resume["nombre_frais"] - resume["frais_soldes"],
            "frais_en_retard": resume["frais_en_retard"],
            "prochaine_echeance": resume["prochaine_echeance"],
        },
        "assiduite": absences,
    }
