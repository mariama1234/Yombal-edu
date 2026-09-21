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

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from . import models

ZERO = Decimal("0")
_SOMME = DecimalField(max_digits=14, decimal_places=2)

# Ce qu'une famille doit réellement pour un frais : le tarif moins la remise.
# Exprimé en SQL pour que les agrégats n'aient pas à charger les lignes ;
# `FraisEleve.montant_net` en est l'équivalent côté Python.
NET = F("montant_du") - F("remise")

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


class StatutFinancier:
    """
    Lecture d'ensemble de la situation d'un élève (ou d'une classe).

    Distinct de `FraisEleve.Statut`, qui ne qualifie qu'une ligne de frais :
    ici on répond à « où en est cet élève ? », et « en retard » l'emporte sur
    « partiel » — c'est l'échéance dépassée qui déclenche la relance, pas le
    montant restant.
    """
    A_JOUR = "a_jour"
    PARTIEL = "partiel"
    EN_RETARD = "en_retard"
    NON_PAYE = "non_paye"
    # « Rien n'a été facturé » n'est pas « tout est réglé ». Les deux donnent
    # un reste à zéro, pour des raisons opposées : un élève pré-inscrit dont
    # les frais n'ont pas encore été émis s'affichait « À jour » alors qu'il
    # n'avait pas versé un franc.
    SANS_FRAIS = "sans_frais"

    LIBELLES = {
        A_JOUR: "À jour",
        PARTIEL: "Partiel",
        EN_RETARD: "En retard",
        NON_PAYE: "Non payé",
        SANS_FRAIS: "Aucun frais",
    }

    VALEURS = (A_JOUR, PARTIEL, EN_RETARD, NON_PAYE, SANS_FRAIS)


def statut_financier(du, paye, en_retard=False, complet=True):
    """
    Statut d'ensemble d'un jeu de frais, à partir des montants.

    « À jour » exige qu'il y ait eu quelque chose à payer *et* que ce soit
    payé : sans facture émise, on ne dit rien de la famille, on dit qu'on ne
    lui a rien demandé.

    Il exige aussi que tous les frais qui reviennent à l'élève aient été émis
    (`complet`, voir `frais_d_office_manquants`). Un élève dont la facture ne
    porte que les droits d'inscription, sans sa mensualité, a tout réglé de
    cette facture sans avoir payé ce qu'il doit : il n'est pas à jour.
    """
    du = Decimal(str(du or 0))
    paye = Decimal(str(paye or 0))
    if du <= ZERO:
        return StatutFinancier.SANS_FRAIS
    if paye >= du:
        return StatutFinancier.A_JOUR if complet else StatutFinancier.PARTIEL
    if en_retard:
        return StatutFinancier.EN_RETARD
    return StatutFinancier.PARTIEL if paye > ZERO else StatutFinancier.NON_PAYE


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
    recalculer_statut(frais)
    confirmer_inscription(frais.id_eleve_id, frais.id_annee_scolaire_id)

    recu = models.Recu.objects.create(
        id_paiement=paiement, numero_recu=f"REC-{paiement.pk:06d}")

    enregistrer_mouvement(
        frais.id_eleve.id_ecole,
        journal_du_paiement(paiement),
        paiement.date_paiement.date(),
        f"Paiement {frais.id_type_frais.libelle} — {frais.id_eleve}",
        credit=paiement.montant,
        paiement=paiement,
        utilisateur=utilisateur,
    )
    return recu


def frais_dinscription(id_eleve, id_annee_scolaire):
    """Les lignes de frais de type « inscription » de l'année, paiements chargés."""
    return list(models.FraisEleve.objects
                .filter(id_eleve_id=id_eleve, id_annee_scolaire_id=id_annee_scolaire,
                        id_type_frais__nature=models.TypeFrais.Nature.INSCRIPTION)
                .prefetch_related("paiements"))


def reste_dinscription(id_eleve, id_annee_scolaire):
    """
    Ce qu'il reste à régler sur le frais d'inscription.

    `None` quand aucun frais d'inscription n'a été émis : c'est un cas
    distinct de « il ne reste rien à payer », et l'écran doit le dire — sans
    ce frais, l'élève ne peut pas être confirmé.
    """
    lignes = frais_dinscription(id_eleve, id_annee_scolaire)
    if not lignes:
        return None
    return sum((f.solde_restant for f in lignes), ZERO)


def premier_mois(id_eleve, id_annee_scolaire):
    """La première mensualité de l'année, paiements chargés ; `None` si aucune n'est émise."""
    return (models.FraisEleve.objects
            .filter(id_eleve_id=id_eleve, id_annee_scolaire_id=id_annee_scolaire,
                    id_type_frais__nature=models.TypeFrais.Nature.MENSUALITE,
                    echeance__isnull=False)
            .order_by("echeance", "pk")
            .prefetch_related("paiements")
            .first())


def confirmer_inscription(id_eleve, id_annee_scolaire):
    """
    Fait passer un « inscrit incomplet » à « Inscrit » une fois les **droits
    d'inscription et le premier mois intégralement réglés**.

    C'est la règle de l'école : réserver une place ne fait pas un inscrit, et
    les droits seuls non plus — l'élève est inscrit quand il a payé l'entrée
    et son premier mois. L'un sans l'autre laisse l'inscription incomplète.

    L'automatisme est posé sur l'encaissement plutôt que sur un bouton parce
    que tous les chemins de paiement convergent ici — le guichet, l'imputation
    automatique, un futur webhook — et qu'un élève qui a payé mais que
    personne n'a « validé » resterait invisible des listes, des bulletins et
    des appels. Une remise qui solde le frais d'inscription confirme au même
    titre : c'est le solde qui compte, pas l'origine des fonds.

    Renvoie l'inscription confirmée, ou `None` s'il n'y avait rien à faire —
    y compris quand aucun frais d'inscription n'a encore été émis.
    """
    inscription = (models.Inscription.objects
                   .filter(id_eleve_id=id_eleve, id_annee_scolaire_id=id_annee_scolaire,
                           statut=models.Inscription.Statut.EN_ATTENTE)
                   .first())
    if inscription is None:
        return None

    reste = reste_dinscription(id_eleve, id_annee_scolaire)
    if reste is None or reste > ZERO:
        return None
    mois = premier_mois(id_eleve, id_annee_scolaire)
    if mois is None or mois.solde_restant > ZERO:
        return None

    inscription.statut = models.Inscription.Statut.EN_COURS
    inscription.save(update_fields=["statut"])
    return inscription


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


