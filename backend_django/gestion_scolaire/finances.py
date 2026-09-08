"""
Cœur du module financier (Module 7 du TDR).

Les vues restent minces : tout ce qui demande un calcul — solde de caisse,
génération des frais d'une classe, relances, bilan, exports — vit ici.

Deux choix structurants :

* **Aucune dépendance nouvelle.** Le `.xlsx` s'appuie sur `openpyxl`, déjà
  présent pour l'import des listes d'élèves ; le PDF est écrit directement
  (voir `exports.py`), comme l'import lit déjà le `.docx` sans bibliothèque.
  Une école qui déploie l'application ne doit pas avoir à installer un moteur
  de rendu pour imprimer un reçu.
* **Le journal de caisse est la seule source du solde.** Chaque encaissement
  et chaque dépense y écrit un mouvement, et `solde_apres` est calculé à
  l'insertion à partir des mouvements déjà en base : le solde se lit sans
  rejouer toute l'histoire.
"""
from __future__ import annotations

from calendar import monthrange
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from . import models

ZERO = Decimal("0")
_SOMME = DecimalField(max_digits=14, decimal_places=2)


class ErreurFinance(Exception):
    """Message destiné à être affiché tel quel à l'utilisateur."""


def _somme(champ, filtre=None):
    """Somme qui vaut 0 plutôt que None quand il n'y a aucune ligne."""
    return Coalesce(Sum(champ, filter=filtre), Value(ZERO), output_field=_SOMME)


def montant_json(valeur):
    """
    Montant prêt à être sérialisé en JSON.

    Le bilan est un agrégat de lecture destiné à des compteurs et des
    graphiques : un float s'y manipule directement côté Angular, là où les
    chaînes renvoyées par DRF pour les `DecimalField` obligeraient à parser
    chaque valeur. Les écritures, elles, restent en `Decimal` de bout en bout.
    """
    return float(valeur or 0)


# ---------------------------------------------------------------------------
# Journal de caisse
# ---------------------------------------------------------------------------

def solde(ecole, type_journal=None, a_la_date=None):
    """Solde courant d'un journal (crédits − débits)."""
    qs = models.JournalCaisse.objects.filter(id_ecole=ecole)
    if type_journal:
        qs = qs.filter(type_journal=type_journal)
    if a_la_date:
        qs = qs.filter(date_mouvement__lte=a_la_date)
    agrege = qs.aggregate(credit=_somme("credit"), debit=_somme("debit"))
    return agrege["credit"] - agrege["debit"]


def enregistrer_mouvement(ecole, type_journal, date_mouvement, libelle,
                          credit=ZERO, debit=ZERO, paiement=None, depense=None,
                          utilisateur=None):
    """Écrit un mouvement et renseigne le solde qui en résulte."""
    credit = credit or ZERO
    debit = debit or ZERO
    return models.JournalCaisse.objects.create(
        id_ecole=ecole,
        id_paiement=paiement,
        id_depense=depense,
        type_journal=type_journal,
        date_mouvement=date_mouvement,
        libelle=libelle[:255],
        credit=credit,
        debit=debit,
        solde_apres=solde(ecole, type_journal) + credit - debit,
        id_utilisateur=utilisateur if (utilisateur and utilisateur.is_authenticated) else None,
    )


def journal_du_paiement(paiement):
    """Espèces → caisse ; mobile money et carte bancaire → banque."""
    if paiement.methode == models.Paiement.Methode.ESPECES:
        return models.JournalCaisse.TypeJournal.CAISSE
    return models.JournalCaisse.TypeJournal.BANQUE


def enregistrer_paiement(paiement, utilisateur=None):
    """
    Retombées d'un encaissement : statut du frais, reçu, mouvement de caisse.

    Regroupé ici parce que les trois vont ensemble — un paiement sans reçu ni
    mouvement laisserait la caisse fausse et le parent sans justificatif.
    """
    frais = paiement.id_frais_eleve
    frais.statut = (models.FraisEleve.Statut.PAYE if frais.solde_restant <= 0
                    else models.FraisEleve.Statut.PARTIEL)
    frais.save(update_fields=["statut"])

    recu = models.Recu.objects.create(
        id_paiement=paiement, numero_recu=f"REC-{paiement.pk:06d}")

    enregistrer_mouvement(
        frais.id_eleve.id_ecole,
        journal_du_paiement(paiement),
        paiement.date_paiement.date(),
        f"Paiement {frais.id_type_frais.get_libelle_display()} — {frais.id_eleve}",
        credit=paiement.montant,
        paiement=paiement,
        utilisateur=utilisateur,
    )
    return recu


