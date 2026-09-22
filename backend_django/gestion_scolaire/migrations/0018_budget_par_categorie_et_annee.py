"""
Le budget d'un poste de charge passe sur l'année scolaire.

Posé sur le poste, le montant valait pour toutes les années à la fois, et
faisait doublon avec les lignes de `budget` que le bilan lit déjà. Les
montants déjà saisis sont reportés sur l'année active de leur école avant que
la colonne disparaisse ; une école sans année active n'en avait de toute
façon aucun usage.
"""
from django.db import migrations, models


def reporter_sur_lannee(apps, schema_editor):
    TypeDepense = apps.get_model("gestion_scolaire", "TypeDepense")
    AnneeScolaire = apps.get_model("gestion_scolaire", "AnneeScolaire")
    Budget = apps.get_model("gestion_scolaire", "Budget")

    for poste in TypeDepense.objects.exclude(budget_alloue=0):
        annee = (AnneeScolaire.objects
                 .filter(id_ecole=poste.id_ecole, statut="active")
                 .order_by("-date_debut").first())
        if annee is None:
            continue
        # Une ligne existant déjà pour ce poste fait foi : le montant porté
        # par le poste n'était qu'une saisie parallèle, jamais lue par le bilan.
        if Budget.objects.filter(id_type_depense=poste, id_annee_scolaire=annee).exists():
            continue
        Budget.objects.create(
            id_ecole=poste.id_ecole, id_type_depense=poste,
            id_annee_scolaire=annee, categorie=poste.libelle,
            montant_prevu=poste.budget_alloue, montant_realise=0)


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0017_budget_par_categorie"),
    ]

    operations = [
        migrations.RunPython(reporter_sur_lannee, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="typedepense",
            name="budget_alloue",
        ),
    ]
