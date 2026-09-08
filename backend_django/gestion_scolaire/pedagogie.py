"""
Cœur du module pédagogique (Module 5 du TDR, ÉPIC 2 de la priorisation).

Jusqu'ici les notes se saisissaient une par une par le CRUD générique et rien
n'en était tiré : pas de moyenne, pas de rang, pas de bulletin. C'est
pourtant le « cœur de la proposition de valeur pédagogique » de la
priorisation. Tout le calcul vit ici, les vues restent minces — même
découpage que `finances` et `scolarite`.

Les trois règles de calcul, écrites une seule fois :

* **Ramener sur 20 avant de moyenner.** Une interrogation sur 10 et une
  composition sur 20 ne se moyennent pas telles quelles. Chaque note est
  ramenée sur 20 via le barème de son évaluation ; l'enseignant continue de
  saisir la note portée sur la copie.
* **Deux pondérations distinctes.** À l'intérieur d'une matière, les
  évaluations pèsent par leur `coefficient` (un devoir compte moins qu'une
  composition). Entre matières, ce sont les `CoefficientMatiere` de la classe
  qui pèsent — le coefficient de maths d'une 6ème n'est pas celui d'une
  terminale. Une matière sans coefficient déclaré compte pour 1.
* **Une matière sans note ne compte pas.** Elle est absente du calcul plutôt
  que comptée zéro : un élève non évalué en sport n'a pas à voir sa moyenne
  générale s'effondrer.

Le rang suit la convention des établissements : ex æquo au même rang, et le
rang suivant saute d'autant (1, 2, 2, 4).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Count, Q

from . import models

ZERO = Decimal("0")
SUR = Decimal("20")

# Seuil de réussite retenu pour les statistiques et les mentions.
MOYENNE_PASSAGE = Decimal("10")

# Mentions par seuil décroissant. Le premier seuil atteint gagne.
MENTIONS = [
    (Decimal("16"), "Excellent"),
    (Decimal("14"), "Très bien"),
    (Decimal("12"), "Bien"),
    (Decimal("10"), "Assez bien"),
    (Decimal("8"), "Insuffisant"),
    (ZERO, "Très insuffisant"),
]


class ErreurPedagogie(Exception):
    """Message destiné à être affiché tel quel à l'utilisateur."""


# ---------------------------------------------------------------------------
# Briques de calcul
# ---------------------------------------------------------------------------

def arrondi(valeur, decimales=2):
    """Arrondi commercial, celui qu'attend un bulletin (12,345 → 12,35)."""
    if valeur is None:
        return None
    quantum = Decimal(1).scaleb(-decimales)
    return Decimal(valeur).quantize(quantum, rounding=ROUND_HALF_UP)


def note_sur_20(valeur, bareme):
    """Ramène une note à l'échelle /20 d'après le barème de son épreuve."""
    bareme = Decimal(bareme or SUR)
    if bareme <= ZERO:
        raise ErreurPedagogie("Le barème d'une évaluation doit être strictement positif.")
    return Decimal(valeur) * SUR / bareme


def mention(moyenne):
    if moyenne is None:
        return None
    for seuil, libelle in MENTIONS:
        if Decimal(moyenne) >= seuil:
            return libelle
    return MENTIONS[-1][1]


def _moyenne_ponderee(couples):
    """
    `couples` : itérable de (valeur, poids). Renvoie None si le poids total
    est nul — c'est-à-dire s'il n'y a rien à moyenner.
    """
    total, poids_total = ZERO, ZERO
    for valeur, poids in couples:
        poids = Decimal(poids or 0)
        if poids <= ZERO:
            continue
        total += Decimal(valeur) * poids
        poids_total += poids
    if poids_total <= ZERO:
        return None
    return total / poids_total


def _coefficients_matieres(classe):
    """Coefficient de chaque matière pour cette classe, indexé par id_matiere."""
    return {
        c.id_matiere_id: Decimal(c.coefficient)
        for c in models.CoefficientMatiere.objects.filter(
            id_classe=classe, id_annee_scolaire_id=classe.id_annee_scolaire_id)
    }


def _verifier_perimetre(ecole, classe, periode):
    if classe.id_ecole_id != ecole.pk:
        raise ErreurPedagogie("Cette classe appartient à une autre école.")
    if periode.id_annee_scolaire.id_ecole_id != ecole.pk:
        raise ErreurPedagogie("Cette période appartient à une autre école.")
    if periode.id_annee_scolaire_id != classe.id_annee_scolaire_id:
        raise ErreurPedagogie(
            f"La période {periode.libelle} ne relève pas de l'année scolaire "
            f"de la classe {classe.nom}."
        )


