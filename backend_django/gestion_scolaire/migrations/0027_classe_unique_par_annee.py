"""
Une classe ne peut plus exister deux fois sous le même nom, la même année,
dans la même école.

Avant d'ajouter la contrainte, les doublons déjà en base sont renommés —
« 6ème A », « 6ème A (2) », « 6ème A (3) » — plutôt que supprimés : ils
portent peut-être des élèves, des notes ou des frais. Il reste à l'école à
fusionner ou renommer ces classes à la main.
"""
from django.db import migrations, models


def renommer_doublons(apps, schema_editor):
    Classe = apps.get_model("gestion_scolaire", "Classe")
    vus = {}
    noms_pris = set()
    for classe in Classe.objects.order_by("id"):
        cle = (classe.id_ecole_id, classe.id_annee_scolaire_id)
        noms_pris.add((cle, " ".join(classe.nom.split()).lower()))
    for classe in Classe.objects.order_by("id"):
        cle = (classe.id_ecole_id, classe.id_annee_scolaire_id)
        nom = " ".join(classe.nom.split())
        normal = (cle, nom.lower())
        if normal not in vus:
            vus[normal] = classe.id
            if nom != classe.nom:
                classe.nom = nom
                classe.save(update_fields=["nom"])
            continue
        rang = 2
        while (cle, f"{nom} ({rang})".lower()) in noms_pris:
            rang += 1
        nouveau = f"{nom} ({rang})"[:50]
        noms_pris.add((cle, nouveau.lower()))
        classe.nom = nouveau
        classe.save(update_fields=["nom"])


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0026_rythme_emploi_du_temps_par_niveau"),
    ]

    operations = [
        migrations.RunPython(renommer_doublons, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="classe",
            constraint=models.UniqueConstraint(
                fields=("id_ecole", "id_annee_scolaire", "nom"),
                name="uq_classe_ecole_annee_nom",
            ),
        ),
    ]