def enregistrer_depense(depense, utilisateur=None):
    """
    Contrepartie d'une dépense : un débit au journal, et la consommation
    imputée au budget de l'année s'il en existe un pour la catégorie.

    Sans ça, le bilan affichait des recettes sans les charges en face et le
    « budget réalisé » restait à zéro quoi qu'on dépense.
    """
    mouvement = enregistrer_mouvement(
        depense.id_ecole,
        models.JournalCaisse.TypeJournal.CAISSE,
        depense.date_depense,
        f"Dépense {depense.id_type_depense.libelle} — {depense.motif}",
        debit=depense.montant,
        depense=depense,
        utilisateur=utilisateur,
    )
    imputer_au_budget(depense)
    return mouvement


def imputer_au_budget(depense):
    """
    Recalcule le réalisé des budgets couvrant cette dépense.

    Recalculé plutôt qu'incrémenté : une dépense corrigée ou supprimée laisse
    sinon un réalisé qui ne correspond plus à aucune ligne.
    """
    budgets = models.Budget.objects.filter(
        id_ecole=depense.id_ecole,
        id_type_depense=depense.id_type_depense,
        id_annee_scolaire__date_debut__lte=depense.date_depense,
        id_annee_scolaire__date_fin__gte=depense.date_depense,
    ).select_related("id_annee_scolaire")
    for budget in budgets:
        budget.montant_realise = (
            models.Depense.objects
            .filter(id_ecole=budget.id_ecole_id,
                    id_type_depense=budget.id_type_depense_id,
                    date_depense__gte=budget.id_annee_scolaire.date_debut,
                    date_depense__lte=budget.id_annee_scolaire.date_fin)
            .aggregate(total=_somme("montant"))["total"]
        )
        budget.save(update_fields=["montant_realise"])


# ---------------------------------------------------------------------------
# Génération des frais
# ---------------------------------------------------------------------------

def echeances_mensuelles(premiere, nombre):
    """
    Suite d'échéances au même jour du mois, à partir de `premiere`.

    Le jour est ramené au dernier jour du mois quand il n'existe pas (un
    paiement fixé au 31 est dû le 28 ou le 29 en février).
    """
    dates = []
    for decalage in range(nombre):
        mois = premiere.month - 1 + decalage
        annee = premiere.year + mois // 12
        mois = mois % 12 + 1
        jour = min(premiere.day, monthrange(annee, mois)[1])
        dates.append(date(annee, mois, jour))
    return dates


def eleves_concernes(ecole, annee, classe=None):
    """Élèves actifs inscrits pour l'année (optionnellement d'une seule classe)."""
    inscrits = models.Inscription.objects.filter(
        id_annee_scolaire=annee,
        id_eleve__id_ecole=ecole,
        id_eleve__statut=models.Eleve.Statut.ACTIF,
    ).exclude(statut=models.Inscription.Statut.ANNULEE)
    if classe is not None:
        inscrits = inscrits.filter(id_classe=classe)
    return (models.Eleve.objects
            .filter(pk__in=inscrits.values("id_eleve"))
            .order_by("nom", "prenom"))