# ---------------------------------------------------------------------------
# Moyennes d'une classe sur une période
# ---------------------------------------------------------------------------

def eleves_de_la_classe(classe):
    """Élèves dont l'inscription dans cette classe n'a pas été annulée."""
    return [
        i.id_eleve for i in
        models.Inscription.objects
        .filter(id_classe=classe)
        .exclude(statut=models.Inscription.Statut.ANNULEE)
        .select_related("id_eleve")
        .order_by("id_eleve__nom", "id_eleve__prenom")
    ]


def moyennes_classe(ecole, classe, periode):
    """
    Bulletin de toute la classe en une passe.

    Calculer élève par élève rejouerait les mêmes requêtes autant de fois
    qu'il y a d'inscrits : ici on lit une fois les évaluations, une fois les
    notes, et tout le reste est de l'arithmétique en mémoire. C'est ce qui
    permet de générer les bulletins d'une classe de 60 en une requête HTTP.

    Renvoie une liste ordonnée par rang, chaque entrée portant le détail par
    matière et la moyenne générale.
    """
    _verifier_perimetre(ecole, classe, periode)

    eleves = eleves_de_la_classe(classe)
    if not eleves:
        raise ErreurPedagogie(f"Aucun élève inscrit en {classe.nom}.")

    evaluations = list(
        models.Evaluation.objects
        .filter(id_classe=classe, id_periode=periode)
        .select_related("id_matiere")
    )
    par_evaluation = {e.pk: e for e in evaluations}

    notes = (models.Note.objects
             .filter(id_evaluation__in=evaluations, id_eleve__in=eleves)
             .values_list("id_eleve_id", "id_evaluation_id", "valeur"))

    # {id_eleve: {id_matiere: [(note_sur_20, coefficient_evaluation), ...]}}
    releve = {eleve.pk: {} for eleve in eleves}
    for id_eleve, id_evaluation, valeur in notes:
        evaluation = par_evaluation[id_evaluation]
        (releve[id_eleve]
         .setdefault(evaluation.id_matiere_id, [])
         .append((note_sur_20(valeur, evaluation.bareme), Decimal(evaluation.coefficient))))

    coefficients = _coefficients_matieres(classe)
    matieres = {e.id_matiere_id: e.id_matiere for e in evaluations}

    lignes = []
    for eleve in eleves:
        detail = []
        for id_matiere, matiere in sorted(matieres.items(), key=lambda kv: kv[1].nom):
            notes_matiere = releve[eleve.pk].get(id_matiere, [])
            moyenne = _moyenne_ponderee(notes_matiere)
            detail.append({
                "id_matiere": id_matiere,
                "matiere": matiere.nom,
                "coefficient": coefficients.get(id_matiere, Decimal("1")),
                "nombre_notes": len(notes_matiere),
                "moyenne": arrondi(moyenne),
                "appreciation": mention(moyenne),
            })

        generale = _moyenne_ponderee(
            (d["moyenne"], d["coefficient"]) for d in detail if d["moyenne"] is not None
        )
        lignes.append({
            "id_eleve": eleve.pk,
            "matricule": eleve.matricule,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "matieres": detail,
            "moyenne_generale": arrondi(generale),
            "appreciation": mention(generale),
            "rang": None,
        })

    return _classer(lignes)


def _classer(lignes):
    """
    Classe par moyenne décroissante, ex æquo au même rang.

    Les élèves sans aucune note gardent un rang nul : ils ne sont pas
    « derniers », ils ne sont pas classés — les compter derniers ferait
    remonter artificiellement le rang de tous les autres.
    """
    classables = [l for l in lignes if l["moyenne_generale"] is not None]
    classables.sort(key=lambda l: l["moyenne_generale"], reverse=True)

    precedente, rang_courant = None, 0
    for position, ligne in enumerate(classables, start=1):
        if ligne["moyenne_generale"] != precedente:
            rang_courant = position
            precedente = ligne["moyenne_generale"]
        ligne["rang"] = rang_courant

    non_classes = [l for l in lignes if l["moyenne_generale"] is None]
    non_classes.sort(key=lambda l: (l["nom"], l["prenom"]))
    return classables + non_classes


