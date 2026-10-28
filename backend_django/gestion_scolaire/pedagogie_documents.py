"""
Documents du module pédagogique : bulletin, relevé de notes, palmarès.

Même parti pris que `finances_documents` : aucune dépendance de rendu, le PDF
est écrit par `exports.DocumentPDF`. Un bulletin est un tableau et quelques
encadrés — installer un moteur de rendu sur chaque poste d'école pour
imprimer ça ne se justifie pas.

Chaque fonction renvoie le triplet `(contenu, nom_fichier, type_mime)`
attendu par `_fichier()` dans les vues.
"""
from __future__ import annotations

from . import exports

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"


def _sous_titre(ecole, complement=None):
    morceaux = [ecole.nom]
    if ecole.telephone:
        morceaux.append(ecole.telephone)
    if complement:
        morceaux.append(complement)
    return " · ".join(morceaux)


def _texte(valeur, defaut="—"):
    return defaut if valeur in (None, "") else str(valeur)


def _moyenne(valeur):
    """12.50 → « 12,50 ». La virgule est la séparatrice décimale attendue."""
    if valeur is None:
        return "—"
    return f"{float(valeur):.2f}".replace(".", ",")


# ---------------------------------------------------------------------------
# Bulletin d'un élève
# ---------------------------------------------------------------------------

def bulletin(ecole, releve, conseil=None):
    """
    Bulletin individuel, à partir de `pedagogie.releve_de_notes()`.

    `conseil` : décisions du conseil de classe, imprimées en bas quand elles
    existent.
    """
    eleve = releve["eleve"]
    document = exports.DocumentPDF(
        titre="Bulletin de notes",
        sous_titre=_sous_titre(ecole, f"{releve['periode']} · {releve['classe']}"),
        pied=ecole.nom,
    )

    document.champs([
        ("Élève", f"{eleve['prenom']} {eleve['nom']}"),
        ("Matricule", eleve["matricule"]),
        ("Classe", releve["classe"]),
        ("Période", releve["periode"]),
    ])
    document.espace(6)

    document.section("Résultats par matière")
    document.tableau(
        [("Matière", 150, "g"), ("Coef.", 40, "d"), ("Notes", 40, "d"),
         ("Moyenne /20", 60, "d"), ("Appréciation", 90, "g")],
        [[m["matiere"], _moyenne(m["coefficient"]), str(len(m["notes"])),
          _moyenne(m["moyenne"]), _texte(m["appreciation"], "")]
         for m in releve["matieres"]],
    )

    if not releve["matieres"]:
        document.paragraphe("Aucune note saisie sur cette période.")

    document.espace(4)
    document.encadre("Moyenne générale", f"{_moyenne(releve['moyenne_generale'])} / 20")

    rang = releve.get("rang_classe")
    document.champs([
        ("Rang", "—" if rang is None else f"{rang} sur {releve['effectif']}"),
        ("Appréciation", _texte(releve["appreciation"])),
    ])

    if conseil:
        document.section("Décision du conseil de classe")
        document.paragraphe(conseil)

    document.section("Signatures")
    document.paragraphe("Le professeur principal                     "
                        "La Direction                     Les parents", gris=0.45)

    # Le bulletin se classe par élève : son nom d'abord, puis la période.
    nom = exports.nom_document("bulletin", eleve["prenom"], eleve["nom"],
                               releve["classe"], releve["periode"], extension="pdf")
    return document.rendu(), nom, PDF


# ---------------------------------------------------------------------------
# Relevé de notes détaillé
# ---------------------------------------------------------------------------

def releve_de_notes(ecole, releve):
    """Toutes les notes qui composent les moyennes, évaluation par évaluation."""
    eleve = releve["eleve"]
    document = exports.DocumentPDF(
        titre="Relevé de notes",
        sous_titre=_sous_titre(ecole, f"{releve['periode']} · {releve['classe']}"),
        pied=ecole.nom,
    )

    document.champs([
        ("Élève", f"{eleve['prenom']} {eleve['nom']}"),
        ("Matricule", eleve["matricule"]),
        ("Classe", releve["classe"]),
    ])

    for matiere in releve["matieres"]:
        document.section(f"{matiere['matiere']} — moyenne {_moyenne(matiere['moyenne'])} / 20")
        document.tableau(
            [("Date", 60, "g"), ("Type", 70, "g"), ("Note", 50, "d"),
             ("Barème", 45, "d"), ("Sur 20", 50, "d"), ("Coef.", 40, "d"),
             ("Appréciation", 100, "g")],
            [[n["date"].strftime("%d/%m/%Y"), n["type"], _moyenne(n["valeur"]),
              _moyenne(n["bareme"]), _moyenne(n["sur_20"]), _moyenne(n["coefficient"]),
              _texte(n["appreciation"], "")]
             for n in matiere["notes"]],
        )

    if not releve["matieres"]:
        document.paragraphe("Aucune note saisie sur cette période.")

    document.espace(4)
    document.encadre("Moyenne générale", f"{_moyenne(releve['moyenne_generale'])} / 20")

    nom = exports.nom_document("releve-de-notes", eleve["prenom"], eleve["nom"],
                               releve["classe"], releve["periode"], extension="pdf")
    return document.rendu(), nom, PDF


# ---------------------------------------------------------------------------
# Palmarès de la classe
# ---------------------------------------------------------------------------

_ENTETES_PALMARES = ["Rang", "Matricule", "Nom", "Prénom", "Moyenne /20", "Appréciation"]


