"""
Import de listes d'élèves depuis un fichier Excel, CSV ou Word.

Pourquoi trois formats : les écoles produisent leurs listes avec ce qu'elles
ont sous la main. À Mic Cité les 17 listes nominatives sont des `.docx` tapés
par les enseignants — n'accepter que l'Excel aurait imposé de ressaisir
près de 600 élèves à la main. Le `.xlsx` et le `.csv` couvrent les échanges
plus structurés (export d'un autre logiciel, saisie en tableur).

Le module ne touche jamais la base : il transforme un fichier en lignes
normalisées, puis construit un *plan* d'import que l'appelant peut soit
afficher en aperçu, soit appliquer. C'est ce qui permet à l'utilisateur de
voir exactement ce qui sera créé avant de valider, sans code dupliqué entre
les deux chemins.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from django.db import transaction

from . import matricules, models

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

EXTENSIONS_SUPPORTEES = (".xlsx", ".csv", ".docx")


class ErreurImport(Exception):
    """Fichier inexploitable — message destiné à être affiché tel quel."""


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _sans_accent(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte)
                   if unicodedata.category(c) != "Mn")


def _cle(texte):
    """Clé de comparaison : sans accent, sans ponctuation, minuscule."""
    return re.sub(r"[^a-z0-9]+", "", _sans_accent(str(texte or "")).lower())


def _nettoyer(texte):
    return re.sub(r"\s+", " ", str(texte or "")).strip()


def normaliser_nom(texte):
    """
    Les listes sont tapées en capitales ; on repasse en casse de titre pour
    que l'affichage soit homogène avec les élèves saisis via le formulaire.
    Les particules collées (N'DIAYE, M'BAYE) et les noms composés gardent
    leur majuscule interne, ce que `str.title()` seul fait déjà correctement.
    """
    texte = _nettoyer(texte)
    if not texte:
        return ""
    return re.sub(r"[A-Za-zÀ-ÿ]+", lambda m: m.group(0).capitalize(), texte.lower())


# ---------------------------------------------------------------------------
# Reconnaissance des classes
# ---------------------------------------------------------------------------

# Ordre important : CE1/CE2/CM1/CM2 avant CI/CP, sinon "CM1" ne serait jamais
# atteint. Les sections de maternelle sont écrites en toutes lettres parce que
# l'abréviation "GS" apparaît déjà dans l'en-tête comme "Groupe Scolaire".
_NIVEAUX = [
    (r"PETITE\s+SECTION", "Petite Section"),
    (r"MOYENNE\s+SECTION", "Moyenne Section"),
    (r"GRANDE\s+SECTION", "Grande Section"),
    (r"\bCE1\b", "CE1"),
    (r"\bCE2\b", "CE2"),
    (r"\bCM1\b", "CM1"),
    (r"\bCM2\b", "CM2"),
    (r"\bCI\b", "CI"),
    (r"\bCP\b", "CP"),
    # Les motifs sont comparés au texte déjà mis en capitales et désaccentué
    # par `deviner_classe` : « 6ème » y arrive sous la forme « 6EME ».
    (r"\b6\s*(?:E|EME)\b", "6ème"),
    (r"\b5\s*(?:E|EME)\b", "5ème"),
    (r"\b4\s*(?:E|EME)\b", "4ème"),
    (r"\b3\s*(?:E|EME)\b", "3ème"),
]


def deviner_classe(texte):
    """
    Extrait ("CE1 A", "CE1") d'un en-tête du genre
    « Liste nominative CE1 A 2025 / 2026 Mme NDIAYE ».

    Renvoie (None, None) si aucun niveau connu n'apparaît : mieux vaut
    demander la classe à l'utilisateur que de deviner faux et créer une
    classe fantôme.
    """
    brut = _sans_accent(_nettoyer(texte)).upper()
    for motif, niveau in _NIVEAUX:
        trouve = re.search(motif, brut)
        if not trouve:
            continue
        # Une lettre isolée juste après le niveau désigne la section (CE1 A).
        # On exige la fin ou un séparateur derrière pour ne pas capturer le
        # "M" de "Mme" ni la première lettre d'un nom d'enseignant.
        suite = brut[trouve.end():]
        section = re.match(r"\s*([A-C])(?![A-Za-z])", suite)
        if section:
            return f"{niveau} {section.group(1)}", niveau
        return niveau, niveau
    return None, None


# ---------------------------------------------------------------------------
# Lignes lues dans le fichier
# ---------------------------------------------------------------------------

@dataclass
class LigneEleve:
    nom: str = ""
    prenom: str = ""
    classe: str = ""
    telephone: str = ""
    sexe: str = ""
    matricule: str = ""
    mention: str = ""       # annotation entre parenthèses trouvée dans la liste
    source: str = ""        # d'où vient la ligne, pour situer une anomalie
    anomalies: list = field(default_factory=list)

    @property
    def complete(self):
        return bool(self.nom and self.prenom and self.classe)


_PARENTHESE = re.compile(r"\(([^)]*)\)")


def _extraire_mention(texte):
    """
    « CISSE ( AVANCE) » → ("Cisse", "AVANCE").

    Les enseignants annotent les listes entre parenthèses (avance, redouble,
    parti...). On sort l'annotation du nom — sinon elle finirait dans l'état
    civil — mais on la conserve pour l'afficher dans l'aperçu, de sorte que
    rien ne disparaisse en silence.
    """
    mention = " / ".join(_nettoyer(m) for m in _PARENTHESE.findall(texte or "") if _nettoyer(m))
    return _nettoyer(_PARENTHESE.sub("", texte or "")), mention


# ---------------------------------------------------------------------------
# Lecture Word
# ---------------------------------------------------------------------------

def _texte_cellule(cellule):
    return _nettoyer(" ".join(
        "".join(t.text or "" for t in p.iter(W + "t"))
        for p in cellule.iter(W + "p")
    ))


def _lire_docx(contenu, classe_defaut=""):
    """
    Un `.docx` contient un tableau par classe, précédé d'un titre. On associe
    chaque tableau au dernier titre exploitable rencontré avant lui : c'est
    ce qui permet à « LISTE PETITE SECTION.docx », qui empile les trois
    sections de maternelle, d'être importé comme trois classes distinctes.

    `classe_defaut` prend le pas sur le titre : c'est le choix explicite de
    l'utilisateur face à un document dont l'en-tête ne nomme aucune classe.
    Il est appliqué pendant la lecture, et non après coup, pour que les
    lignes ne soient pas d'abord marquées « classe introuvable ».
    """
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            racine = ET.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ErreurImport("Fichier Word illisible ou corrompu.") from exc

    corps = racine.find(W + "body")
    if corps is None:
        raise ErreurImport("Document Word vide.")

    lignes = []
    classe_courante = ""
    for element in corps:
        balise = element.tag
        if balise == W + "p":
            titre = _nettoyer("".join(t.text or "" for t in element.iter(W + "t")))
            classe, _ = deviner_classe(titre)
            if classe:
                classe_courante = classe
        elif balise == W + "tbl":
            lignes.extend(_lire_tableau_docx(element, classe_defaut or classe_courante))
    return lignes


def _lire_tableau_docx(tableau, classe):
    lignes = []
    rangees = list(tableau.iter(W + "tr"))
    if not rangees:
        return lignes

    entetes = [_cle(_texte_cellule(c)) for c in rangees[0].findall(W + "tc")]
    colonnes = _reperer_colonnes(entetes)
    # Sans en-tête reconnaissable on retombe sur la disposition des listes de
    # l'école : numéro, prénoms, noms.
    if "nom" not in colonnes or "prenom" not in colonnes:
        colonnes = {"prenom": 1, "nom": 2}
        rangees_donnees = rangees
    else:
        rangees_donnees = rangees[1:]

    for index, rangee in enumerate(rangees_donnees, start=2):
        cellules = [_texte_cellule(c) for c in rangee.findall(W + "tc")]
        ligne = _ligne_depuis_cellules(cellules, colonnes, classe, f"ligne {index}")
        if ligne:
            lignes.append(ligne)
    return lignes


# ---------------------------------------------------------------------------
# Lecture Excel / CSV
# ---------------------------------------------------------------------------

_ALIAS = {
    "nom": {"nom", "noms", "nomdefamille", "nomeleve", "patronyme"},
    "prenom": {"prenom", "prenoms", "prenomeleve"},
    "classe": {"classe", "classes", "niveau", "section"},
    "telephone": {"telephone", "tel", "telephones", "contact", "numero", "portable"},
    "sexe": {"sexe", "genre"},
    "matricule": {"matricule", "matricules", "immatriculation"},
}


def _reperer_colonnes(entetes):
    """Associe un nom de champ à son index de colonne, quel que soit l'intitulé."""
    colonnes = {}
    for index, entete in enumerate(entetes):
        for champ, alias in _ALIAS.items():
            if entete in alias and champ not in colonnes:
                colonnes[champ] = index
    return colonnes