# ---------------------------------------------------------------------------
# Bulletins (2.2 de la priorisation)
# ---------------------------------------------------------------------------

@transaction.atomic
def generer_bulletins(ecole, classe, periode, appliquer=True):
    """
    Écrit un bulletin par élève de la classe pour la période.

    Même contrat d'aperçu que la génération des frais : `appliquer=False`
    renvoie les moyennes calculées sans rien écrire, pour que l'écran montre
    la classe avant validation.

    Régénérer met à jour les bulletins existants au lieu d'échouer sur la
    contrainte d'unicité : une note corrigée après le conseil de classe doit
    pouvoir se répercuter.

    Les élèves sans aucune note sont ignorés — un bulletin sans moyenne n'a
    rien à dire, et l'écrire ferait croire à un zéro.
    """
    lignes = moyennes_classe(ecole, classe, periode)
    retenues = [l for l in lignes if l["moyenne_generale"] is not None]

    if appliquer and retenues:
        existants = {
            b.id_eleve_id: b for b in
            models.Bulletin.objects.filter(
                id_classe=classe, id_periode=periode,
                id_eleve__in=[l["id_eleve"] for l in retenues])
        }
        a_creer, a_modifier = [], []
        for ligne in retenues:
            bulletin = existants.get(ligne["id_eleve"])
            if bulletin is None:
                a_creer.append(models.Bulletin(
                    id_eleve_id=ligne["id_eleve"], id_classe=classe, id_periode=periode,
                    moyenne_generale=ligne["moyenne_generale"],
                    rang_classe=ligne["rang"],
                    appreciation_generale=ligne["appreciation"],
                ))
            else:
                bulletin.moyenne_generale = ligne["moyenne_generale"]
                bulletin.rang_classe = ligne["rang"]
                bulletin.appreciation_generale = ligne["appreciation"]
                a_modifier.append(bulletin)

        models.Bulletin.objects.bulk_create(a_creer, batch_size=200)
        models.Bulletin.objects.bulk_update(
            a_modifier, ["moyenne_generale", "rang_classe", "appreciation_generale"],
            batch_size=200)
        crees, mis_a_jour = len(a_creer), len(a_modifier)
    else:
        crees = mis_a_jour = 0

    return {
        "classe": classe.nom,
        "periode": periode.libelle,
        "eleves": len(lignes),
        "bulletins": len(retenues),
        "sans_note": len(lignes) - len(retenues),
        "crees": crees,
        "mis_a_jour": mis_a_jour,
        "moyenne_classe": arrondi(_moyenne_ponderee(
            (l["moyenne_generale"], 1) for l in retenues)),
        "lignes": lignes,
        "applique": bool(appliquer),
    }


def releve_de_notes(ecole, eleve, periode):
    """
    Détail des notes d'un élève sur une période : chaque évaluation, sa note
    brute, son barème, et la moyenne de chaque matière.

    Le bulletin donne les moyennes ; le relevé donne ce qui les compose,
    c'est-à-dire ce qu'une famille demande quand elle conteste une moyenne.
    """
    if eleve.id_ecole_id != ecole.pk:
        raise ErreurPedagogie("Cet élève appartient à une autre école.")

    inscription = (models.Inscription.objects
                   .filter(id_eleve=eleve,
                           id_annee_scolaire_id=periode.id_annee_scolaire_id)
                   .exclude(statut=models.Inscription.Statut.ANNULEE)
                   .select_related("id_classe").first())
    if inscription is None:
        raise ErreurPedagogie(
            f"{eleve.prenom} {eleve.nom} n'est pas inscrit sur l'année de cette période."
        )
    classe = inscription.id_classe

    notes = (models.Note.objects
             .filter(id_eleve=eleve, id_evaluation__id_periode=periode,
                     id_evaluation__id_classe=classe)
             .select_related("id_evaluation", "id_evaluation__id_matiere")
             .order_by("id_evaluation__id_matiere__nom", "id_evaluation__date_evaluation"))

    coefficients = _coefficients_matieres(classe)
    par_matiere = {}
    for note in notes:
        evaluation = note.id_evaluation
        entree = par_matiere.setdefault(evaluation.id_matiere_id, {
            "id_matiere": evaluation.id_matiere_id,
            "matiere": evaluation.id_matiere.nom,
            "coefficient": coefficients.get(evaluation.id_matiere_id, Decimal("1")),
            "notes": [],
        })
        entree["notes"].append({
            "date": evaluation.date_evaluation,
            "type": evaluation.get_type_display(),
            "valeur": note.valeur,
            "bareme": evaluation.bareme,
            "sur_20": arrondi(note_sur_20(note.valeur, evaluation.bareme)),
            "coefficient": evaluation.coefficient,
            "appreciation": note.appreciation,
        })

    matieres = []
    for entree in sorted(par_matiere.values(), key=lambda e: e["matiere"]):
        moyenne = _moyenne_ponderee(
            (n["sur_20"], n["coefficient"]) for n in entree["notes"])
        entree["moyenne"] = arrondi(moyenne)
        entree["appreciation"] = mention(moyenne)
        matieres.append(entree)

    generale = _moyenne_ponderee((m["moyenne"], m["coefficient"]) for m in matieres
                                 if m["moyenne"] is not None)
    bulletin = models.Bulletin.objects.filter(
        id_eleve=eleve, id_classe=classe, id_periode=periode).first()

    return {
        "eleve": {
            "id": eleve.pk,
            "matricule": eleve.matricule,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
        },
        "classe": classe.nom,
        "id_classe": classe.pk,
        "periode": periode.libelle,
        "matieres": matieres,
        "moyenne_generale": arrondi(generale),
        "appreciation": mention(generale),
        "rang_classe": bulletin.rang_classe if bulletin else None,
        "effectif": len(eleves_de_la_classe(classe)),
    }


