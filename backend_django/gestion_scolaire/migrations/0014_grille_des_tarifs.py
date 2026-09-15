"""
Tarifs par niveau : de deux colonnes sur le niveau à une grille complète.

Seuls les frais d'inscription et la mensualité pouvaient varier selon le
niveau. Les types de frais étant désormais libres, chacun peut varier : une
ligne `tarif_niveau` porte le montant d'un frais pour un niveau, et une case
absente applique le montant par défaut du frais.

Les montants déjà saisis sur les niveaux sont repris dans la grille avant que
les deux colonnes ne disparaissent.
"""
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

COLONNES = (
    ("montant_inscription", "inscription", "Frais d'inscription", "Annuelle"),
    ("montant_mensualite", "mensualite", "Mensualité", "Mensuelle"),
)


def vers_grille(apps, schema_editor):
    Niveau = apps.get_model("gestion_scolaire", "Niveau")
    TypeFrais = apps.get_model("gestion_scolaire", "TypeFrais")
    TarifNiveau = apps.get_model("gestion_scolaire", "TarifNiveau")
    for niveau in Niveau.objects.all():
        for champ, nature, nom, frequence in COLONNES:
            montant = getattr(niveau, champ)
            if montant is None:
                continue
            type_frais = (TypeFrais.objects
                          .filter(id_ecole_id=niveau.id_ecole_id, nature=nature)
                          .order_by("pk").first())
            if type_frais is None:
                type_frais = TypeFrais.objects.create(
                    id_ecole_id=niveau.id_ecole_id, libelle=nom, nature=nature,
                    montant_defaut=Decimal("0"), frequence=frequence)
            TarifNiveau.objects.update_or_create(
                id_niveau=niveau, id_type_frais=type_frais, defaults={"montant": montant})


def vers_colonnes(apps, schema_editor):
    TarifNiveau = apps.get_model("gestion_scolaire", "TarifNiveau")
    for tarif in TarifNiveau.objects.select_related("id_niveau", "id_type_frais"):
        for champ, nature, _nom, _frequence in COLONNES:
            if tarif.id_type_frais.nature == nature:
                setattr(tarif.id_niveau, champ, tarif.montant)
                tarif.id_niveau.save(update_fields=[champ])


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0013_types_frais_libres"),
    ]

    operations = [
        migrations.CreateModel(
            name="TarifNiveau",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False,
                                           verbose_name="ID")),
                ("montant", models.DecimalField(
                    decimal_places=2, max_digits=12,
                    validators=[django.core.validators.MinValueValidator(Decimal("0"))])),
                ("id_niveau", models.ForeignKey(
                    db_column="id_niveau", on_delete=django.db.models.deletion.CASCADE,
                    related_name="tarifs", to="gestion_scolaire.niveau")),
                ("id_type_frais", models.ForeignKey(
                    db_column="id_type_frais", on_delete=django.db.models.deletion.CASCADE,
                    related_name="tarifs", to="gestion_scolaire.typefrais")),
            ],
            options={
                "db_table": "tarif_niveau",
                "ordering": ["id_niveau", "id_type_frais"],
                "constraints": [models.UniqueConstraint(fields=("id_niveau", "id_type_frais"),
                                                        name="uq_tarif_niveau_type")],
            },
        ),
        migrations.RunPython(vers_grille, vers_colonnes),
        migrations.RemoveField(model_name="niveau", name="montant_inscription"),
        migrations.RemoveField(model_name="niveau", name="montant_mensualite"),
    ]
