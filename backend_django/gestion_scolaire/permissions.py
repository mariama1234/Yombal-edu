from rest_framework import permissions


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


class EstAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "admin")


class EstAdminOuCaissier(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ("admin", "caissier")
        )


class EstAdminOuEnseignant(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ("admin", "enseignant")
        )


class LectureSeuleOuAdmin(permissions.BasePermission):
    """Tout utilisateur authentifié peut lire ; seul un admin peut modifier."""
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and request.user.is_authenticated and request.user.role == "admin")
