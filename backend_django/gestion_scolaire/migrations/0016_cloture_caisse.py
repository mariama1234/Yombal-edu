"""Arrêté de caisse : les totaux d'une journée, figés au moment du comptage."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0015_autorisation_classe_et_auteur_depense"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClotureCaisse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("date_journee", models.DateField()),
                ("total_encaisse", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("total_depense", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("especes_attendues", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("commentaire", models.TextField(blank=True, null=True)),
                ("date_cloture", models.DateTimeField(auto_now_add=True)),
                ("id_ecole", models.ForeignKey(
                    db_column="id_ecole", on_delete=django.db.models.deletion.CASCADE,
                    related_name="clotures_caisse", to="gestion_scolaire.ecole")),
                ("id_utilisateur", models.ForeignKey(
                    blank=True, db_column="id_utilisateur", null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="clotures_caisse", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "cloture_caisse", "ordering": ["-date_journee"]},
        ),
        migrations.AddConstraint(
            model_name="cloturecaisse",
            constraint=models.UniqueConstraint(fields=("id_ecole", "date_journee"),
                                               name="uq_cloture_ecole_journee"),
        ),
    ]
