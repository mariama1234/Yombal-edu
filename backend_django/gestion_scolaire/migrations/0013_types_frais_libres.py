"""
Types de frais : du code imposé au nom choisi par l'école.

`libelle` n'est plus une liste fermée (inscription, mensualité, demi-pension…) :
chaque établissement nomme ses frais comme il l'entend. Ce qui décide des
règles — facturation d'office, tarif du niveau, confirmation de l'inscription —
passe dans `nature`.

Les types existants gardent leur sens : l'ancien code devient un nom lisible
(« demi_pension » → « Demi-pension »), et « inscription » / « mensualité »
reçoivent la nature correspondante.
"""
from django.db import migrations, models

NOMS = {
    "inscription": "Inscription",
    "mensualite": "Mensualité",
    "demi_pension": "Demi-pension",
    "transport": "Transport",
    "uniforme": "Uniforme",
    "fournitures": "Fournitures",
    "autre": "Autre",
}
NATURES_D_OFFICE = ("inscription", "mensualite")


def vers_noms_libres(apps, schema_editor):
    TypeFrais = apps.get_model("gestion_scolaire", "TypeFrais")
    for type_frais in TypeFrais.objects.all():
        code = type_frais.libelle
        if code in NATURES_D_OFFICE:
            type_frais.nature = code
        type_frais.libelle = NOMS.get(code, code)
        type_frais.save(update_fields=["libelle", "nature"])


def vers_codes(apps, schema_editor):
    TypeFrais = apps.get_model("gestion_scolaire", "TypeFrais")
    codes = {nom: code for code, nom in NOMS.items()}
    for type_frais in TypeFrais.objects.all():
        if type_frais.nature in NATURES_D_OFFICE:
            type_frais.libelle = type_frais.nature
        else:
            type_frais.libelle = codes.get(type_frais.libelle, "autre")
        type_frais.save(update_fields=["libelle"])


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0012_tarifs_niveau"),
    ]

    operations = [
        migrations.AddField(
            model_name="typefrais",
            name="nature",
            field=models.CharField(
                choices=[("inscription", "Frais d'inscription"), ("mensualite", "Mensualité"),
                         ("autre", "Autre frais (à cocher)")],
                default="autre", max_length=15),
        ),
        migrations.AlterField(
            model_name="typefrais",
            name="libelle",
            field=models.CharField(max_length=100),
        ),
        migrations.RunPython(vers_noms_libres, vers_codes),
    ]