def annoter_consommation(queryset, annee=None):
    """
    Ajoute à chaque catégorie de dépense ce qu'elle a déjà consommé.

    Le disponible s'en déduit : budget alloué moins `montant_depense`. Le
    calcul est fait en SQL, sur la fenêtre de l'année scolaire visée, pour que
    la liste des catégories tienne en une requête quel que soit le nombre de
    dépenses. Sans année — une école qui démarre n'en a pas encore — tout
    l'historique compte, faute de fenêtre à appliquer.
    """
    fenetre = None
    if annee is not None:
        fenetre = Q(depenses__date_depense__gte=annee.date_debut,
                    depenses__date_depense__lte=annee.date_fin)
    return queryset.annotate(montant_depense=_somme("depenses__montant", fenetre))


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

    # Montant saisi : il s'impose à tous. Vide : chaque élève paie le tarif de
    # son niveau (inscription, mensualité), à défaut le montant du type.
    impose = None
    if montant not in (None, ""):
        try:
            impose = Decimal(montant)
        except (InvalidOperation, TypeError):
            raise ErreurFinance("Montant illisible.")
        if impose <= 0:
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

    classes = {
        i.id_eleve_id: i.id_classe
        for i in models.Inscription.objects
        .filter(id_annee_scolaire=annee, id_eleve__in=eleves)
        .exclude(statut=models.Inscription.Statut.ANNULEE)
        .select_related("id_classe__id_niveau")
    }

    grille = grille_du_type(type_frais)
    a_creer = []
    ignores = 0
    sans_tarif = 0
    for eleve in eleves:
        montant_eleve = impose if impose is not None else tarif(type_frais, classes.get(eleve.pk), grille)
        if not montant_eleve or montant_eleve <= 0:
            sans_tarif += 1
            continue
        for echeance in echeances:
            if (eleve.pk, echeance) in deja:
                ignores += 1
                continue
            a_creer.append(models.FraisEleve(
                id_eleve=eleve, id_type_frais=type_frais, id_annee_scolaire=annee,
                montant_du=montant_eleve, echeance=echeance,
                statut=models.FraisEleve.Statut.IMPAYE,
            ))
    if sans_tarif and not a_creer and not ignores:
        raise ErreurFinance(
            "Aucun montant à facturer : saisissez un montant, ou renseignez le tarif "
            "du niveau ou le montant par défaut du type de frais."
        )

    montants = {f.montant_du for f in a_creer}
    if impose is not None:
        montant_affiche = impose
    elif len(montants) == 1:
        montant_affiche = next(iter(montants))
    elif not montants:
        montant_affiche = type_frais.montant_defaut
    else:
        montant_affiche = None  # varie selon le niveau de chaque élève

    if appliquer and a_creer:
        models.FraisEleve.objects.bulk_create(a_creer, batch_size=500)

    return {
        "type_frais": type_frais.libelle,
        "classe": classe.nom if classe is not None else "Toutes les classes",
        "eleves": len(eleves),
        "echeances": [e.isoformat() for e in echeances],
        "montant": None if montant_affiche is None else montant_json(montant_affiche),
        "crees": len(a_creer),
        "ignores": ignores,
        "total_genere": montant_json(sum((f.montant_du for f in a_creer), ZERO)),
        "applique": bool(appliquer),
    }


TYPES_D_OFFICE = (models.TypeFrais.Nature.INSCRIPTION, models.TypeFrais.Nature.MENSUALITE)


def est_mensuel(type_frais):
    """La mensualité, ou un type dont la fréquence dit « mensuel » (transport, cantine…)."""
    if type_frais.nature == models.TypeFrais.Nature.MENSUALITE:
        return True
    return "mens" in (type_frais.frequence or "").lower()


def tarif(type_frais, classe=None, grille=None):
    """
    Montant d'un frais pour un élève de cette classe.

    Chaque frais peut varier selon le niveau — l'inscription en 3ème n'est pas
    celle de 4ème — d'après la grille des tarifs. Sans montant pour le niveau
    de la classe, c'est le montant par défaut du frais qui s'applique.

    `grille` ({id_niveau: montant}, voir `grille_du_type`) évite une requête
    par élève quand on facture une classe entière.
    """
    if classe is not None and classe.id_niveau_id:
        if grille is not None:
            montant = grille.get(classe.id_niveau_id)
        else:
            montant = (models.TarifNiveau.objects
                       .filter(id_niveau_id=classe.id_niveau_id, id_type_frais=type_frais)
                       .values_list("montant", flat=True).first())
        if montant is not None:
            return montant
    return type_frais.montant_defaut


def grille_du_type(type_frais):
    """`{id_niveau: montant}` d'un type de frais, pour facturer toute une classe."""
    return dict(models.TarifNiveau.objects.filter(id_type_frais=type_frais)
                .values_list("id_niveau_id", "montant"))


def types_d_office(ecole):
    """Les types de nature « inscription » puis « mensualité » de l'école."""
    types = []
    for nature in TYPES_D_OFFICE:
        type_frais = (models.TypeFrais.objects
                      .filter(id_ecole=ecole, nature=nature).order_by("pk").first())
        if type_frais is not None:
            types.append(type_frais)
    return types


def frais_d_office_manquants(types, natures_presentes):
    """
    Libellés des frais dus d'office (droits d'inscription, mensualité) dont
    aucune ligne n'a été émise, parmi `types` (voir `types_d_office`).
    """
    return [t.libelle for t in types if t.nature not in natures_presentes]


def eleves_aux_frais_incomplets(ecole, frais):
    """
    `{id_eleve: [libellés manquants]}` pour les élèves du jeu `frais` à qui il
    manque un frais dû d'office, sur l'une des années de ce jeu.

    Une seule requête pour toute une promotion : les natures présentes par
    élève et par année suffisent.
    """
    types = types_d_office(ecole)
    if not types:
        return {}
    presentes = {}
    for id_eleve, id_annee, nature in (frais.order_by()
                                       .values_list("id_eleve_id", "id_annee_scolaire_id",
                                                    "id_type_frais__nature")
                                       .distinct()):
        presentes.setdefault((id_eleve, id_annee), set()).add(nature)
    manquants = {}
    for (id_eleve, _), natures in presentes.items():
        for libelle in frais_d_office_manquants(types, natures):
            libelles = manquants.setdefault(id_eleve, [])
            if libelle not in libelles:
                libelles.append(libelle)
    return manquants


@transaction.atomic
def emettre_frais_inscription(eleve, annee, date_inscription, optionnels=(), classe=None):
    """
    La facture d'un nouvel inscrit : droits d'inscription, première mensualité
    et les frais cochés par la personne qui inscrit (transport, cantine…).

    Chaque ligne est émise une fois, due le jour de l'inscription : c'est le
    total que la famille présente à la caisse. Les mois suivants se génèrent
    ensuite depuis Finances, au tarif du niveau. L'ordre de création — droits,
    mensualité, puis frais cochés — est aussi l'ordre d'imputation d'un
    règlement : payer droits + une mensualité suffit à confirmer l'inscription,
    quoi qu'on ait coché par ailleurs.
    """
    types = types_d_office(eleve.id_ecole)
    deja_retenus = {t.pk for t in types}
    types += [t for t in optionnels if t.pk not in deja_retenus]

    deja = set(models.FraisEleve.objects
               .filter(id_eleve=eleve, id_annee_scolaire=annee)
               .values_list("id_type_frais_id", "echeance"))
    crees = []
    for type_frais in types:
        montant = tarif(type_frais, classe)
        if not montant or montant <= 0 or (type_frais.pk, date_inscription) in deja:
            continue
        # Une à une plutôt qu'en bloc : l'ordre des clés fixe l'ordre d'imputation.
        crees.append(models.FraisEleve.objects.create(
            id_eleve=eleve, id_type_frais=type_frais, id_annee_scolaire=annee,
            montant_du=montant, echeance=date_inscription,
            statut=models.FraisEleve.Statut.IMPAYE,
        ))
    return crees


