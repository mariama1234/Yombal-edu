"""
Photos des élèves.

Une photo d'enfant est une donnée personnelle : elle n'est pas servie comme un
fichier statique à une URL devinable, mais lue par l'API, derrière
l'authentification et le cloisonnement par école. Sur le disque, elle vit sous
`MEDIA_ROOT/photos_eleves/<école>/`, et `Eleve.photo_url` en garde le chemin
relatif.

Le format est reconnu aux premiers octets du fichier plutôt qu'à son nom ou au
type annoncé par le navigateur : les deux se falsifient d'un simple renommage.
"""
import uuid
from pathlib import Path

from django.conf import settings

TAILLE_MAX = 3 * 1024 * 1024
DOSSIER = "photos_eleves"


class ErreurPhoto(Exception):
    """Photo refusée ; le message s'affiche tel quel."""


def type_image(debut):
    """`(extension, type MIME)` d'après les premiers octets, ou `None`."""
    if debut.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if debut.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if debut[:4] == b"RIFF" and debut[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _racine():
    return Path(settings.MEDIA_ROOT).resolve()


def chemin(eleve):
    """Fichier de la photo de l'élève, ou `None` s'il n'y en a pas sur le disque."""
    relatif = eleve.photo_url
    # Un ancien `photo_url` peut contenir une adresse externe : ce n'est pas un
    # fichier que l'école détient.
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
        trouve = type_image(f.read(12))
    return trouve[1] if trouve else "application/octet-stream"


def enregistrer(eleve, fichier_envoye):
    """Dépose ou remplace la photo ; l'ancienne est effacée une fois la nouvelle en place."""
    if fichier_envoye.size > TAILLE_MAX:
        raise ErreurPhoto(
            f"Photo trop volumineuse (maximum {TAILLE_MAX // (1024 * 1024)} Mo).")
    contenu = fichier_envoye.read()
    trouve = type_image(contenu[:12])
    if trouve is None:
        raise ErreurPhoto("Format non reconnu : envoyez une photo JPEG, PNG ou WebP.")
    extension, _ = trouve

    ancienne = chemin(eleve)
    # Un nom neuf à chaque dépôt : un navigateur ne ressert jamais l'ancienne
    # photo depuis son cache.
    relatif = (Path(DOSSIER) / str(eleve.id_ecole_id)
               / f"{eleve.pk}-{uuid.uuid4().hex[:12]}.{extension}")
    destination = _racine() / relatif
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(contenu)

    eleve.photo_url = relatif.as_posix()
    eleve.save(update_fields=["photo_url"])
    if ancienne is not None:
        ancienne.unlink(missing_ok=True)


def supprimer(eleve):
    fichier = chemin(eleve)
    eleve.photo_url = None
    eleve.save(update_fields=["photo_url"])
    if fichier is not None:
        fichier.unlink(missing_ok=True)
