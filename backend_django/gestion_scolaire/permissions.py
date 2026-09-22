"""
Qui peut faire quoi : la matrice des autorisations par profil.

La matrice reprend ligne à ligne le fichier « Matrice des autorisations par
profil » validé avec l'école. Elle est écrite une seule fois, ici : les vues
la lisent pour autoriser une requête, et `/api/auth/moi/` la renvoie au client
pour qu'il n'affiche que ce que le compte a le droit d'ouvrir. Le client ne
fait qu'en tenir compte à l'affichage ; c'est le serveur qui refuse (403).

Plusieurs rôles partagent une même colonne du fichier — un directeur a les
droits d'un administrateur, une secrétaire ceux d'un surveillant. Le rôle
reste distinct sur le compte pour savoir qui est qui ; les droits se lisent
sur le profil.

Au-delà du module, trois comptes ont un périmètre :
- un parent ne voit que ses enfants, un élève que lui-même ;
- un enseignant ne voit et ne saisit notes, présences et bulletins que dans
  ses classes (celles où il est affecté, ou dont il est professeur principal).
"""
from django.db.models import Q
from rest_framework import permissions

ECRITURE = "ecriture"
LECTURE = "lecture"


# ---------- Rôles et profils ----------

class Profil:
    DIRECTION = "direction"
    VIE_SCOLAIRE = "vie_scolaire"
    CAISSIER = "caissier"
    COMPTABLE = "comptable"
    RESPONSABLE_FINANCIER = "responsable_financier"
    ENSEIGNANT = "enseignant"
    PARENT = "parent"
    ELEVE = "eleve"


# Rôle du compte → colonne du fichier.
PROFIL_DU_ROLE = {
    "admin": Profil.DIRECTION,
    "directeur": Profil.DIRECTION,
    "responsable_administratif": Profil.DIRECTION,
    "surveillant": Profil.VIE_SCOLAIRE,
    "secretaire": Profil.VIE_SCOLAIRE,
    "caissier": Profil.CAISSIER,
    "comptable": Profil.COMPTABLE,
    "responsable_financier": Profil.RESPONSABLE_FINANCIER,
    "enseignant": Profil.ENSEIGNANT,
    "parent": Profil.PARENT,
    "eleve": Profil.ELEVE,
}

ROLES_DIRECTION = tuple(r for r, p in PROFIL_DU_ROLE.items() if p == Profil.DIRECTION)
PROFILS_FAMILLE = (Profil.PARENT, Profil.ELEVE)


# ---------- La matrice ----------

# Colonnes dans l'ordre du fichier.
_COLONNES = (Profil.DIRECTION, Profil.VIE_SCOLAIRE, Profil.CAISSIER, Profil.COMPTABLE,
             Profil.RESPONSABLE_FINANCIER, Profil.ENSEIGNANT, Profil.PARENT, Profil.ELEVE)

E, L, _ = ECRITURE, LECTURE, None

_MATRICE = {
    #                        Dir Vie Cai Cpt RFi Ens Par Élè
    "tableau_de_bord":      (E,  E,  L,  L,  L,  L,  L,  L),
    "eleves":               (E,  E,  E,  E,  E,  L,  L,  L),
    "inscriptions":         (E,  E,  E,  E,  E,  _,  L,  L),
    "parents":              (E,  E,  E,  E,  E,  _,  L,  L),
    "classes":              (E,  E,  L,  L,  L,  L,  L,  L),
    "matieres":             (E,  E,  _,  L,  L,  L,  L,  L),
    "emploi_du_temps":      (E,  E,  _,  L,  L,  L,  L,  L),
    "presences":            (E,  E,  _,  _,  _,  E,  L,  L),
    "notes":                (E,  E,  _,  _,  _,  E,  L,  L),
    "bulletins":            (E,  E,  _,  _,  _,  E,  L,  L),
    "personnel":            (E,  L,  L,  L,  L,  _,  _,  _),
    "frais":                (E,  E,  L,  L,  L,  _,  L,  _),
    "encaissement":         (E,  E,  E,  L,  L,  _,  L,  _),
    "relances":             (E,  E,  E,  L,  L,  _,  L,  _),
    "journal_caisse":       (L,  L,  L,  L,  L,  _,  _,  _),
    "depenses":             (E,  E,  _,  E,  E,  _,  _,  _),
    "budget":               (E,  E,  _,  E,  E,  _,  _,  _),
    "types_frais":          (E,  E,  L,  L,  E,  _,  L,  _),
    "parametres":           (E,  _,  _,  _,  _,  _,  _,  _),

    # Hors fichier : ce qui existait déjà, rapporté aux nouveaux profils.
    # Les années scolaires se lisent partout (le bandeau haut en dépend) et ne
    # s'ouvrent ou ne se ferment que par la direction ; comptes, journal
    # d'audit et communications restent à la direction.
    "annees_scolaires":     (E,  L,  L,  L,  L,  L,  L,  L),
    "comptes":              (E,  _,  _,  _,  _,  _,  _,  _),
    "communications":       (E,  L,  L,  L,  L,  L,  L,  L),
}