def facture(eleve, annee):
    """
    La facture de l'élève pour l'année : ce qui est dû, réglé et restant.

    Sert au document remis à l'inscription, à l'écran de la caisse qui l'ouvre
    par le matricule, et au reçu, qui en reprend le montant restant.
    """
    lignes = list(models.FraisEleve.objects
                  .filter(id_eleve=eleve, id_annee_scolaire=annee)
                  .select_related("id_type_frais")
                  .prefetch_related("paiements")
                  .order_by("echeance", "pk"))
    mensuels = {f.id_type_frais_id for f in lignes if est_mensuel(f.id_type_frais)}
    inscription = (eleve.inscriptions.filter(id_annee_scolaire=annee)
                   .exclude(statut=models.Inscription.Statut.ANNULEE)
                   .select_related("id_classe").first())
    du = sum((f.montant_net for f in lignes), ZERO)
    paye = sum((f.montant_paye for f in lignes), ZERO)
    return {
        "id_eleve": eleve.pk,
        "matricule": eleve.matricule,
        "nom": eleve.nom,
        "prenom": eleve.prenom,
        "classe": inscription.id_classe.nom if inscription else None,
        "id_annee_scolaire": annee.pk,
        "annee": annee.libelle,
        "statut_inscription": inscription.statut if inscription else None,
        "statut_inscription_libelle": inscription.get_statut_display() if inscription else None,
        "lignes": [{
            "id": f.pk,
            "libelle": _libelle_frais(f, mensuels),
            "echeance": f.echeance.isoformat() if f.echeance else None,
            "montant": montant_json(f.montant_net),
            "paye": montant_json(f.montant_paye),
            "reste": montant_json(f.solde_restant),
        } for f in lignes],
        "total_du": montant_json(du),
        "total_paye": montant_json(paye),
        "reste": montant_json(du - paye),
    }


def types_optionnels_de(eleve, annee):
    """
    Les frais au choix que la famille avait cette année-là (transport, cantine…).

    À la réinscription, ils sont reconduits : une famille ne renonce pas au
    transport parce que l'enfant passe de classe, et les recocher un par un
    pour toute une promotion serait le meilleur moyen d'en oublier.
    """
    return list(models.TypeFrais.objects
                .filter(frais_eleves__id_eleve=eleve, frais_eleves__id_annee_scolaire=annee)
                .exclude(nature__in=TYPES_D_OFFICE)
                .distinct().order_by("pk"))


def _inscription_de_lannee(eleve, annee=None):
    """L'inscription non annulée de l'élève sur `annee`, à défaut la plus récente."""
    qs = (models.Inscription.objects
          .filter(id_eleve=eleve)
          .exclude(statut=models.Inscription.Statut.ANNULEE)
          .select_related("id_classe", "id_annee_scolaire"))
    if annee is not None:
        qs = qs.filter(id_annee_scolaire=annee)
    return qs.order_by("-id_annee_scolaire__date_debut", "-date_inscription").first()


def frais_disponibles(ecole, eleve, annee=None):
    """
    Les frais au choix de l'école, au tarif du niveau de l'élève, et ceux
    qu'il a déjà cette année — ce que propose « Ajouter des frais ».
    """
    if eleve.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cet élève appartient à une autre école.")
    inscription = _inscription_de_lannee(eleve, annee)
    if inscription is None:
        raise ErreurFinance("Cet élève n'a aucune inscription en cours : inscrivez-le d'abord.")
    annee, classe = inscription.id_annee_scolaire, inscription.id_classe
    deja = set(models.FraisEleve.objects
               .filter(id_eleve=eleve, id_annee_scolaire=annee)
               .values_list("id_type_frais_id", flat=True))
    avec_tarif = set()
    if classe.id_niveau_id:
        avec_tarif = set(models.TarifNiveau.objects
                         .filter(id_niveau_id=classe.id_niveau_id)
                         .values_list("id_type_frais_id", flat=True))
    types = (models.TypeFrais.objects
             .filter(id_ecole=ecole, nature=models.TypeFrais.Nature.AUTRE)
             .order_by("libelle"))
    return {
        "annee": {
            "id": annee.pk,
            "libelle": annee.libelle,
            "date_debut": annee.date_debut.isoformat(),
            "date_fin": annee.date_fin.isoformat(),
        },
        "classe": classe.nom,
        "niveau": classe.libelle_niveau,
        "types": [{
            "id": t.pk,
            "libelle": t.libelle,
            "montant": montant_json(tarif(t, classe)),
            "tarif_niveau": t.pk in avec_tarif,
            "mensuel": est_mensuel(t),
            "deja": t.pk in deja,
        } for t in types],
    }


