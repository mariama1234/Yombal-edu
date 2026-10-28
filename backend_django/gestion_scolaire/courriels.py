"""
Courriels envoyés par l'application : pour l'instant, les paramètres de
connexion d'un compte.

Le mot de passe n'est jamais relu en base — il n'y est qu'en empreinte. Envoyer
« ses paramètres » à quelqu'un, c'est donc lui attribuer un mot de passe
provisoire neuf et le lui écrire. La vue le fait dans une transaction : si le
courriel ne part pas, l'ancien mot de passe reste valable.

La messagerie se règle dans l'environnement (voir `config/settings.py`) ; sans
réglage, les courriels s'affichent dans la console du serveur.
"""
from django.conf import settings
from django.core.mail import send_mail
from django.utils.crypto import get_random_string

# Sans les caractères qu'on confond en recopiant (0/O, 1/l).
ALPHABET_MOT_DE_PASSE = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ErreurCourriel(Exception):
    """Le courriel n'a pas pu partir ; le message s'affiche tel quel."""


def mot_de_passe_provisoire():
    return get_random_string(10, ALPHABET_MOT_DE_PASSE)


def envoyer_identifiants(compte, mot_de_passe):
    """Écrit à `compte` son identifiant, son mot de passe et l'adresse de connexion."""
    if not getattr(settings, "ENVOI_COURRIELS_ACTIF", True):
        raise ErreurCourriel(
            "La messagerie du serveur n'est pas configurée : aucun courriel ne peut partir. "
            "Transmettez les identifiants à la main, ou demandez le réglage de la messagerie.")
    if not compte.email:
        raise ErreurCourriel("Ce compte n'a pas d'adresse e-mail.")
    ecole = compte.id_ecole.nom if compte.id_ecole_id else "YombalEdu"
    adresse = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    lignes = [
        f"Bonjour {compte.prenom} {compte.nom},".replace("  ", " "),
        "",
        f"Voici vos paramètres de connexion à YombalEdu pour {ecole} :",
        "",
        f"  Identifiant : {compte.email}",
        f"  Mot de passe provisoire : {mot_de_passe}",
        f"  Profil : {compte.get_role_display()}",
    ]
    if adresse:
        lignes.append(f"  Adresse : {adresse}/connexion")
    lignes += [
        "",
        "Changez ce mot de passe dès votre première connexion, depuis « Mon profil ».",
        "Si vous n'attendiez pas ce message, prévenez la direction de l'établissement.",
        "",
        f"— {ecole}",
    ]
    try:
        send_mail(
            subject=f"Vos paramètres de connexion — {ecole}",
            message="\n".join(lignes),
            from_email=None,
            recipient_list=[compte.email],
        )
    except Exception as exc:  # SMTP injoignable, identifiants refusés…
        raise ErreurCourriel(
            "Le courriel n'a pas pu partir. Vérifiez la configuration de la messagerie "
            f"du serveur ({exc.__class__.__name__}).") from exc
