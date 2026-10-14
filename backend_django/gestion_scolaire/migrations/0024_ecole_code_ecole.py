"""
Code de l'école, qui ouvre désormais le matricule des élèves.

Les écoles existantes reprennent leur ancien préfixe de matricule s'il en
avaient un (« MIC » reste « MIC »), sinon un code tiré de leur nom. Le
paramètre `prefixe_matricule`, remplacé par ce code, est retiré.
Les matricules déjà attribués ne changent pas.
"""
from django.db import migrations, models


def remplir_codes(apps, schema_editor):
    from gestion_scolaire import matricules

    Ecole = apps.get_model("gestion_scolaire", "Ecole")
    ParametreEcole = apps.get_model("gestion_scolaire", "ParametreEcole")
    prefixes = dict(ParametreEcole.objects.filter(cle="prefixe_matricule")
                    .values_list("id_ecole_id", "valeur"))
    pris = set()
    for ecole in Ecole.objects.order_by("pk"):
        code = matricules.normaliser_code(prefixes.get(ecole.pk))
        if not matricules.code_valide(code) or code in pris:
            code = matricules.code_depuis_nom(ecole.nom, pris)
        pris.add(code)
        ecole.code_ecole = code
        ecole.save(update_fields=["code_ecole"])
    ParametreEcole.objects.filter(cle="prefixe_matricule").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0023_ages_niveaux_et_parametres"),
    ]

    operations = [
        migrations.AddField(
            model_name="ecole",
            name="code_ecole",
            field=models.CharField(max_length=10, null=True),
        ),
        migrations.RunPython(remplir_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="ecole",
            name="code_ecole",
            field=models.CharField(max_length=10, unique=True),
        ),
    ]