# ---------------------------------------------------------------------------
# Palmarès et statistiques (2.3 de la priorisation)
# ---------------------------------------------------------------------------

def palmares(ecole, classe, periode, limite=None):
    """Classement de la classe, éventuellement tronqué aux `limite` premiers."""
    lignes = [l for l in moyennes_classe(ecole, classe, periode)
              if l["moyenne_generale"] is not None]
    if limite:
        lignes = lignes[:limite]
    return {
        "classe": classe.nom,
        "periode": periode.libelle,
        "lignes": [{
            "rang": l["rang"],
            "matricule": l["matricule"],
            "nom": l["nom"],
            "prenom": l["prenom"],
            "moyenne_generale": l["moyenne_generale"],
            "appreciation": l["appreciation"],
        } for l in lignes],
    }


def statistiques(ecole, classe, periode):
    """
    Indicateurs d'une classe sur une période : moyenne de classe, taux de
    réussite, extrêmes, répartition, et le même détail par matière.

    Le taux de réussite se compte sur les élèves classés — un élève sans note
    n'est ni reçu ni recalé, et l'inclure ferait baisser le taux sans raison.
    """
    lignes = moyennes_classe(ecole, classe, periode)
    classes_ = [l for l in lignes if l["moyenne_generale"] is not None]
    moyennes = [l["moyenne_generale"] for l in classes_]

    par_matiere = {}
    for ligne in lignes:
        for detail in ligne["matieres"]:
            if detail["moyenne"] is None:
                continue
            par_matiere.setdefault(detail["matiere"], []).append(detail["moyenne"])

    repartition = {libelle: 0 for _, libelle in MENTIONS}
    for valeur in moyennes:
        repartition[mention(valeur)] += 1

    return {
        "classe": classe.nom,
        "periode": periode.libelle,
        "inscrits": len(lignes),
        "sans_note": len(lignes) - len(classes_),
        "ensemble": _bloc_statistique(moyennes),
        "repartition": repartition,
        "matieres": [
            dict(matiere=nom, **_bloc_statistique(valeurs))
            for nom, valeurs in sorted(par_matiere.items())
        ],
    }


# ---------------------------------------------------------------------------
# Synthèse école (tableau de bord de la direction, Module 12 du TDR)
# ---------------------------------------------------------------------------

def periode_courante(ecole, annee=None, aujourdhui=None):
    """
    Période à présenter par défaut : celle qui contient la date du jour.

    Hors période — pendant les vacances, ou l'été — on retient la dernière
    commencée plutôt que rien : un directeur qui ouvre son tableau de bord en
    août veut voir le troisième trimestre, pas un écran vide.
    """
    aujourdhui = aujourdhui or date.today()
    periodes = models.PeriodeScolaire.objects.filter(
        id_annee_scolaire__id_ecole=ecole).select_related("id_annee_scolaire")
    if annee is not None:
        periodes = periodes.filter(id_annee_scolaire=annee)
    else:
        periodes = periodes.filter(
            id_annee_scolaire__statut=models.AnneeScolaire.Statut.ACTIVE)

    en_cours = periodes.filter(date_debut__lte=aujourdhui,
                               date_fin__gte=aujourdhui).order_by("date_debut").first()
    if en_cours is not None:
        return en_cours
    commencees = periodes.filter(date_debut__lte=aujourdhui).order_by("-date_debut").first()
    return commencees or periodes.order_by("date_debut").first()