def palmares(ecole, donnees, format="xlsx"):
    lignes = [[l["rang"], l["matricule"], l["nom"], l["prenom"],
               float(l["moyenne_generale"]), l["appreciation"]]
              for l in donnees["lignes"]]
    intitule = f"Palmarès {donnees['classe']} — {donnees['periode']}"

    def nom(extension):
        return exports.nom_document("palmares", donnees["classe"], donnees["periode"],
                                    ecole.nom, extension=extension)

    if format == "pdf":
        document = exports.DocumentPDF(
            titre=intitule, sous_titre=_sous_titre(ecole), pied=ecole.nom)
        document.tableau(
            [("Rang", 40, "d"), ("Matricule", 80, "g"), ("Nom", 90, "g"),
             ("Prénom", 90, "g"), ("Moyenne /20", 70, "d"), ("Appréciation", 90, "g")],
            [[str(l["rang"]), l["matricule"], l["nom"], l["prenom"],
              _moyenne(l["moyenne_generale"]), _texte(l["appreciation"], "")]
             for l in donnees["lignes"]],
        )
        if not donnees["lignes"]:
            document.paragraphe("Aucun élève classé sur cette période.")
        return document.rendu(), nom("pdf"), PDF

    contenu = exports.classeur_xlsx([(donnees["classe"][:31], _ENTETES_PALMARES, lignes)])
    return contenu, nom("xlsx"), XLSX


# ---------------------------------------------------------------------------
# Notes d'une classe, toutes matières (le « tableau de moyennes » du conseil)
# ---------------------------------------------------------------------------

def moyennes_classe(ecole, resultat, format="xlsx"):
    """
    Tableau récapitulatif d'une classe, à partir de
    `pedagogie.generer_bulletins()` ou `moyennes_classe()`.

    Une colonne par matière, une ligne par élève : c'est le document qu'on
    pose sur la table du conseil de classe.
    """
    lignes_source = resultat["lignes"]
    matieres = [m["matiere"] for m in (lignes_source[0]["matieres"] if lignes_source else [])]
    entetes = ["Rang", "Matricule", "Nom", "Prénom"] + matieres + ["Moyenne générale",
                                                                   "Appréciation"]

    lignes = []
    for ligne in lignes_source:
        lignes.append(
            [ligne["rang"] or "", ligne["matricule"], ligne["nom"], ligne["prenom"]]
            + [float(m["moyenne"]) if m["moyenne"] is not None else ""
               for m in ligne["matieres"]]
            + [float(ligne["moyenne_generale"]) if ligne["moyenne_generale"] is not None else "",
               ligne["appreciation"] or ""]
        )

    intitule = f"Moyennes {resultat['classe']} — {resultat['periode']}"

    def nom(extension):
        return exports.nom_document("moyennes", resultat["classe"], resultat["periode"],
                                    ecole.nom, extension=extension)

    if format == "pdf":
        document = exports.DocumentPDF(
            titre=intitule, sous_titre=_sous_titre(ecole), pied=ecole.nom, paysage=True)
        colonnes = ([("Rang", 30, "d"), ("Nom", 80, "g"), ("Prénom", 70, "g")]
                    + [(m[:12], 45, "d") for m in matieres]
                    + [("Moy.", 45, "d")])
        document.tableau(
            colonnes,
            [[str(l["rang"] or ""), l["nom"], l["prenom"]]
             + [_moyenne(m["moyenne"]) for m in l["matieres"]]
             + [_moyenne(l["moyenne_generale"])]
             for l in lignes_source],
        )
        if not lignes_source:
            document.paragraphe("Aucun élève inscrit dans cette classe.")
        return document.rendu(), nom("pdf"), PDF

    contenu = exports.classeur_xlsx([(resultat["classe"][:31], entetes, lignes)])
    return contenu, nom("xlsx"), XLSX


# ---------------------------------------------------------------------------
# Emploi du temps
# ---------------------------------------------------------------------------

def emploi_du_temps(ecole, seances, periode=None):
    """
    Emploi du temps imprimable, une section par classe, du lundi au samedi.

    `seances` arrive déjà trié (classe, jour, heure) par la vue : le document
    reprend l'ordre de l'écran. `periode` (« Novembre 2026 ») précise la
    fenêtre affichée quand ce n'est pas l'année entière.
    """
    document = exports.DocumentPDF(
        titre="Emploi du temps",
        sous_titre=_sous_titre(ecole, periode or "Toute l'année"),
        pied=ecole.nom, paysage=True,
    )
    par_classe = {}
    for seance in seances:
        par_classe.setdefault(seance.id_classe.nom, []).append(seance)

    for classe, lignes in par_classe.items():
        document.section(classe)
        document.tableau(
            [("Jour", 70, "g"), ("Horaire", 80, "g"), ("Matière", 140, "g"),
             ("Enseignant", 140, "g"), ("Salle", 70, "g"), ("Période", 110, "g")],
            [[s.get_jour_semaine_display(),
              f"{s.heure_debut:%H:%M} – {s.heure_fin:%H:%M}",
              s.id_matiere.nom,
              s.id_enseignant.nom_complet,
              _texte(s.id_salle.nom if s.id_salle_id else None),
              _periode_seance(s)]
             for s in lignes],
        )
        document.espace(8)

    if not par_classe:
        document.paragraphe("Aucun cours planifié pour cette sélection.")

    nom = exports.nom_document("emploi-du-temps",
                               *(par_classe if len(par_classe) == 1 else []),
                               periode or "", ecole.nom)
    return document.rendu(), nom, PDF


def _periode_seance(seance):
    from .serializers import libelle_periode_seance
    return libelle_periode_seance(seance.periodicite, seance.date_debut, seance.date_fin)