@transaction.atomic
def ajouter_frais_optionnels(ecole, eleve, types, annee=None, date_debut=None):
    """
    Frais au choix souscrits en cours d'année : l'élève commence le taekwondo
    en janvier, prend le transport à la rentrée des vacances.

    Un frais unique (uniforme) est émis une fois, dû à la date de début. Un
    frais mensuel l'est pour chaque mois restant, de la date de début à la fin
    de l'année scolaire : la famille voit d'emblée à quoi elle s'engage, et
    chaque mois se relance comme une mensualité. Le montant est celui du niveau
    de l'élève, à défaut le montant par défaut du frais.

    Un frais que l'élève a déjà sur l'année n'est pas émis une seconde fois.
    """
    if eleve.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cet élève appartient à une autre école.")
    inscription = _inscription_de_lannee(eleve, annee)
    if inscription is None:
        raise ErreurFinance("Cet élève n'a aucune inscription en cours : inscrivez-le d'abord.")
    annee, classe = inscription.id_annee_scolaire, inscription.id_classe

    date_debut = max(date_debut or timezone.localdate(), annee.date_debut)
    if date_debut > annee.date_fin:
        raise ErreurFinance(f"L'année {annee.libelle} se termine le {annee.date_fin:%d/%m/%Y} : "
                            "choisissez une date antérieure.")

    deja = set(models.FraisEleve.objects
               .filter(id_eleve=eleve, id_annee_scolaire=annee)
               .values_list("id_type_frais_id", flat=True))
    crees, resume, ignores, sans_tarif = [], [], [], []
    for type_frais in types:
        if type_frais.id_ecole_id != ecole.pk:
            raise ErreurFinance("Ce type de frais appartient à une autre école.")
        if type_frais.nature != models.TypeFrais.Nature.AUTRE:
            raise ErreurFinance(f"« {type_frais.libelle} » est dû d'office : il n'a pas à être ajouté.")
        if type_frais.pk in deja:
            ignores.append(type_frais.libelle)
            continue
        montant = tarif(type_frais, classe)
        if not montant or montant <= 0:
            sans_tarif.append(type_frais.libelle)
            continue

        echeances = [date_debut]
        if est_mensuel(type_frais):
            mois = ((annee.date_fin.year - date_debut.year) * 12
                    + annee.date_fin.month - date_debut.month + 1)
            echeances = [e for e in echeances_mensuelles(date_debut, mois) if e <= annee.date_fin]
        for echeance in echeances:
            crees.append(models.FraisEleve.objects.create(
                id_eleve=eleve, id_type_frais=type_frais, id_annee_scolaire=annee,
                montant_du=montant, echeance=echeance,
                statut=models.FraisEleve.Statut.IMPAYE,
            ))
        resume.append({"libelle": type_frais.libelle, "echeances": len(echeances),
                       "montant": montant_json(montant)})

    if not crees:
        motifs = []
        if ignores:
            motifs.append("déjà souscrit(s) cette année : " + ", ".join(ignores))
        if sans_tarif:
            motifs.append("aucun montant pour : " + ", ".join(sans_tarif)
                          + " (renseignez le tarif du niveau ou le montant par défaut)")
        raise ErreurFinance("Aucun frais ajouté — " + " ; ".join(motifs) + "."
                            if motifs else "Aucun frais à ajouter.")

    return {
        "id_annee_scolaire": annee.pk,
        "annee": annee.libelle,
        "crees": len(crees),
        "total": montant_json(sum((f.montant_du for f in crees), ZERO)),
        "frais": resume,
        "deja": ignores,
        "sans_tarif": sans_tarif,
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
            du=_somme(F("id_eleve__frais__montant_du") - F("id_eleve__frais__remise"),
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

    attendu = frais.aggregate(t=_somme(NET))["t"]
    encaisse = paiements.aggregate(t=_somme("montant"))["t"]
    total_depenses = depenses.aggregate(t=_somme("montant"))["t"]

    # Le reste à recouvrer se lit sur les frais, pas sur `attendu - encaisse` :
    # un paiement peut être daté hors période alors que le frais, lui, est dû.
    solde_frais = frais.aggregate(
        du=_somme(NET),
        paye=_somme("paiements__montant"),
    )
    reste = solde_frais["du"] - solde_frais["paye"]

    aujourdhui = timezone.localdate()
    impayes = frais.exclude(statut=models.FraisEleve.Statut.PAYE)

    methodes = dict(models.Paiement.Methode.choices)

    par_type = (paiements
                .values("id_frais_eleve__id_type_frais", "id_frais_eleve__id_type_frais__libelle")
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
        # Combien d'élèves à jour, en retard, partiels : un montant global ne
        # dit pas si l'ardoise pèse sur trois familles ou sur trois cents.
        "eleves": repartition_eleves(frais, aujourdhui, ecole=ecole),
        "derniers_paiements": [
            {
                "id": p.pk,
                "date": p.date_paiement.isoformat(),
                "montant": montant_json(p.montant),
                "eleve": f"{p.id_frais_eleve.id_eleve.prenom} "
                         f"{p.id_frais_eleve.id_eleve.nom}".strip(),
                "id_eleve": p.id_frais_eleve.id_eleve_id,
                "type_frais": p.id_frais_eleve.id_type_frais.libelle,
                "methode": p.get_methode_display(),
                "reference_transaction": p.reference_transaction,
            }
            for p in paiements.select_related(
                "id_frais_eleve__id_eleve", "id_frais_eleve__id_type_frais",
            ).order_by("-date_paiement", "-pk")[:10]
        ],
        "recettes": {
            "total": montant_json(encaisse),
            "par_type_frais": [
                {"code": str(l["id_frais_eleve__id_type_frais"]),
                 "libelle": l["id_frais_eleve__id_type_frais__libelle"],
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


# ---------------------------------------------------------------------------
# Situation financière d'un élève (Dossier → Finances)
# ---------------------------------------------------------------------------

def recalculer_statut(frais):
    """
    Remet le statut d'une ligne de frais en accord avec ce qui a été encaissé.

    Le statut est dénormalisé — c'est lui qu'on filtre dans les listes et les
    relances — donc tout ce qui bouge le solde doit repasser par ici : un
    encaissement, mais aussi une remise accordée après coup, qui peut solder
    un frais sans qu'un centime de plus soit versé.
    """
    if frais.solde_restant <= ZERO:
        statut = models.FraisEleve.Statut.PAYE
    elif frais.montant_paye > ZERO:
        statut = models.FraisEleve.Statut.PARTIEL
    else:
        statut = models.FraisEleve.Statut.IMPAYE
    if frais.statut != statut:
        frais.statut = statut
        frais.save(update_fields=["statut"])
    return frais.statut


def mois_de_lannee(annee):
    """Suite des mois couverts par une année scolaire, du premier au dernier."""
    courant = date(annee.date_debut.year, annee.date_debut.month, 1)
    fin = date(annee.date_fin.year, annee.date_fin.month, 1)
    mois = []
    while courant <= fin:
        mois.append(courant)
        courant = date(courant.year + courant.month // 12, courant.month % 12 + 1, 1)
    return mois


def libelle_mois(jour):
    return f"{MOIS_FR[jour.month - 1].capitalize()} {jour.year}"


def _libelle_frais(frais, types_repetes):
    """
    « Inscription », mais « Mensualité novembre » quand le type revient
    plusieurs fois dans l'année : sans le mois, l'échéancier afficherait dix
    lignes identiques.
    """
    base = frais.id_type_frais.libelle
    if frais.echeance and frais.id_type_frais_id in types_repetes:
        return f"{base} {MOIS_FR[frais.echeance.month - 1]}"
    return base


def annees_de_leleve(ecole, eleve):
    """
    Années scolaires sur lesquelles l'élève a une trace — inscription ou frais.

    Les frais comptent autant que les inscriptions : un élève radié en cours
    d'année garde une ardoise, et elle doit rester consultable.
    """
    ids = set(models.Inscription.objects.filter(id_eleve=eleve)
              .values_list("id_annee_scolaire_id", flat=True))
    ids |= set(models.FraisEleve.objects.filter(id_eleve=eleve)
               .values_list("id_annee_scolaire_id", flat=True))
    classes = {
        i.id_annee_scolaire_id: i
        for i in models.Inscription.objects.filter(id_eleve=eleve)
        .select_related("id_classe", "id_classe__id_niveau")
        .order_by("-date_inscription")
    }
    annees = (models.AnneeScolaire.objects
              .filter(pk__in=ids, id_ecole=ecole)
              .order_by("-date_debut"))
    return [{
        "id": a.pk,
        "libelle": a.libelle,
        "statut": a.statut,
        "classe": classes[a.pk].id_classe.nom if a.pk in classes else None,
        "niveau": (classes[a.pk].id_classe.libelle_niveau
                   if a.pk in classes else None),
    } for a in annees]


def situation_eleve(ecole, eleve, annee=None, aujourdhui=None):
    """
    Tout le financier d'un élève **pour une seule année scolaire** : résumé,
    détail des frais, échéancier mensuel et historique des paiements.

    Une seule réponse plutôt que quatre appels, comme le dossier scolaire : ces
    blocs n'ont de sens qu'ensemble, et l'écran les affiche d'un bloc.

    L'année est le pivot obligatoire. Agréger toutes les années confondues —
    ce que faisait le dossier jusqu'ici — additionne l'ardoise de CE2 et celle
    de CM2 : le chiffre affiché ne correspond alors à aucune facture réelle et
    ne se rapproche d'aucun paiement.
    """
    if eleve.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cet élève appartient à une autre école.")
    aujourdhui = aujourdhui or timezone.localdate()

    annees = annees_de_leleve(ecole, eleve)
    if annee is None:
        active = (models.AnneeScolaire.objects
                  .filter(id_ecole=ecole, statut=models.AnneeScolaire.Statut.ACTIVE)
                  .order_by("-date_debut").first())
        # L'année active d'abord — c'est celle qu'on ouvre neuf fois sur dix ;
        # à défaut, la plus récente où l'élève a laissé une trace.
        if active is not None and any(a["id"] == active.pk for a in annees):
            annee = active
        elif annees:
            annee = models.AnneeScolaire.objects.get(pk=annees[0]["id"])
        else:
            annee = active
    elif annee.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cette année scolaire appartient à une autre école.")

    inscription = None
    if annee is not None:
        inscription = (models.Inscription.objects
                       .filter(id_eleve=eleve, id_annee_scolaire=annee)
                       .exclude(statut=models.Inscription.Statut.ANNULEE)
                       .select_related("id_classe", "id_classe__id_niveau")
                       .first())

    lignes = []
    if annee is not None:
        lignes = list(models.FraisEleve.objects
                      .filter(id_eleve=eleve, id_annee_scolaire=annee)
                      .select_related("id_type_frais")
                      .prefetch_related("paiements")
                      .order_by("echeance", "pk"))

    # Un type qui revient plusieurs fois dans l'année est daté au mois.
    occurrences = {}
    for f in lignes:
        occurrences[f.id_type_frais_id] = occurrences.get(f.id_type_frais_id, 0) + 1
    types_repetes = {t for t, n in occurrences.items() if n > 1}

    frais = []
    initial = remise_totale = du = paye = ZERO
    en_retard = False
    prochaine = None
    for f in lignes:
        montant_paye = f.montant_paye
        reste = f.solde_restant
        retard = f.est_en_retard(aujourdhui)
        en_retard = en_retard or retard
        initial += f.montant_du
        remise_totale += f.remise or ZERO
        du += f.montant_net
        paye += montant_paye
        if reste > ZERO and f.echeance and (prochaine is None or f.echeance < prochaine):
            prochaine = f.echeance
        frais.append({
            "id": f.pk,
            "libelle": _libelle_frais(f, types_repetes),
            "type_frais": f.id_type_frais.libelle,
            "id_type_frais": f.id_type_frais_id,
            "mois": f.echeance.strftime("%Y-%m") if f.echeance else None,
            "echeance": f.echeance.isoformat() if f.echeance else None,
            "montant_initial": montant_json(f.montant_du),
            "remise": montant_json(f.remise),
            "motif_remise": f.motif_remise,
            "montant": montant_json(f.montant_net),
            "paye": montant_json(montant_paye),
            "reste": montant_json(reste),
            "statut": f.statut,
            "statut_libelle": f.get_statut_display(),
            "en_retard": retard,
        })

    # Un inscrit de l'année doit ses droits d'inscription et sa mensualité ;
    # une facture qui n'en porte pas ne peut pas le dire à jour.
    manquants = []
    if inscription is not None and lignes:
        manquants = frais_d_office_manquants(
            types_d_office(ecole), {f.id_type_frais.nature for f in lignes})
    code = statut_financier(du, paye, en_retard, complet=not manquants)
    return {
        "eleve": {
            "id": eleve.pk,
            "matricule": eleve.matricule,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "nom_complet": f"{eleve.prenom} {eleve.nom}".strip(),
            "statut": eleve.statut,
        },
        "annee": None if annee is None else {
            "id": annee.pk,
            "libelle": annee.libelle,
            "statut": annee.statut,
            "date_debut": annee.date_debut.isoformat(),
            "date_fin": annee.date_fin.isoformat(),
        },
        "annees": annees,
        "scolarite": None if inscription is None else {
            "id_inscription": inscription.pk,
            "id_classe": inscription.id_classe_id,
            "classe": inscription.id_classe.nom,
            "niveau": inscription.id_classe.libelle_niveau,
            "date_inscription": inscription.date_inscription.isoformat(),
            "statut": inscription.get_statut_display(),
        },
        "resume": {
            "frais_initial": montant_json(initial),
            "remise": montant_json(remise_totale),
            "total_du": montant_json(du),
            "total_paye": montant_json(paye),
            "reste": montant_json(du - paye),
            "statut": code,
            "statut_libelle": StatutFinancier.LIBELLES[code],
            "taux": round(float(paye / du * 100), 1) if du > ZERO else 0.0,
            "nombre_frais": len(frais),
            "frais_soldes": sum(1 for f in frais if f["reste"] <= 0),
            "frais_en_retard": sum(1 for f in frais if f["en_retard"]),
            "prochaine_echeance": prochaine.isoformat() if prochaine else None,
            "frais_manquants": manquants,
        },
        "confirmation": _confirmation(eleve, annee, inscription, lignes),
        "frais": frais,
        "echeancier": _echeancier(annee, lignes, aujourdhui),
        "paiements": historique_paiements(eleve, annee, types_repetes),
    }


def _confirmation(eleve, annee, inscription, lignes):
    """
    Où en est l'élève sur les deux choses qui font de lui un inscrit : les
    droits d'inscription et le premier mois.

    Le calcul est refait sur `lignes`, déjà chargées, plutôt que par une
    requête de plus — et il sert à l'écran pour dire *pourquoi* un
    inscrit incomplet le reste, au lieu de le laisser deviner.
    """
    if annee is None:
        return None
    droits = [f for f in lignes
              if f.id_type_frais.nature == models.TypeFrais.Nature.INSCRIPTION]
    du = sum((f.montant_net for f in droits), ZERO)
    paye = sum((f.montant_paye for f in droits), ZERO)
    mensualites = sorted(
        (f for f in lignes
         if f.id_type_frais.nature == models.TypeFrais.Nature.MENSUALITE and f.echeance),
        key=lambda f: (f.echeance, f.pk))
    mois = mensualites[0] if mensualites else None
    confirmee = (inscription is not None
                 and inscription.statut in models.Inscription.STATUTS_CONFIRMES)
    return {
        "statut": inscription.statut if inscription else None,
        "statut_libelle": inscription.get_statut_display() if inscription else None,
        "confirmee": confirmee,
        # Aucun frais d'inscription émis : rien à régler, donc rien qui puisse
        # confirmer. C'est un blocage, pas un état d'attente ordinaire.
        "frais_emis": bool(droits),
        "droits_dus": montant_json(du),
        "droits_payes": montant_json(paye),
        "droits_restants": montant_json(du - paye),
        # La seconde condition : le premier mois de l'année, soldé.
        "premier_mois_emis": mois is not None,
        "premier_mois": libelle_mois(mois.echeance) if mois else None,
        "premier_mois_du": montant_json(mois.montant_net if mois else ZERO),
        "premier_mois_paye": montant_json(mois.montant_paye if mois else ZERO),
        "premier_mois_restant": montant_json(mois.solde_restant if mois else ZERO),
    }


def _echeancier(annee, lignes, aujourdhui):
    """
    Un mois de l'année scolaire par ligne, soldé ou non.

    Les mois sont tous listés, même sans frais : la question posée à l'écran
    est « où en est-on en décembre ? », et une ligne absente se lit comme une
    donnée manquante, pas comme « rien à payer ».
    """
    if annee is None:
        return []
    par_mois = {}
    for f in lignes:
        if f.echeance is None:
            continue
        cle = f.echeance.strftime("%Y-%m")
        seau = par_mois.setdefault(cle, {"du": ZERO, "paye": ZERO, "retard": False})
        seau["du"] += f.montant_net
        seau["paye"] += f.montant_paye
        seau["retard"] = seau["retard"] or f.est_en_retard(aujourdhui)

    echeancier = []
    for premier in mois_de_lannee(annee):
        cle = premier.strftime("%Y-%m")
        seau = par_mois.get(cle)
        if seau is None:
            echeancier.append({
                "mois": cle, "libelle": libelle_mois(premier),
                "du": 0.0, "paye": 0.0, "reste": 0.0,
                "statut": StatutFinancier.SANS_FRAIS,
                "statut_libelle": StatutFinancier.LIBELLES[StatutFinancier.SANS_FRAIS],
            })
            continue
        statut = statut_financier(seau["du"], seau["paye"], seau["retard"])
        echeancier.append({
            "mois": cle,
            "libelle": libelle_mois(premier),
            "du": montant_json(seau["du"]),
            "paye": montant_json(seau["paye"]),
            "reste": montant_json(seau["du"] - seau["paye"]),
            "statut": statut,
            "statut_libelle": StatutFinancier.LIBELLES[statut],
        })

    # Les frais sans échéance (inscription, uniforme) n'ont pas de mois : les
    # taire les ferait disparaître du seul écran où on lit l'année entière.
    sans_date = [f for f in lignes if f.echeance is None]
    if sans_date:
        du = sum((f.montant_net for f in sans_date), ZERO)
        paye = sum((f.montant_paye for f in sans_date), ZERO)
        statut = statut_financier(du, paye, False)
        echeancier.append({
            "mois": None, "libelle": "Sans échéance",
            "du": montant_json(du), "paye": montant_json(paye),
            "reste": montant_json(du - paye),
            "statut": statut, "statut_libelle": StatutFinancier.LIBELLES[statut],
        })
    return echeancier


def historique_paiements(eleve, annee=None, types_repetes=None):
    """Tous les encaissements de l'élève sur l'année, du plus récent au plus ancien."""
    qs = (models.Paiement.objects
          .filter(id_frais_eleve__id_eleve=eleve)
          .select_related("id_frais_eleve", "id_frais_eleve__id_type_frais",
                          "id_utilisateur_caissier", "recu")
          .order_by("-date_paiement", "-pk"))
    if annee is not None:
        qs = qs.filter(id_frais_eleve__id_annee_scolaire=annee)
    paiements = list(qs)

    if types_repetes is None:
        compte = {}
        for p in paiements:
            cle = p.id_frais_eleve.id_type_frais_id
            compte[cle] = compte.get(cle, 0) + 1
        types_repetes = {t for t, n in compte.items() if n > 1}

    lignes = []
    for p in paiements:
        frais = p.id_frais_eleve
        recu = getattr(p, "recu", None)
        caissier = p.id_utilisateur_caissier
        lignes.append({
            "id": p.pk,
            "date": p.date_paiement.isoformat(),
            "montant": montant_json(p.montant),
            "id_frais_eleve": frais.pk,
            "frais": _libelle_frais(frais, types_repetes),
            "type_frais": frais.id_type_frais.libelle,
            "mois": frais.echeance.strftime("%Y-%m") if frais.echeance else None,
            "methode": p.methode,
            "methode_libelle": p.get_methode_display(),
            "reference_transaction": p.reference_transaction,
            "commentaire": p.commentaire,
            "id_recu": recu.pk if recu else None,
            "numero_recu": recu.numero_recu if recu else None,
            "enregistre_par": (f"{caissier.prenom} {caissier.nom}".strip()
                               if caissier else None),
            "statut": "confirme",
        })
    return lignes


# ---------------------------------------------------------------------------
# Situation financière de tous les élèves
# ---------------------------------------------------------------------------

def situation_globale(ecole, annee, classe=None, niveau=None, statut=None,
                      recherche=None, aujourdhui=None):
    """
    Une ligne par élève inscrit sur l'année : dû, payé, reste, statut.

    L'écran Finances liste des *frais* : dix lignes pour un élève qui paie au
    mois, ce qui ne dit pas qui est à jour. C'est pourtant la question que
    pose la direction, et elle se répond par élève.

    Trois requêtes agrégées plutôt qu'une jointure unique : joindre frais et
    paiements ensemble multiplie chaque frais par ses paiements et gonfle le
    dû d'autant — le même piège que dans `impayes_par_classe`.
    """
    if annee.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cette année scolaire appartient à une autre école.")
    aujourdhui = aujourdhui or timezone.localdate()

    inscriptions = (models.Inscription.objects
                    .filter(id_annee_scolaire=annee, id_eleve__id_ecole=ecole)
                    .exclude(statut=models.Inscription.Statut.ANNULEE)
                    .select_related("id_eleve", "id_classe", "id_classe__id_niveau"))
    if classe is not None:
        inscriptions = inscriptions.filter(id_classe=classe)
    if niveau is not None:
        inscriptions = inscriptions.filter(id_classe__id_niveau=niveau)
    if recherche:
        terme = str(recherche).strip()
        inscriptions = inscriptions.filter(
            Q(id_eleve__nom__icontains=terme)
            | Q(id_eleve__prenom__icontains=terme)
            | Q(id_eleve__matricule__icontains=terme)
        )
    inscriptions = inscriptions.order_by("id_classe__nom", "id_eleve__nom",
                                         "id_eleve__prenom")

    ids = list(inscriptions.values_list("id_eleve_id", flat=True))
    frais = models.FraisEleve.objects.filter(id_annee_scolaire=annee, id_eleve__in=ids)

    montants = {
        l["id_eleve"]: l for l in
        frais.values("id_eleve").annotate(du=_somme(NET),
                                          initial=_somme("montant_du"),
                                          remise=_somme("remise"))
    }
    paye_par_eleve = {
        l["eleve"]: l["paye"] for l in
        models.Paiement.objects.filter(id_frais_eleve__in=frais)
        .values(eleve=F("id_frais_eleve__id_eleve"))
        .annotate(paye=_somme("montant"))
    }
    en_retard = set(frais.filter(echeance__lt=aujourdhui)
                    .exclude(statut=models.FraisEleve.Statut.PAYE)
                    .values_list("id_eleve_id", flat=True))
    incomplets = eleves_aux_frais_incomplets(ecole, frais)

    lignes = []
    totaux = {"du": ZERO, "paye": ZERO, "reste": ZERO}
    repartition = {v: 0 for v in StatutFinancier.VALEURS}
    for inscription in inscriptions:
        eleve = inscription.id_eleve
        chiffres = montants.get(eleve.pk) or {}
        du = chiffres.get("du") or ZERO
        paye = paye_par_eleve.get(eleve.pk) or ZERO
        code = statut_financier(du, paye, eleve.pk in en_retard,
                                complet=eleve.pk not in incomplets)
        repartition[code] += 1
        totaux["du"] += du
        totaux["paye"] += paye
        totaux["reste"] += du - paye
        if statut and code != statut:
            continue
        lignes.append({
            "id_eleve": eleve.pk,
            "matricule": eleve.matricule,
            "nom_complet": f"{eleve.prenom} {eleve.nom}".strip(),
            "id_classe": inscription.id_classe_id,
            "classe": inscription.id_classe.nom,
            "id_niveau": inscription.id_classe.id_niveau_id,
            "niveau": inscription.id_classe.libelle_niveau,
            "frais_initial": montant_json(chiffres.get("initial")),
            "remise": montant_json(chiffres.get("remise")),
            "du": montant_json(du),
            "paye": montant_json(paye),
            "reste": montant_json(du - paye),
            "statut": code,
            "statut_libelle": StatutFinancier.LIBELLES[code],
            "frais_manquants": incomplets.get(eleve.pk, []),
        })

    return {
        "annee": annee.libelle,
        "id_annee_scolaire": annee.pk,
        "eleves": lignes,
        "totaux": {
            "eleves": len(lignes),
            "du": montant_json(totaux["du"]),
            "paye": montant_json(totaux["paye"]),
            "reste": montant_json(totaux["reste"]),
        },
        # La répartition porte sur toute la promotion, filtre de statut compris :
        # sinon l'onglet « en retard » afficherait « 0 à jour » et laisserait
        # croire que plus personne n'a payé.
        "repartition": repartition,
    }


# ---------------------------------------------------------------------------
# Imputation d'un encaissement
# ---------------------------------------------------------------------------

@transaction.atomic
def affecter_paiement(ecole, eleve, montant, methode, annee=None, type_frais=None,
                      mois=None, reference=None, commentaire=None,
                      date_paiement=None, utilisateur=None, frais=None):
    """
    Impute un montant sur les frais ouverts de l'élève, du plus ancien au plus
    récent, et en tire les mêmes conséquences qu'un encaissement au guichet.

    C'est la brique que réclame le paiement automatique : d'un montant, d'un
    élève et d'une référence, retrouver l'inscription, l'année, le frais
    concerné, écrire le paiement, le reçu et le mouvement de caisse — sans
    qu'un administrateur ait à toucher le dossier. Un règlement encaissé en
    ligne et un règlement reçu à la caisse passent par le même chemin : deux
    chemins finiraient par diverger, et c'est la caisse qui serait fausse.

    Un versement peut couvrir plusieurs échéances : 50 000 F sur deux
    mensualités de 25 000 produisent deux paiements, un par frais, chacun avec
    son reçu. Un frais partiellement soldé est complété avant qu'on entame le
    suivant.
    """
    if eleve.id_ecole_id != ecole.pk:
        raise ErreurFinance("Cet élève appartient à une autre école.")
    try:
        montant = Decimal(str(montant))
    except (InvalidOperation, TypeError):
        raise ErreurFinance("Montant illisible.")
    if montant <= ZERO:
        raise ErreurFinance("Le montant encaissé doit être strictement positif.")
    if methode not in models.Paiement.Methode.values:
        raise ErreurFinance(f"Moyen de paiement inconnu : {methode}.")

    ouverts = (models.FraisEleve.objects
               .filter(id_eleve=eleve)
               .select_related("id_type_frais", "id_annee_scolaire")
               .prefetch_related("paiements")
               .order_by("echeance", "pk"))
    if annee is not None:
        if annee.id_ecole_id != ecole.pk:
            raise ErreurFinance("Cette année scolaire appartient à une autre école.")
        ouverts = ouverts.filter(id_annee_scolaire=annee)
    if type_frais is not None:
        ouverts = ouverts.filter(id_type_frais=type_frais)
    if mois:
        # « 2026-10 » : le mois d'échéance visé par le règlement.
        try:
            an, numero = str(mois).split("-")[:2]
            ouverts = ouverts.filter(echeance__year=int(an), echeance__month=int(numero))
        except (ValueError, TypeError):
            raise ErreurFinance("Mois attendu au format AAAA-MM.")

    if frais:
        # Frais cochés à l'écran : le paiement ne va que sur eux. Un identifiant
        # qui n'est pas un frais de cet élève (autre élève, autre année) est
        # refusé plutôt qu'ignoré : ignoré, l'argent partirait ailleurs.
        voulus = {int(identifiant) for identifiant in frais}
        ouverts = ouverts.filter(pk__in=voulus)
        if set(ouverts.values_list("pk", flat=True)) != voulus:
            raise ErreurFinance("Un des frais cochés n'appartient pas à cet élève pour cette année.")

    ouverts = [f for f in ouverts if f.solde_restant > ZERO]
    if not ouverts:
        raise ErreurFinance(
            f"Aucun frais ouvert pour {eleve.prenom} {eleve.nom} avec ces critères."
        )

    disponible = sum((f.solde_restant for f in ouverts), ZERO)
    if montant > disponible:
        raise ErreurFinance(
            f"Montant supérieur au reste à payer ({montant_json(disponible)} F)."
        )

    paiements = []
    restant = montant
    for frais in ouverts:
        if restant <= ZERO:
            break
        part = min(restant, frais.solde_restant)
        # `id_frais_eleve_id` et non l'objet : celui qu'on a en main porte le
        # cache de `prefetch_related("paiements")`, vide au moment du chargement.
        # Le passer tel quel ferait recalculer le statut sur un solde périmé et
        # laisserait la ligne « impayée » alors qu'elle vient d'être soldée.
        paiement = models.Paiement.objects.create(
            id_frais_eleve_id=frais.pk,
            montant=part,
            methode=methode,
            reference_transaction=reference or None,
            commentaire=commentaire or None,
            id_utilisateur_caissier=(utilisateur if (utilisateur and
                                     utilisateur.is_authenticated) else None),
            **({"date_paiement": date_paiement} if date_paiement else {}),
        )
        enregistrer_paiement(paiement, utilisateur=utilisateur)
        paiements.append(paiement)
        restant -= part

    return paiements


# ---------------------------------------------------------------------------
# Répartition des élèves par statut (tableau de bord)
# ---------------------------------------------------------------------------

def repartition_eleves(frais, aujourdhui=None, ecole=None):
    """
    Combien d'élèves à jour, partiels, en retard, sans aucun règlement.

    Avec `ecole`, un élève à qui manque un frais dû d'office n'est pas compté
    à jour, comme dans son dossier.
    """
    aujourdhui = aujourdhui or timezone.localdate()
    incomplets = eleves_aux_frais_incomplets(ecole, frais) if ecole is not None else {}
    du_par_eleve = {l["id_eleve"]: l["du"] for l in
                    frais.values("id_eleve").annotate(du=_somme(NET))}
    paye_par_eleve = {
        l["eleve"]: l["paye"] for l in
        models.Paiement.objects.filter(id_frais_eleve__in=frais)
        .values(eleve=F("id_frais_eleve__id_eleve"))
        .annotate(paye=_somme("montant"))
    }
    en_retard = set(frais.filter(echeance__lt=aujourdhui)
                    .exclude(statut=models.FraisEleve.Statut.PAYE)
                    .values_list("id_eleve_id", flat=True))

    repartition = {v: 0 for v in StatutFinancier.VALEURS}
    for id_eleve, du in du_par_eleve.items():
        code = statut_financier(du, paye_par_eleve.get(id_eleve, ZERO),
                                id_eleve in en_retard, complet=id_eleve not in incomplets)
        repartition[code] += 1
    repartition["total"] = len(du_par_eleve)
    return repartition


# ---------------------------------------------------------------------------
# Historique de caisse
# ---------------------------------------------------------------------------

def historique_caisse(ecole, jour, caissier=None):
    """
    Ce qui est entré et sorti de la caisse un jour donné, et qui l'a saisi.

    Les paiements portent le caissier qui les a encaissés, les dépenses la
    personne qui les a enregistrées. Les totaux par caissier servent au
    rapprochement du soir : ce que chacun a en tiroir contre ce que dit le
    logiciel. « Espèces attendues » ne compte que les espèces, moins les
    dépenses — le mobile money et la carte ne passent pas par le tiroir.

    `caissier` restreint le tout aux opérations d'une seule personne.
    """
    paiements = (models.Paiement.objects
                 .filter(id_frais_eleve__id_eleve__id_ecole=ecole, date_paiement__date=jour)
                 .select_related("id_frais_eleve__id_eleve", "id_frais_eleve__id_type_frais",
                                 "id_utilisateur_caissier", "recu")
                 .order_by("date_paiement", "pk"))
    depenses = (models.Depense.objects
                .filter(id_ecole=ecole, date_depense=jour)
                .select_related("id_type_depense", "id_utilisateur")
                .order_by("pk"))
    if caissier is not None:
        paiements = paiements.filter(id_utilisateur_caissier=caissier)
        depenses = depenses.filter(id_utilisateur=caissier)

    def nom(utilisateur):
        return f"{utilisateur.prenom} {utilisateur.nom}".strip() if utilisateur else "Non renseigné"

    par_caissier = {}

    def compte(utilisateur):
        cle = utilisateur.pk if utilisateur else None
        return par_caissier.setdefault(cle, {
            "id": cle, "nom": nom(utilisateur), "encaisse": ZERO, "depense": ZERO,
            "nombre_paiements": 0, "nombre_depenses": 0,
        })

    methodes = dict(models.Paiement.Methode.choices)
    par_methode = {}
    encaisse = especes = depense_totale = ZERO
    lignes_paiements = []
    for p in paiements:
        frais = p.id_frais_eleve
        eleve = frais.id_eleve
        recu = getattr(p, "recu", None)
        encaisse += p.montant
        if p.methode == models.Paiement.Methode.ESPECES:
            especes += p.montant
        methode = par_methode.setdefault(p.methode, {
            "methode": p.methode, "libelle": methodes.get(p.methode, p.methode),
            "montant": ZERO, "nombre": 0})
        methode["montant"] += p.montant
        methode["nombre"] += 1
        ligne_caissier = compte(p.id_utilisateur_caissier)
        ligne_caissier["encaisse"] += p.montant
        ligne_caissier["nombre_paiements"] += 1
        mensuel = {frais.id_type_frais_id} if est_mensuel(frais.id_type_frais) else set()
        lignes_paiements.append({
            "id": p.pk,
            "heure": timezone.localtime(p.date_paiement).strftime("%H:%M"),
            "id_eleve": eleve.pk,
            "eleve": f"{eleve.prenom} {eleve.nom}".strip(),
            "matricule": eleve.matricule,
            "frais": _libelle_frais(frais, mensuel),
            "montant": montant_json(p.montant),
            "methode": p.methode,
            "methode_libelle": methodes.get(p.methode, p.methode),
            "reference": p.reference_transaction,
            "numero_recu": recu.numero_recu if recu else None,
            "id_recu": recu.pk if recu else None,
            "id_caissier": p.id_utilisateur_caissier_id,
            "caissier": nom(p.id_utilisateur_caissier),
        })

    lignes_depenses = []
    for d in depenses:
        depense_totale += d.montant
        ligne_caissier = compte(d.id_utilisateur)
        ligne_caissier["depense"] += d.montant
        ligne_caissier["nombre_depenses"] += 1
        lignes_depenses.append({
            "id": d.pk,
            "categorie": d.id_type_depense.libelle,
            "motif": d.motif,
            "montant": montant_json(d.montant),
            "id_caissier": d.id_utilisateur_id,
            "caissier": nom(d.id_utilisateur),
        })

    caissiers = (get_user_model().objects
                 .filter(id_ecole=ecole, role__in=("admin", "caissier"))
                 .order_by("nom", "prenom"))

    # L'arrêté du jour, s'il existe. Les écarts n'ont de sens que sur la caisse
    # entière : filtrée sur un caissier, la comparaison opposerait sa part au
    # total de tout le monde.
    arrete = (models.ClotureCaisse.objects
              .filter(id_ecole=ecole, date_journee=jour)
              .select_related("id_utilisateur").first())
    cloture = None
    if arrete is not None:
        cloture = {
            "id": arrete.pk,
            "date_cloture": timezone.localtime(arrete.date_cloture).strftime("%d/%m/%Y à %H:%M"),
            "par": nom(arrete.id_utilisateur),
            "total_encaisse": montant_json(arrete.total_encaisse),
            "total_depense": montant_json(arrete.total_depense),
            "especes_attendues": montant_json(arrete.especes_attendues),
            "commentaire": arrete.commentaire,
            "ecart_encaisse": (None if caissier is not None
                               else montant_json(encaisse - arrete.total_encaisse)),
            "ecart_depense": (None if caissier is not None
                              else montant_json(depense_totale - arrete.total_depense)),
        }

    return {
        "date": jour.isoformat(),
        "caissier": None if caissier is None else {"id": caissier.pk, "nom": nom(caissier)},
        "caissiers": [{"id": u.pk, "nom": nom(u), "role": u.role} for u in caissiers],
        "totaux": {
            "encaisse": montant_json(encaisse),
            "depense": montant_json(depense_totale),
            "solde": montant_json(encaisse - depense_totale),
            "especes": montant_json(especes),
            "especes_en_caisse": montant_json(especes - depense_totale),
            "nombre_paiements": len(lignes_paiements),
            "nombre_depenses": len(lignes_depenses),
            "par_methode": [dict(m, montant=montant_json(m["montant"]))
                            for m in sorted(par_methode.values(), key=lambda m: -m["montant"])],
        },
        "par_caissier": [dict(c, encaisse=montant_json(c["encaisse"]), depense=montant_json(c["depense"]))
                         for c in sorted(par_caissier.values(), key=lambda c: c["nom"])],
        "paiements": lignes_paiements,
        "depenses": lignes_depenses,
        "cloture": cloture,
    }


@transaction.atomic
def cloturer_journee(ecole, jour, utilisateur=None, commentaire=None):
    """
    Arrête la caisse d'une journée : les totaux du logiciel sont figés, datés
    et signés par la personne qui compte.

    Une seconde clôture est refusée : réécrire un arrêté effacerait ce qui
    avait été constaté, et c'est justement la trace qu'on garde pour expliquer
    un écart. Les encaissements restent possibles après la clôture — la
    journée affiche alors l'écart, plutôt que de bloquer une famille au guichet.
    """
    if models.ClotureCaisse.objects.filter(id_ecole=ecole, date_journee=jour).exists():
        raise ErreurFinance(f"La caisse du {jour:%d/%m/%Y} est déjà clôturée.")
    totaux = historique_caisse(ecole, jour)["totaux"]
    return models.ClotureCaisse.objects.create(
        id_ecole=ecole,
        date_journee=jour,
        total_encaisse=Decimal(str(totaux["encaisse"])),
        total_depense=Decimal(str(totaux["depense"])),
        especes_attendues=Decimal(str(totaux["especes_en_caisse"])),
        commentaire=(commentaire or "").strip() or None,
        id_utilisateur=(utilisateur if utilisateur is not None and utilisateur.is_authenticated
                        else None),
    )