def _bloc_statistique(valeurs):
    """Indicateurs d'une série de moyennes. Vide plutôt que zéro s'il n'y a rien."""
    if not valeurs:
        return {"effectif": 0, "moyenne": None, "minimum": None, "maximum": None,
                "reussite": 0, "taux_reussite": None}
    reussite = sum(1 for v in valeurs if v >= MOYENNE_PASSAGE)
    return {
        "effectif": len(valeurs),
        "moyenne": arrondi(sum(valeurs) / len(valeurs)),
        "minimum": min(valeurs),
        "maximum": max(valeurs),
        "reussite": reussite,
        "taux_reussite": arrondi(Decimal(reussite) * 100 / len(valeurs), 1),
    }


def synthese(ecole, periode):
    """
    Résultats de toute l'école sur une période.

    `statistiques()` ne répond que pour une classe ; la direction a besoin de
    la vue d'ensemble — taux de réussite global, classement des classes,
    matières en difficulté. Le calcul réutilise `moyennes_classe()` classe par
    classe, pour qu'il n'existe qu'une seule définition de la moyenne : la
    dupliquer en SQL ferait diverger le tableau de bord et les bulletins au
    premier changement de règle.

    Les classes sans aucune note sont listées à part plutôt que comptées à
    zéro : « pas encore saisi » et « mauvais résultats » ne se confondent pas.
    """
    if periode.id_annee_scolaire.id_ecole_id != ecole.pk:
        raise ErreurPedagogie("Cette période appartient à une autre école.")

    classes = (models.Classe.objects
               .filter(id_ecole=ecole, id_annee_scolaire=periode.id_annee_scolaire_id)
               .order_by("nom"))

    moyennes_ecole = []
    par_classe, sans_notes = [], []
    par_matiere = {}

    for classe in classes:
        try:
            lignes = moyennes_classe(ecole, classe, periode)
        except ErreurPedagogie:
            # Classe sans aucun inscrit : elle n'a pas sa place au classement.
            continue

        notes_classe = [l["moyenne_generale"] for l in lignes
                        if l["moyenne_generale"] is not None]
        if not notes_classe:
            sans_notes.append({"classe": classe.nom, "id_classe": classe.pk,
                               "inscrits": len(lignes)})
            continue

        moyennes_ecole.extend(notes_classe)
        bloc = _bloc_statistique(notes_classe)
        par_classe.append({
            "id_classe": classe.pk,
            "classe": classe.nom,
            "inscrits": len(lignes),
            "sans_note": len(lignes) - len(notes_classe),
            **bloc,
        })

        for ligne in lignes:
            for detail in ligne["matieres"]:
                if detail["moyenne"] is not None:
                    par_matiere.setdefault(detail["matiere"], []).append(detail["moyenne"])

    # Classement décroissant : la première ligne est la classe qui réussit le
    # mieux, la dernière celle où il faut aller voir.
    par_classe.sort(key=lambda c: c["moyenne"], reverse=True)

    repartition = {libelle: 0 for _, libelle in MENTIONS}
    for valeur in moyennes_ecole:
        repartition[mention(valeur)] += 1

    matieres = sorted(
        (dict(matiere=nom, **_bloc_statistique(valeurs))
         for nom, valeurs in par_matiere.items()),
        key=lambda m: m["moyenne"],
    )

    return {
        "periode": periode.libelle,
        "id_periode": periode.pk,
        "annee": periode.id_annee_scolaire.libelle,
        "id_annee_scolaire": periode.id_annee_scolaire_id,
        "ensemble": _bloc_statistique(moyennes_ecole),
        "repartition": repartition,
        "classes": par_classe,
        "classes_sans_notes": sans_notes,
        "matieres": matieres,
        "evolution": evolution_resultats(ecole, periode.id_annee_scolaire),
    }