@transaction.atomic
def generer_frais(ecole, annee, type_frais, echeances, montant=None, classe=None,
                  appliquer=True):
    """
    Crée un frais par élève et par échéance.

    C'est la brique qui manquait pour que le module serve : sans elle, chaque
    mensualité devait être saisie élève par élève. Dix mensualités pour 600
    élèves, ce sont 6 000 lignes — impensable à la main.

    L'appel est idempotent : un frais déjà présent pour le même élève, le même
    type et la même échéance est compté « ignoré », jamais dupliqué. Relancer
    la génération après l'arrivée de trois inscrits ne crée que leurs frais.

    `appliquer=False` renvoie le même décompte sans rien écrire, ce qui permet
    d'afficher un aperçu avant validation — comme l'import d'élèves.
    """
    if type_frais.id_ecole_id != ecole.pk:
        raise ErreurFinance("Ce type de frais appartient à une autre école.")
    if annee.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cette année scolaire appartient à une autre école.")
    if classe is not None and classe.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cette classe appartient à une autre école.")
    if not echeances:
        raise ErreurFinance("Indiquez au moins une échéance.")

    try:
        montant = Decimal(montant) if montant not in (None, "") else type_frais.montant_defaut
    except (InvalidOperation, TypeError):
        raise ErreurFinance("Montant illisible.")
    if montant <= 0:
        raise ErreurFinance("Le montant d'un frais doit être strictement positif.")

    eleves = list(eleves_concernes(ecole, annee, classe))
    if not eleves:
        raise ErreurFinance(
            "Aucun élève inscrit pour cette année"
            + (f" en {classe.nom}." if classe is not None else ".")
        )

    deja = set(
        models.FraisEleve.objects
        .filter(id_type_frais=type_frais, id_annee_scolaire=annee, id_eleve__in=eleves)
        .values_list("id_eleve_id", "echeance")
    )

    a_creer = []
    ignores = 0
    for eleve in eleves:
        for echeance in echeances:
            if (eleve.pk, echeance) in deja:
                ignores += 1
                continue
            a_creer.append(models.FraisEleve(
                id_eleve=eleve, id_type_frais=type_frais, id_annee_scolaire=annee,
                montant_du=montant, echeance=echeance,
                statut=models.FraisEleve.Statut.IMPAYE,
            ))

    if appliquer and a_creer:
        models.FraisEleve.objects.bulk_create(a_creer, batch_size=500)

    return {
        "type_frais": type_frais.get_libelle_display(),
        "classe": classe.nom if classe is not None else "Toutes les classes",
        "eleves": len(eleves),
        "echeances": [e.isoformat() for e in echeances],
        "montant": montant_json(montant),
        "crees": len(a_creer),
        "ignores": ignores,
        "total_genere": montant_json(montant * len(a_creer)),
        "applique": bool(appliquer),
    }


# ---------------------------------------------------------------------------
# Relances
# ---------------------------------------------------------------------------

def frais_a_relancer(ecole, jours_retard=0, delai_min=7, aujourdhui=None):
    """
    Frais échus et non soldés, hors ceux déjà relancés récemment.

    `delai_min` évite le travers classique de la relance automatique : trois
    SMS le même jour au même parent parce que la tâche a été lancée trois fois.
    """
    aujourdhui = aujourdhui or timezone.localdate()
    limite = aujourdhui - timedelta(days=jours_retard)
    relance_recente = Q(relances__date_envoi__date__gt=aujourdhui - timedelta(days=delai_min))
    return (models.FraisEleve.objects
            .filter(id_eleve__id_ecole=ecole, echeance__lte=limite)
            .exclude(statut=models.FraisEleve.Statut.PAYE)
            .exclude(relance_recente)
            .select_related("id_eleve", "id_type_frais")
            .distinct())


@transaction.atomic
def generer_relances(ecole, canal, jours_retard=0, delai_min=7, appliquer=True):
    """Une relance par frais échu impayé. Retourne le détail pour affichage."""
    if canal not in models.Relance.Canal.values:
        raise ErreurFinance(f"Canal inconnu : {canal}.")

    frais = list(frais_a_relancer(ecole, jours_retard, delai_min))
    if appliquer and frais:
        models.Relance.objects.bulk_create(
            [models.Relance(id_frais_eleve=f, canal=canal,
                            statut=models.Relance.Statut.ENVOYEE) for f in frais],
            batch_size=500,
        )
    return {
        "canal": canal,
        "relances": len(frais),
        "montant_relance": montant_json(sum((f.solde_restant for f in frais), ZERO)),
        "eleves": sorted({f"{f.id_eleve.prenom} {f.id_eleve.nom}" for f in frais})[:50],
        "applique": bool(appliquer),
    }


# ---------------------------------------------------------------------------
# Bilan financier
# ---------------------------------------------------------------------------

