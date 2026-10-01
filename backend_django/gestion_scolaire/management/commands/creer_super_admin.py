"""
Crée le compte super administrateur de la plateforme.

    python manage.py creer_super_admin --email moi@yombaledu.sn --prenom Awa --nom Diop

Le mot de passe est demandé au clavier, jamais passé en argument : il finirait
dans l'historique du terminal.
"""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Crée le super administrateur, qui gère les écoles clientes."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--prenom", required=True)
        parser.add_argument("--nom", required=True)

    def handle(self, *args, email, prenom, nom, **options):
        Utilisateur = get_user_model()
        if Utilisateur.objects.filter(email__iexact=email).exists():
            raise CommandError(f"Un compte existe déjà pour {email}.")

        mot_de_passe = getpass("Mot de passe : ")
        if mot_de_passe != getpass("Confirmez le mot de passe : "):
            raise CommandError("Les deux saisies ne correspondent pas.")
        try:
            validate_password(mot_de_passe)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        Utilisateur.objects.create_user(
            email=email, id_ecole=None, password=mot_de_passe,
            role=Utilisateur.Role.SUPER_ADMIN, prenom=prenom, nom=nom)
        self.stdout.write(self.style.SUCCESS(f"Super administrateur créé : {email}"))
