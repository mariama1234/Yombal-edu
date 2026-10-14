"""
Matricule des élèves : `CODE-NNNNN-G-AA`.

* `CODE` : le code de l'école (`Ecole.code_ecole`) ;
* `NNNNN` : numéro chronologique sur cinq chiffres, propre à l'école et jamais
  remis à zéro d'une année sur l'autre ;
* `G` : 1 pour un garçon, 2 pour une fille, 0 tant que le sexe est inconnu ;
* `AA` : deux derniers chiffres de l'année de naissance, 00 tant qu'elle est
  inconnue.

L'inscription exige le sexe et la date de naissance : les 0 ne viennent que
des listes importées, qui n'ont souvent ni l'un ni l'autre. Compléter la fiche
corrige la fin du matricule (voir `Eleve.save`) ; le numéro, lui, ne bouge pas.

Les élèves inscrits sous l'ancien format (`PREFIXE-ANNEE-0001`) le gardent :
il est imprimé sur leurs factures et leurs reçus.
"""
import re
import unicodedata

# La fin du matricule, quel que soit le code : un code d'école modifié après
# coup ne doit ni faire repartir la numérotation à 1 ni bloquer la correction.
_FORMAT = re.compile(r"^(?P<tete>.+-(?P<numero>\d{5}))-[012]-\d{2}$")

CODE_LONGUEUR_MAX = 10
_CODE_VALIDE = re.compile(rf"^[A-Z0-9]{{2,{CODE_LONGUEUR_MAX}}}$")


def chiffre_sexe(sexe):
    return {"M": "1", "F": "2"}.get(sexe or "", "0")


def chiffres_annee(date_naissance):
    return f"{date_naissance.year % 100:02d}" if date_naissance else "00"


def composer(code, numero, sexe, date_naissance):
    return f"{code}-{numero:05d}-{chiffre_sexe(sexe)}-{chiffres_annee(date_naissance)}"


def ajuster(matricule, sexe, date_naissance):
    """
    Le matricule avec une fin à jour du sexe et de la date de naissance.
    Un matricule d'un autre format est rendu tel quel.
    """
    trouve = _FORMAT.match(matricule or "")
    if not trouve:
        return matricule
    return f"{trouve['tete']}-{chiffre_sexe(sexe)}-{chiffres_annee(date_naissance)}"


def dernier_numero(matricules):
    """Le plus grand numéro chronologique parmi ces matricules (0 s'il n'y en a pas)."""
    numeros = (_FORMAT.match(m or "") for m in matricules)
    return max((int(t["numero"]) for t in numeros if t), default=0)


def normaliser_code(valeur):
    """Majuscules, sans accent ni séparateur : « Mic-Cité » donne « MICCITE »."""
    sans_accent = unicodedata.normalize("NFKD", valeur or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", sans_accent.upper())


def code_valide(code):
    return bool(_CODE_VALIDE.match(code or ""))


def code_depuis_nom(nom, pris):
    """
    Code proposé d'après le nom de l'école, absent de `pris`. Le mot « école »
    est retiré, sauf s'il ne reste presque rien (« École A » donne ECOLEA) :
    sinon toutes les écoles commenceraient par ECOLE.
    """
    mots = [m for m in re.split(r"\s+", nom or "") if normaliser_code(m) != "ECOLE"]
    base = normaliser_code("".join(mots))[:6]
    if len(base) < 3:
        base = normaliser_code(nom)[:6]
    if len(base) < 2:
        base = "ECOLE"
    candidat, rang = base, 1
    while candidat in pris:
        rang += 1
        suffixe = str(rang)
        candidat = base[:CODE_LONGUEUR_MAX - len(suffixe)] + suffixe
    return candidat


def prochain_numero(ecole):
    """
    Le numéro chronologique suivant de l'école.

    À appeler dans une transaction : la ligne de l'école est verrouillée pour
    que deux inscriptions simultanées ne tirent pas le même numéro. Verrouiller
    les élèves ne suffisait pas : sans élève encore numéroté, il n'y a rien à
    verrouiller.
    """
    from . import models

    type(ecole).objects.select_for_update().filter(pk=ecole.pk).first()
    return dernier_numero(models.Eleve.objects.filter(id_ecole=ecole)
                          .values_list("matricule", flat=True)) + 1