def impayes_par_classe(ecole, annee):
    """
    Reste à recouvrer, classe par classe.

    « Il reste douze millions à encaisser » ne dit pas où aller chercher :
    c'est la classe qui donne la liste d'appel et le professeur principal par
    qui passer. Le classement se fait sur le reste dû, décroissant.

    Deux requêtes plutôt qu'un `aggregate` unique : joindre les frais *et*
    les paiements dans la même requête multiplie les lignes de frais par le
    nombre de paiements et gonfle le montant dû d'autant.
    """
    if annee.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cette année scolaire appartient à une autre école.")

    inscriptions = (models.Inscription.objects
                    .filter(id_annee_scolaire=annee, id_eleve__id_ecole=ecole)
                    .exclude(statut=models.Inscription.Statut.ANNULEE))

    facture = {
        ligne["id_classe"]: ligne
        for ligne in inscriptions.values("id_classe", "id_classe__nom").annotate(
            du=_somme("id_eleve__frais__montant_du",
                      Q(id_eleve__frais__id_annee_scolaire=annee)),
            eleves=Count("id_eleve", distinct=True),
        )
    }

    encaisse = {
        ligne["id_classe"]: ligne["paye"]
        for ligne in models.Paiement.objects
        .filter(id_frais_eleve__id_annee_scolaire=annee,
                id_frais_eleve__id_eleve__id_ecole=ecole,
                id_frais_eleve__id_eleve__inscriptions__id_annee_scolaire=annee)
        .values(id_classe=F("id_frais_eleve__id_eleve__inscriptions__id_classe"))
        .annotate(paye=_somme("montant"))
    }

    # Élèves dont au moins une échéance est dépassée : ce sont eux qu'on
    # relance en premier, indépendamment du montant.
    aujourdhui = timezone.localdate()
    en_retard = {
        ligne["id_classe"]: ligne["nombre"]
        for ligne in inscriptions
        .filter(id_eleve__frais__id_annee_scolaire=annee,
                id_eleve__frais__echeance__lt=aujourdhui)
        .exclude(id_eleve__frais__statut=models.FraisEleve.Statut.PAYE)
        .values("id_classe")
        .annotate(nombre=Count("id_eleve", distinct=True))
    }

    lignes = []
    for id_classe, ligne in facture.items():
        du = ligne["du"] or ZERO
        paye = encaisse.get(id_classe, ZERO) or ZERO
        reste = du - paye
        if du <= ZERO:
            continue
        lignes.append({
            "id_classe": id_classe,
            "classe": ligne["id_classe__nom"],
            "eleves": ligne["eleves"],
            "eleves_en_retard": en_retard.get(id_classe, 0),
            "du": montant_json(du),
            "paye": montant_json(paye),
            "reste": montant_json(reste),
            "taux": float(round(paye * 100 / du, 1)) if du else 0.0,
        })

    lignes.sort(key=lambda l: l["reste"], reverse=True)
    return {
        "annee": annee.libelle,
        "id_annee_scolaire": annee.pk,
        "total_reste": montant_json(sum(Decimal(str(l["reste"])) for l in lignes)),
        "classes": lignes,
    }