def _ligne_depuis_cellules(cellules, colonnes, classe_defaut, source):
    def valeur(champ):
        index = colonnes.get(champ)
        if index is None or index >= len(cellules):
            return ""
        return _nettoyer(cellules[index])

    nom_brut, prenom_brut = valeur("nom"), valeur("prenom")
    # Une ligne de total, une ligne vide ou un en-tête répété n'ont pas de nom.
    if not nom_brut and not prenom_brut:
        return None
    # Un en-tête répété en milieu de tableau ne doit pas devenir un élève.
    if _cle(nom_brut) in _ALIAS["nom"] or _cle(prenom_brut) in _ALIAS["prenom"]:
        return None

    nom, mention_nom = _extraire_mention(nom_brut)
    prenom, mention_prenom = _extraire_mention(prenom_brut)
    mention = " / ".join(m for m in (mention_nom, mention_prenom) if m)

    sexe = _cle(valeur("sexe"))[:1].upper()
    ligne = LigneEleve(
        nom=normaliser_nom(nom),
        prenom=normaliser_nom(prenom),
        classe=_nettoyer(valeur("classe")) or classe_defaut or "",
        telephone=valeur("telephone"),
        sexe=sexe if sexe in ("M", "F") else "",
        matricule=valeur("matricule"),
        mention=mention,
        source=source,
    )
    if not ligne.nom:
        ligne.anomalies.append("Nom manquant.")
    if not ligne.prenom:
        ligne.anomalies.append("Prénom manquant.")
    if not ligne.classe:
        ligne.anomalies.append("Classe introuvable : précisez-la à l'import.")
    return ligne


