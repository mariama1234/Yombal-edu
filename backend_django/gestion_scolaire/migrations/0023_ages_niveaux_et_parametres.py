"""
Préremplit la fourchette d'âge des niveaux existants d'après la grille validée
avec le client, et installe les deux réglages du contrôle d'âge dans chaque
école. Rien n'est écrasé : un niveau déjà renseigné et un paramètre déjà
présent restent tels quels.

Le paramètre `age_minimum_inscription`, qui n'était lu nulle part, est retiré :
la fourchette par niveau le remplace.
"""
from django.db import migrations


PARAMETRES = [
    ("controle_age_inscription", "Contrôle de l'âge à l'inscription", "prevenir",
     "prevenir : avertir, l'inscription reste possible et la dérogation est tracée ; "
     "bloquer : refuser ; desactive : ne pas contrôler."),
    ("date_reference_age", "Date de référence de l'âge", "31/12",
     "Jour (JJ/MM) où l'on calcule l'âge, dans l'année où commence l'année scolaire."),
]


def preremplir(apps, schema_editor):
    from gestion_scolaire.scolarite import fourchette_par_defaut

    Niveau = apps.get_model("gestion_scolaire", "Niveau")
    for niveau in Niveau.objects.filter(age_min__isnull=True, age_max__isnull=True):
        fourchette = fourchette_par_defaut(niveau.libelle)
        if fourchette:
            niveau.age_min, niveau.age_max = fourchette
            niveau.save(update_fields=["age_min", "age_max"])

    Ecole = apps.get_model("gestion_scolaire", "Ecole")
    ParametreEcole = apps.get_model("gestion_scolaire", "ParametreEcole")
    for ecole in Ecole.objects.all():
        for cle, libelle, valeur, description in PARAMETRES:
            ParametreEcole.objects.get_or_create(
                id_ecole=ecole, cle=cle,
                defaults={"libelle": libelle, "valeur": valeur, "type_valeur": "texte",
                          "categorie": "scolarite", "description": description},
            )
    ParametreEcole.objects.filter(cle="age_minimum_inscription").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0022_reunion_age_sante_registre_cas_social"),
    ]

    operations = [
        migrations.RunPython(preremplir, migrations.RunPython.noop),
    ]
