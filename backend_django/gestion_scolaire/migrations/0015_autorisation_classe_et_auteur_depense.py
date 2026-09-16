"""
Autorisation exceptionnelle d'entrer en classe, et auteur des dépenses.

* `Inscription` : un inscrit incomplet peut être autorisé par l'administration
  à suivre les cours ; motif, date et auteur de la décision sont conservés.
* `Depense.id_utilisateur` : qui a saisi la dépense. Les dépenses existantes
  reprennent l'auteur de leur mouvement de caisse, quand il est connu.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def remplir_saisi_par(apps, schema_editor):
    Depense = apps.get_model("gestion_scolaire", "Depense")
    JournalCaisse = apps.get_model("gestion_scolaire", "JournalCaisse")
    mouvements = (JournalCaisse.objects
                  .filter(id_depense__isnull=False, id_utilisateur__isnull=False)
                  .values_list("id_depense", "id_utilisateur"))
    for id_depense, id_utilisateur in mouvements:
        (Depense.objects.filter(pk=id_depense, id_utilisateur__isnull=True)
         .update(id_utilisateur=id_utilisateur))


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0014_grille_des_tarifs"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="inscription",
            name="autorise_en_classe",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="inscription",
            name="motif_autorisation",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="inscription",
            name="date_autorisation",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inscription",
            name="id_utilisateur_autorisation",
            field=models.ForeignKey(
                blank=True, db_column="id_utilisateur_autorisation", null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="autorisations_en_classe", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="depense",
            name="id_utilisateur",
            field=models.ForeignKey(
                blank=True, db_column="id_utilisateur", null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="depenses_saisies", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(remplir_saisi_par, migrations.RunPython.noop),
    ]