def _lire_xlsx(contenu, classe_defaut):
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        raise ErreurImport(
            "La lecture des fichiers Excel nécessite openpyxl (pip install openpyxl)."
        ) from exc

    try:
        classeur = openpyxl.load_workbook(io.BytesIO(contenu), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl lève des types variés
        raise ErreurImport("Fichier Excel illisible ou corrompu.") from exc

    lignes = []
    for feuille in classeur.worksheets:
        rangees = [[_nettoyer("" if c is None else c) for c in r]
                   for r in feuille.iter_rows(values_only=True)]
        rangees = [r for r in rangees if any(r)]
        if not rangees:
            continue

        # Le tableau ne commence pas toujours en A1 : on cherche la première
        # rangée qui ressemble à un en-tête.
        depart, colonnes = 0, {}
        for index, rangee in enumerate(rangees[:10]):
            trouvees = _reperer_colonnes([_cle(c) for c in rangee])
            if "nom" in trouvees and "prenom" in trouvees:
                depart, colonnes = index + 1, trouvees
                break
        if not colonnes:
            raise ErreurImport(
                f"Feuille « {feuille.title} » : aucune colonne « nom » et « prénom » trouvée."
            )

        # Le nom de la feuille sert de classe quand le fichier n'a pas de
        # colonne dédiée — un onglet par classe est une organisation courante.
        classe_feuille, _ = deviner_classe(feuille.title)
        for index, rangee in enumerate(rangees[depart:], start=depart + 1):
            ligne = _ligne_depuis_cellules(
                rangee, colonnes, classe_defaut or classe_feuille,
                f"{feuille.title} ligne {index}",
            )
            if ligne:
                lignes.append(ligne)
    return lignes


def _lire_csv(contenu, classe_defaut):
    for encodage in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texte = contenu.decode(encodage)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 accepte tout octet
        raise ErreurImport("Encodage du CSV non reconnu.")

    try:
        dialecte = csv.Sniffer().sniff(texte[:4096], delimiters=",;\t")
    except csv.Error:
        dialecte = csv.excel
        dialecte.delimiter = ";"

    rangees = [r for r in csv.reader(io.StringIO(texte), dialecte) if any(_nettoyer(c) for c in r)]
    if not rangees:
        raise ErreurImport("Fichier CSV vide.")

    colonnes = _reperer_colonnes([_cle(c) for c in rangees[0]])
    if "nom" not in colonnes or "prenom" not in colonnes:
        raise ErreurImport("Le CSV doit comporter au moins les colonnes « nom » et « prénom ».")

    lignes = []
    for index, rangee in enumerate(rangees[1:], start=2):
        ligne = _ligne_depuis_cellules(rangee, colonnes, classe_defaut, f"ligne {index}")
        if ligne:
            lignes.append(ligne)
    return lignes


def analyser_fichier(nom_fichier, contenu, classe_defaut=""):
    """Point d'entrée de lecture : renvoie les `LigneEleve` brutes du fichier."""
    nom = (nom_fichier or "").lower()
    if nom.endswith(".docx"):
        lignes = _lire_docx(contenu, classe_defaut)
    elif nom.endswith(".xlsx"):
        lignes = _lire_xlsx(contenu, classe_defaut)
    elif nom.endswith(".csv"):
        lignes = _lire_csv(contenu, classe_defaut)
    elif nom.endswith((".xls", ".doc", ".ods")):
        raise ErreurImport(
            "Les anciens formats .xls et .doc ne sont pas lus. "
            "Enregistrez le fichier en .xlsx ou .docx depuis Word ou Excel."
        )
    else:
        raise ErreurImport(
            "Format non reconnu. Formats acceptés : " + ", ".join(EXTENSIONS_SUPPORTEES) + "."
        )

    if not lignes:
        raise ErreurImport("Aucun élève trouvé dans ce fichier.")
    return lignes


# ---------------------------------------------------------------------------
# Construction du plan d'import
# ---------------------------------------------------------------------------

def _capacite_defaut(ecole):
    valeur = (models.ParametreEcole.objects
              .filter(id_ecole=ecole, cle="capacite_classe_defaut")
              .values_list("valeur", flat=True).first())
    try:
        return max(1, int(valeur))
    except (TypeError, ValueError):
        return 40


class _Compteur:
    """
    Distribue les matricules `CODE-00001-G-AA` sans trou ni collision.

    Le compteur démarre au-dessus du plus grand numéro déjà attribué dans
    l'école : reprendre à 1 entrerait en conflit avec la contrainte d'unicité
    `(id_ecole, matricule)` dès le deuxième import. Les listes n'ont pas de
    date de naissance : l'année vaut 00 jusqu'à ce que la fiche soit complétée.
    """

    def __init__(self, ecole):
        self.code = ecole.code_ecole
        self.pris = set(models.Eleve.objects.filter(id_ecole=ecole)
                        .values_list("matricule", flat=True))
        self.dernier = matricules.dernier_numero(self.pris)

    def suivant(self, sexe=""):
        while True:
            self.dernier += 1
            candidat = matricules.composer(self.code, self.dernier, sexe, None)
            if candidat not in self.pris:
                self.pris.add(candidat)
                return candidat

    def reserver(self, matricule):
        """Matricule fourni par le fichier : accepté s'il est encore libre."""
        if not matricule or matricule in self.pris:
            return None
        self.pris.add(matricule)
        return matricule


def construire_plan(ecole, annee, lignes):
    """
    Décide, pour chaque ligne, ce qui sera fait — sans rien écrire.

    Les décisions sont : `creation`, `doublon` (l'élève est déjà inscrit) ou
    `ignore` (ligne inexploitable). Faute de date de naissance dans les
    listes, un élève est identifié par nom + prénom **+ classe**.

    Pourquoi la classe fait partie de l'identité : dans les listes de Mic
    Cité, treize noms apparaissent dans deux classes différentes — un élève
    de CI A et un élève de CM2 A ne sont évidemment pas le même enfant. Se
    limiter à nom + prénom écartait ces homonymes comme des doublons et
    perdait quatorze élèves réels. Deux fois le même nom dans la *même*
    classe reste un doublon : c'est le cas du fichier réimporté.
    """
    compteur = _Compteur(ecole)

    # Élèves déjà en base, avec la ou les classes où ils sont inscrits cette
    # année — un élève sans inscription apparaît avec un ensemble vide.
    existants = {}
    for eleve in models.Eleve.objects.filter(id_ecole=ecole).prefetch_related("inscriptions"):
        identite = (_cle(eleve.nom), _cle(eleve.prenom))
        fiche = existants.setdefault(identite, {"matricule": eleve.matricule, "classes": {}})
        for inscription in eleve.inscriptions.all():
            if inscription.id_annee_scolaire_id == annee.pk:
                fiche["classes"][_cle(inscription.id_classe.nom)] = inscription.id_classe.nom
    classes_connues = {
        _cle(nom): pk for pk, nom in
        models.Classe.objects.filter(id_ecole=ecole, id_annee_scolaire=annee)
        .values_list("pk", "nom")
    }

    vus_dans_le_fichier = {}
    entrees, classes = [], {}

    for ligne in lignes:
        entree = {
            "source": ligne.source,
            "nom": ligne.nom,
            "prenom": ligne.prenom,
            "classe": ligne.classe,
            "telephone": ligne.telephone,
            "sexe": ligne.sexe,
            "mention": ligne.mention,
            "matricule": "",
            "decision": "creation",
            "message": "",
        }

        if ligne.anomalies:
            entree.update(decision="ignore", message=" ".join(ligne.anomalies))
            entrees.append(entree)
            continue

        identite = (_cle(ligne.nom), _cle(ligne.prenom))
        cle_classe = _cle(ligne.classe)
        fiche = existants.get(identite)
        if fiche is not None:
            if cle_classe in fiche["classes"]:
                entree.update(decision="doublon", matricule=fiche["matricule"],
                              message=f"Déjà inscrit en {fiche['classes'][cle_classe]}.")
                entrees.append(entree)
                continue
            if not fiche["classes"]:
                entree.update(decision="doublon", matricule=fiche["matricule"],
                              message="Déjà en base, sans inscription cette année.")
                entrees.append(entree)
                continue
            # Même nom, autre classe : c'est un autre enfant. On le crée, en
            # le signalant pour que l'utilisateur puisse vérifier d'un œil.
            entree["message"] = ("Homonyme d'un élève de "
                                 + ", ".join(sorted(fiche["classes"].values())) + ".")

        if identite + (cle_classe,) in vus_dans_le_fichier:
            entree.update(
                decision="doublon",
                message="Déjà présent dans ce fichier "
                        f"({vus_dans_le_fichier[identite + (cle_classe,)]}).")
            entrees.append(entree)
            continue

        matricule = compteur.reserver(ligne.matricule) or compteur.suivant(ligne.sexe)
        entree["matricule"] = matricule
        vus_dans_le_fichier[identite + (cle_classe,)] = ligne.source

        recap = classes.setdefault(ligne.classe, {
            "nom": ligne.classe,
            "niveau": deviner_classe(ligne.classe)[1] or ligne.classe,
            "existe": _cle(ligne.classe) in classes_connues,
            "eleves": 0,
        })
        recap["eleves"] += 1
        entrees.append(entree)

    return {
        "annee_scolaire": annee.libelle,
        "total": len(entrees),
        "a_creer": sum(1 for e in entrees if e["decision"] == "creation"),
        "doublons": sum(1 for e in entrees if e["decision"] == "doublon"),
        "ignores": sum(1 for e in entrees if e["decision"] == "ignore"),
        "classes": sorted(classes.values(), key=lambda c: c["nom"]),
        "eleves": entrees,
    }


@transaction.atomic
def appliquer_plan(ecole, annee, plan):
    """
    Écrit le plan en base : classes manquantes, élèves, inscriptions.

    Tout est dans une transaction — un import à moitié appliqué laisserait
    des élèves sans inscription, invisibles dans les listes de classe.
    `id_ecole` vient toujours de l'école passée par la vue (celle du compte
    connecté), jamais du fichier : un fichier n'a pas à pouvoir écrire chez
    une autre école.
    """
    capacite = _capacite_defaut(ecole)
    niveaux, classes = {}, {}

    def classe_pour(nom_classe):
        if nom_classe in classes:
            return classes[nom_classe]

        existante = next(
            (c for c in models.Classe.objects.filter(id_ecole=ecole, id_annee_scolaire=annee)
             if _cle(c.nom) == _cle(nom_classe)),
            None,
        )
        if existante is None:
            libelle_niveau = deviner_classe(nom_classe)[1] or nom_classe
            if libelle_niveau not in niveaux:
                niveaux[libelle_niveau], _ = models.Niveau.objects.get_or_create(
                    id_ecole=ecole, libelle=libelle_niveau,
                )
            effectif_prevu = sum(1 for e in plan["eleves"]
                                 if e["decision"] == "creation" and e["classe"] == nom_classe)
            existante = models.Classe.objects.create(
                id_ecole=ecole, id_annee_scolaire=annee, id_niveau=niveaux[libelle_niveau],
                nom=nom_classe, capacite_max=max(capacite, effectif_prevu),
            )
        classes[nom_classe] = existante
        return existante

    date_reference = annee.date_debut
    crees = 0
    for entree in plan["eleves"]:
        if entree["decision"] != "creation":
            continue
        classe = classe_pour(entree["classe"])
        eleve = models.Eleve.objects.create(
            id_ecole=ecole,
            matricule=entree["matricule"],
            nom=entree["nom"],
            prenom=entree["prenom"],
            sexe=entree["sexe"] or None,
            date_premiere_inscription=date_reference,
        )
        models.Inscription.objects.create(
            id_eleve=eleve, id_classe=classe, id_annee_scolaire=annee,
            date_inscription=date_reference,
        )
        crees += 1

    # L'effectif est dénormalisé sur la classe : on le recale sur le nombre
    # réel d'inscriptions plutôt que de l'incrémenter, pour qu'un import
    # rejoué ne le gonfle pas.
    for classe in classes.values():
        classe.effectif = models.Inscription.objects.filter(
            id_classe=classe, id_annee_scolaire=annee,
        ).count()
        classe.save(update_fields=["effectif"])

    return {
        "eleves_crees": crees,
        "classes_creees": [c.nom for c in classes.values() if c.pk and c.effectif],
        "ignores": plan["ignores"],
        "doublons": plan["doublons"],
    }