MATRICE = {
    module: dict(zip(_COLONNES, niveaux)) for module, niveaux in _MATRICE.items()
}


def profil_de(utilisateur):
    return PROFIL_DU_ROLE.get(getattr(utilisateur, "role", None))


def niveau_acces(utilisateur, module):
    """`ECRITURE`, `LECTURE` ou `None` (aucun accès)."""
    return MATRICE.get(module, {}).get(profil_de(utilisateur))


def peut_lire(utilisateur, module):
    return niveau_acces(utilisateur, module) is not None


def peut_ecrire(utilisateur, module):
    return niveau_acces(utilisateur, module) == ECRITURE


def roles_pouvant(module, niveau=ECRITURE):
    """Les rôles qui ont au moins `niveau` sur `module` (ex: qui peut encaisser)."""
    admis = (ECRITURE,) if niveau == ECRITURE else (ECRITURE, LECTURE)
    return tuple(role for role, profil in PROFIL_DU_ROLE.items()
                 if MATRICE.get(module, {}).get(profil) in admis)


def acces_du_compte(utilisateur):
    """Les modules ouverts au compte, pour le client : `{module: niveau}`."""
    return {module: niveau for module in MATRICE
            if (niveau := niveau_acces(utilisateur, module)) is not None}


def est_direction(utilisateur):
    return profil_de(utilisateur) == Profil.DIRECTION


def est_famille(utilisateur):
    return profil_de(utilisateur) in PROFILS_FAMILLE


# ---------- Périmètres ----------

def eleves_de_la_famille(utilisateur):
    """
    Élèves qu'un compte famille a le droit de voir, ou `None` pour un compte
    du personnel (pas de restriction par élève).

    Un compte famille qui n'est rattaché à aucune fiche ne voit rien — et non
    toute l'école.
    """
    from .models import Eleve

    profil = profil_de(utilisateur)
    if profil == Profil.PARENT:
        return Eleve.objects.filter(liens_parents__id_parent__id_utilisateur=utilisateur)
    if profil == Profil.ELEVE:
        return Eleve.objects.filter(id_utilisateur=utilisateur)
    return None


def classes_de_lenseignant(utilisateur):
    """
    Classes d'un enseignant — affecté à une matière ou professeur principal —,
    ou `None` pour un compte qui n'est pas enseignant.
    """
    if profil_de(utilisateur) != Profil.ENSEIGNANT:
        return None
    from .models import Classe

    return Classe.objects.filter(
        Q(affectations__id_enseignant__id_utilisateur=utilisateur)
        | Q(id_enseignant_principal__id_utilisateur=utilisateur)
    ).distinct()


# ---------- Classes de permission DRF ----------

class AccesModule(permissions.BasePermission):
    """
    Applique la matrice à une vue.

    La vue déclare son module (`module = "eleves"`) ; une action qui relève
    d'un autre module le précise dans `modules_par_action`, sous la forme
    `"action": "module"` ou `"action": ("module", ECRITURE)` quand une simple
    lecture prépare une écriture (l'écran de réinscription, par exemple).

    Les actions nommées dans `actions_personnel` portent sur toute l'école
    (palmarès, synthèses, états d'impayés) : elles sont fermées aux comptes
    famille, même quand ceux-ci lisent le module pour leurs enfants.
    """
    message = "Votre profil n'a pas accès à cette fonction."

    def has_permission(self, request, view):
        utilisateur = request.user
        if not (utilisateur and utilisateur.is_authenticated):
            return False

        action = getattr(view, "action", None)
        module = getattr(view, "module", None)
        exige = None
        regle = getattr(view, "modules_par_action", {}).get(action)
        if isinstance(regle, tuple):
            module, exige = regle
        elif regle:
            module = regle

        niveau = niveau_acces(utilisateur, module)
        if niveau is None:
            return False
        if est_famille(utilisateur) and action in getattr(view, "actions_personnel", ()):
            return False
        if exige == ECRITURE or request.method not in permissions.SAFE_METHODS:
            return niveau == ECRITURE
        return True


class EstAdmin(permissions.BasePermission):
    """Profil direction : administrateur, directeur ou responsable administratif."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and est_direction(request.user))


class EstMemeEcole(permissions.BasePermission):
    """
    Isolation multi-écoles : un utilisateur ne peut agir que sur les données
    de sa propre école (id_ecole), quel que soit son rôle.
    """
    def has_object_permission(self, request, view, obj):
        id_ecole_obj = getattr(obj, "id_ecole_id", None)
        if id_ecole_obj is None and hasattr(obj, "id_eleve"):
            id_ecole_obj = obj.id_eleve.id_ecole_id
        return id_ecole_obj == request.user.id_ecole_id