def bilan(ecole, annee=None, debut=None, fin=None):
    """
    Photographie financière de l'école : recouvrement, recettes, dépenses,
    trésorerie, budgets et évolution mensuelle.

    Sans année ni bornes, tout l'historique est pris en compte ; avec une
    année, la période par défaut est celle de l'année scolaire.
    """
    if annee is not None:
        debut = debut or annee.date_debut
        fin = fin or annee.date_fin

    frais = models.FraisEleve.objects.filter(id_eleve__id_ecole=ecole)
    paiements = models.Paiement.objects.filter(id_frais_eleve__id_eleve__id_ecole=ecole)
    depenses = models.Depense.objects.filter(id_ecole=ecole)
    if annee is not None:
        frais = frais.filter(id_annee_scolaire=annee)
        paiements = paiements.filter(id_frais_eleve__id_annee_scolaire=annee)
    if debut is not None:
        paiements = paiements.filter(date_paiement__date__gte=debut)
        depenses = depenses.filter(date_depense__gte=debut)
    if fin is not None:
        paiements = paiements.filter(date_paiement__date__lte=fin)
        depenses = depenses.filter(date_depense__lte=fin)

    attendu = frais.aggregate(t=_somme("montant_du"))["t"]
    encaisse = paiements.aggregate(t=_somme("montant"))["t"]
    total_depenses = depenses.aggregate(t=_somme("montant"))["t"]

    # Le reste à recouvrer se lit sur les frais, pas sur `attendu - encaisse` :
    # un paiement peut être daté hors période alors que le frais, lui, est dû.
    solde_frais = frais.aggregate(
        du=_somme("montant_du"),
        paye=_somme("paiements__montant"),
    )
    reste = solde_frais["du"] - solde_frais["paye"]

    aujourdhui = timezone.localdate()
    impayes = frais.exclude(statut=models.FraisEleve.Statut.PAYE)

    libelles = dict(models.TypeFrais.Libelle.choices)
    methodes = dict(models.Paiement.Methode.choices)

    par_type = (paiements
                .values("id_frais_eleve__id_type_frais__libelle")
                .annotate(montant=_somme("montant"), nombre=Count("id"))
                .order_by("-montant"))
    par_methode = (paiements.values("methode")
                   .annotate(montant=_somme("montant"), nombre=Count("id"))
                   .order_by("-montant"))
    par_categorie = (depenses.values("id_type_depense__libelle")
                     .annotate(montant=_somme("montant"), nombre=Count("id"))
                     .order_by("-montant"))

    budgets = models.Budget.objects.filter(id_ecole=ecole)
    if annee is not None:
        budgets = budgets.filter(id_annee_scolaire=annee)

    return {
        "periode": {
            "id_annee_scolaire": annee.pk if annee else None,
            "annee": annee.libelle if annee else None,
            "debut": debut.isoformat() if debut else None,
            "fin": fin.isoformat() if fin else None,
        },
        "recouvrement": {
            "attendu": montant_json(attendu),
            "encaisse": montant_json(encaisse),
            "reste": montant_json(reste),
            "taux": round(float(solde_frais["paye"] / solde_frais["du"] * 100), 1)
                    if solde_frais["du"] else 0.0,
        },
        "impayes": {
            "nombre": impayes.count(),
            "montant": montant_json(reste),
            "echus": impayes.filter(echeance__lt=aujourdhui).count(),
            "eleves_concernes": impayes.values("id_eleve").distinct().count(),
        },
        "recettes": {
            "total": montant_json(encaisse),
            "par_type_frais": [
                {"code": l["id_frais_eleve__id_type_frais__libelle"],
                 "libelle": libelles.get(l["id_frais_eleve__id_type_frais__libelle"],
                                         l["id_frais_eleve__id_type_frais__libelle"]),
                 "montant": montant_json(l["montant"]), "nombre": l["nombre"]}
                for l in par_type
            ],
            "par_methode": [
                {"code": l["methode"], "libelle": methodes.get(l["methode"], l["methode"]),
                 "montant": montant_json(l["montant"]), "nombre": l["nombre"]}
                for l in par_methode
            ],
        },
        "depenses": {
            "total": montant_json(total_depenses),
            "par_categorie": [
                {"libelle": l["id_type_depense__libelle"],
                 "montant": montant_json(l["montant"]), "nombre": l["nombre"]}
                for l in par_categorie
            ],
        },
        "resultat": {
            "recettes": montant_json(encaisse),
            "depenses": montant_json(total_depenses),
            "solde": montant_json(encaisse - total_depenses),
        },
        "tresorerie": {
            "caisse": montant_json(solde(ecole, models.JournalCaisse.TypeJournal.CAISSE)),
            "banque": montant_json(solde(ecole, models.JournalCaisse.TypeJournal.BANQUE)),
            "total": montant_json(solde(ecole)),
        },
        "budgets": [
            {
                "categorie": b.categorie,
                "type_depense": b.id_type_depense.libelle,
                "prevu": montant_json(b.montant_prevu),
                "realise": montant_json(b.montant_realise),
                "ecart": montant_json(b.montant_prevu - b.montant_realise),
                "consomme": round(float(b.montant_realise / b.montant_prevu * 100), 1)
                            if b.montant_prevu else 0.0,
            }
            for b in budgets.select_related("id_type_depense")
        ],
        "evolution": _evolution(paiements, depenses),
    }


def _evolution(paiements, depenses):
    """Recettes et dépenses agrégées par mois, sur l'union des deux séries."""
    mois = OrderedDict()

    def _poser(cle):
        return mois.setdefault(cle, {"mois": cle, "recettes": 0.0, "depenses": 0.0})

    for ligne in (paiements.annotate(m=TruncMonth("date_paiement"))
                  .values("m").annotate(total=_somme("montant")).order_by("m")):
        if ligne["m"]:
            _poser(ligne["m"].strftime("%Y-%m"))["recettes"] = montant_json(ligne["total"])

    for ligne in (depenses.annotate(m=TruncMonth("date_depense"))
                  .values("m").annotate(total=_somme("montant")).order_by("m")):
        if ligne["m"]:
            _poser(ligne["m"].strftime("%Y-%m"))["depenses"] = montant_json(ligne["total"])

    return [mois[c] for c in sorted(mois)]