def evolution_resultats(ecole, annee):
    """
    Moyenne et taux de réussite de l'école, période par période.

    Lu sur les bulletins déjà générés plutôt que recalculé : recalculer chaque
    classe pour chaque période multiplierait les requêtes par le nombre de
    trimestres, pour un graphique de trois points. Une période dont les
    bulletins n'ont pas été générés n'apparaît simplement pas.
    """
    lignes = []
    periodes = (models.PeriodeScolaire.objects
                .filter(id_annee_scolaire=annee).order_by("date_debut"))

    for periode in periodes:
        moyennes = list(
            models.Bulletin.objects
            .filter(id_periode=periode, id_eleve__id_ecole=ecole,
                    moyenne_generale__isnull=False)
            .values_list("moyenne_generale", flat=True)
        )
        if not moyennes:
            continue
        lignes.append({"periode": periode.libelle, **_bloc_statistique(moyennes)})
    return lignes


# ---------------------------------------------------------------------------
# Assiduité (« suivi de l'assiduité des élèves » du TDR)
# ---------------------------------------------------------------------------

def assiduite(ecole, annee, jours=30, aujourdhui=None):
    """
    Absences et retards sur une fenêtre glissante, par classe.

    Une absence de novembre ne dit rien d'utile en juin : l'assiduité se
    surveille sur les dernières semaines, pas sur le cumul de l'année. La
    fenêtre par défaut couvre le mois écoulé.

    Les non-justifiées sont comptées à part parce que ce sont les seules qui
    appellent une action — un mot aux parents, une convocation.
    """
    if annee.id_ecole_id != ecole.pk:
        raise ErreurPedagogie("Cette année scolaire appartient à une autre école.")
    jours = max(1, min(int(jours), 365))

    aujourdhui = aujourdhui or date.today()
    depuis = aujourdhui - timedelta(days=jours)

    faits = (models.Absence.objects
             .filter(id_classe__id_annee_scolaire=annee,
                     id_classe__id_ecole=ecole,
                     date_absence__date__gte=depuis,
                     date_absence__date__lte=aujourdhui))

    totaux = faits.aggregate(
        absences=Count("pk", filter=Q(statut=models.Absence.Statut.ABSENT)),
        retards=Count("pk", filter=Q(statut=models.Absence.Statut.RETARD)),
        non_justifiees=Count("pk", filter=Q(justifiee=False)),
        eleves=Count("id_eleve", distinct=True),
    )

    par_classe = [
        {
            "id_classe": ligne["id_classe"],
            "classe": ligne["id_classe__nom"],
            "absences": ligne["absences"],
            "retards": ligne["retards"],
            "non_justifiees": ligne["non_justifiees"],
            "eleves": ligne["eleves"],
        }
        for ligne in faits.values("id_classe", "id_classe__nom").annotate(
            absences=Count("pk", filter=Q(statut=models.Absence.Statut.ABSENT)),
            retards=Count("pk", filter=Q(statut=models.Absence.Statut.RETARD)),
            non_justifiees=Count("pk", filter=Q(justifiee=False)),
            eleves=Count("id_eleve", distinct=True),
        )
    ]
    # Les classes les plus touchées en tête : c'est là qu'on intervient.
    par_classe.sort(key=lambda c: c["absences"] + c["retards"], reverse=True)

    # Élèves les plus absents, pour nommer les situations individuelles.
    eleves = [
        {
            "id_eleve": ligne["id_eleve"],
            "nom": f"{ligne['id_eleve__prenom']} {ligne['id_eleve__nom']}",
            "matricule": ligne["id_eleve__matricule"],
            "classe": ligne["id_classe__nom"],
            "absences": ligne["absences"],
            "retards": ligne["retards"],
            "non_justifiees": ligne["non_justifiees"],
        }
        for ligne in faits.values(
            "id_eleve", "id_eleve__nom", "id_eleve__prenom", "id_eleve__matricule",
            "id_classe__nom",
        ).annotate(
            absences=Count("pk", filter=Q(statut=models.Absence.Statut.ABSENT)),
            retards=Count("pk", filter=Q(statut=models.Absence.Statut.RETARD)),
            non_justifiees=Count("pk", filter=Q(justifiee=False)),
        ).order_by("-absences", "-retards")[:10]
    ]

    return {
        "annee": annee.libelle,
        "id_annee_scolaire": annee.pk,
        "jours": jours,
        "depuis": depuis,
        "total": totaux,
        "classes": par_classe,
        "eleves": eleves,
    }


# ---------------------------------------------------------------------------
# Saisie en masse (2.1 de la priorisation)
# ---------------------------------------------------------------------------

