"""
Justificatifs : facture ou reçu d'une dépense, certificat médical ou mot des
parents d'une absence.

Une pièce comptable n'est pas un fichier public : comme les photos d'élèves,
elle est lue par l'API, derrière l'authentification et le cloisonnement par
école, et vit sous `MEDIA_ROOT/justificatifs/<école>/`. Le champ
`justificatif_url` de la ligne (dépense, absence) en garde le chemin relatif.

Le format est reconnu aux premiers octets plutôt qu'au nom du fichier ou au
type annoncé par le navigateur : les deux se falsifient d'un renommage. Un
PDF, une photo de la facture prise au téléphone — c'est ce qui arrive en
pratique — et rien d'autre.
"""
import uuid
from pathlib import Path

from django.conf import settings

TAILLE_MAX = 5 * 1024 * 1024
DOSSIER = "justificatifs"

FORMATS = "PDF, JPEG, PNG ou WebP"


class ErreurJustificatif(Exception):
    """Fichier refusé ; le message s'affiche tel quel."""


def type_fichier(debut):
    """`(extension, type MIME)` d'après les premiers octets, ou `None`."""
    if debut.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if debut.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if debut.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if debut[:4] == b"RIFF" and debut[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _racine():
    return Path(settings.MEDIA_ROOT).resolve()


def _ecole(objet):
    """École de la ligne : directe sur une dépense, par l'élève sur une absence."""
    ecole = getattr(objet, "id_ecole_id", None)
    return ecole if ecole is not None else objet.id_eleve.id_ecole_id


def chemin(objet):
    """Fichier du justificatif, ou `None` s'il n'y en a pas sur le disque."""
    relatif = objet.justificatif_url
    # Le champ acceptait autrefois une adresse externe saisie à la main : ce
    # n'est pas un fichier que l'école détient.
    if not relatif or "://" in relatif:
        return None
    racine = _racine()
    fichier = (racine / relatif).resolve()
    # Un chemin enregistré ne doit jamais faire sortir du dossier des médias.
    if racine not in fichier.parents or not fichier.is_file():
        return None
    return fichier


def type_mime(fichier):
    with open(fichier, "rb") as f:
        trouve = type_fichier(f.read(12))
    return trouve[1] if trouve else "application/octet-stream"


def nom_telechargement(objet, fichier):
    """Nom lisible à l'enregistrement, plutôt que l'identifiant tiré au sort."""
    if hasattr(objet, "date_depense"):
        libelle, jour = objet.motif or "depense", objet.date_depense
    else:
        libelle = f"absence {objet.id_eleve.prenom} {objet.id_eleve.nom}"
        jour = objet.date_absence
    motif = "".join(c if c.isalnum() else "-" for c in libelle).strip("-")
    return f"justificatif-{motif.lower()[:40]}-{jour:%Y-%m-%d}{fichier.suffix}"


def _effacer(fichier):
    """
    Efface le fichier, sans faire échouer l'opération s'il est verrouillé.

    Sous Windows, un téléchargement encore ouvert tient le fichier : la ligne
    ne doit pas rester attachée à un justificatif qu'on a voulu retirer pour
    autant. Le champ est vidé, le fichier devient orphelin — ce qui se nettoie,
    là où une erreur 500 laisserait l'utilisateur sans solution.
    """
    try:
        fichier.unlink(missing_ok=True)
    except OSError:
        pass


def enregistrer(objet, fichier_envoye):
    """Dépose ou remplace le justificatif ; l'ancien part une fois le nouveau en place."""
    if fichier_envoye.size > TAILLE_MAX:
        raise ErreurJustificatif(
            f"Fichier trop volumineux (maximum {TAILLE_MAX // (1024 * 1024)} Mo).")
    contenu = fichier_envoye.read()
    trouve = type_fichier(contenu[:12])
    if trouve is None:
        raise ErreurJustificatif(f"Format non reconnu : envoyez un fichier {FORMATS}.")
    extension, _ = trouve

    ancien = chemin(objet)
    # Un nom neuf à chaque dépôt : un navigateur ne ressert jamais l'ancien
    # fichier depuis son cache. Le préfixe distingue les pièces d'absence de
    # celles des dépenses, qui partagent le dossier de l'école.
    prefixe = "" if hasattr(objet, "date_depense") else "absence-"
    relatif = (Path(DOSSIER) / str(_ecole(objet))
               / f"{prefixe}{objet.pk}-{uuid.uuid4().hex[:12]}.{extension}")
    destination = _racine() / relatif
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(contenu)

    objet.justificatif_url = relatif.as_posix()
    objet.save(update_fields=["justificatif_url"])
    if ancien is not None:
        _effacer(ancien)


def supprimer(objet):
    fichier = chemin(objet)
    objet.justificatif_url = None
    objet.save(update_fields=["justificatif_url"])
    if fichier is not None:
        _effacer(fichier)