@transaction.atomic
def saisir_notes(ecole, evaluation, saisies, utilisateur):
    """
    Enregistre d'un coup les notes d'une évaluation.

    C'est la brique qui manquait pour que la saisie soit tenable : le CRUD
    obligeait à une requête par élève, soit soixante allers-retours pour un
    devoir de 6ème. La grille de l'écran envoie tout en une fois.

    `saisies` : liste de dicts {id_eleve, valeur, appreciation}. Une valeur
    absente ou nulle efface la note (l'élève était absent, ou l'enseignant
    corrige une saisie) — sans quoi une note fausse serait indélébile.

    L'opération est transactionnelle : une seule note invalide et rien n'est
    écrit, plutôt qu'une grille à moitié saisie.
    """
    if evaluation.id_classe.id_ecole_id != ecole.pk:
        raise ErreurPedagogie("Cette évaluation appartient à une autre école.")

    bareme = Decimal(evaluation.bareme)
    inscrits = {e.pk: e for e in eleves_de_la_classe(evaluation.id_classe)}

    a_ecrire, a_effacer = [], []
    for saisie in saisies:
        id_eleve = saisie.get("id_eleve")
        if id_eleve not in inscrits:
            raise ErreurPedagogie(
                f"L'élève {id_eleve} n'est pas inscrit en {evaluation.id_classe.nom}."
            )
        valeur = saisie.get("valeur")
        if valeur in (None, ""):
            a_effacer.append(id_eleve)
            continue
        valeur = Decimal(valeur)
        if valeur < ZERO or valeur > bareme:
            eleve = inscrits[id_eleve]
            raise ErreurPedagogie(
                f"{eleve.prenom} {eleve.nom} : note {valeur} hors barème (0 à {bareme})."
            )
        a_ecrire.append((id_eleve, valeur, (saisie.get("appreciation") or "").strip() or None))

    existantes = {
        n.id_eleve_id: n for n in
        models.Note.objects.filter(id_evaluation=evaluation)
    }

    creees, modifiees = [], []
    for id_eleve, valeur, appreciation in a_ecrire:
        note = existantes.get(id_eleve)
        if note is None:
            creees.append(models.Note(
                id_eleve_id=id_eleve, id_evaluation=evaluation,
                id_utilisateur=utilisateur, valeur=valeur, appreciation=appreciation,
            ))
        else:
            note.valeur = valeur
            note.appreciation = appreciation
            note.id_utilisateur = utilisateur
            modifiees.append(note)

    models.Note.objects.bulk_create(creees, batch_size=200)
    models.Note.objects.bulk_update(modifiees, ["valeur", "appreciation", "id_utilisateur"],
                                    batch_size=200)
    supprimees = 0
    if a_effacer:
        supprimees, _ = (models.Note.objects
                         .filter(id_evaluation=evaluation, id_eleve_id__in=a_effacer)
                         .delete())

    return {
        "evaluation": evaluation.pk,
        "classe": evaluation.id_classe.nom,
        "matiere": evaluation.id_matiere.nom,
        "bareme": bareme,
        "creees": len(creees),
        "modifiees": len(modifiees),
        "effacees": supprimees,
    }


def grille_de_saisie(ecole, evaluation):
    """
    Liste des inscrits de la classe avec leur note actuelle, pour préremplir
    la grille de saisie — y compris les élèves non encore notés.
    """
    if evaluation.id_classe.id_ecole_id != ecole.pk:
        raise ErreurPedagogie("Cette évaluation appartient à une autre école.")

    notes = {n.id_eleve_id: n for n in models.Note.objects.filter(id_evaluation=evaluation)}
    return {
        "id_evaluation": evaluation.pk,
        "classe": evaluation.id_classe.nom,
        "id_classe": evaluation.id_classe_id,
        "matiere": evaluation.id_matiere.nom,
        "periode": evaluation.id_periode.libelle,
        "type": evaluation.get_type_display(),
        "date_evaluation": evaluation.date_evaluation,
        "bareme": evaluation.bareme,
        "coefficient": evaluation.coefficient,
        "eleves": [{
            "id_eleve": eleve.pk,
            "matricule": eleve.matricule,
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "valeur": notes[eleve.pk].valeur if eleve.pk in notes else None,
            "appreciation": notes[eleve.pk].appreciation if eleve.pk in notes else None,
        } for eleve in eleves_de_la_classe(evaluation.id_classe)],
    }
