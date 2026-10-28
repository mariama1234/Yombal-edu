"""
Retours de la formation du 27/10/2026 (école MIC CITE).

Outre les colonnes nouvelles, la migration reprend l'existant :

* les niveaux connus prennent leur rang dans le cursus (CI avant CP, 6ème
  avant 5ème), les autres viennent après, dans l'ordre alphabétique ;
* une fiche enseignant reliée à un compte reprend le nom, le prénom et les
  coordonnées du compte — jusqu'ici ils n'existaient que là ;
* l'année de recrutement se déduit de la date d'embauche quand elle est connue.
"""
from django.db import migrations, models


def reprendre(apps, schema_editor):
    from gestion_scolaire.scolarite import PAS_ORDRE, rang_par_defaut

    Niveau = apps.get_model("gestion_scolaire", "Niveau")
    suivants = {}
    for niveau in Niveau.objects.order_by("libelle"):
        rang = rang_par_defaut(niveau.libelle)
        if rang is None:
            suivants[niveau.id_ecole_id] = suivants.get(niveau.id_ecole_id, 1000) + PAS_ORDRE
            rang = suivants[niveau.id_ecole_id]
        niveau.ordre = rang
        niveau.save(update_fields=["ordre"])

    Enseignant = apps.get_model("gestion_scolaire", "Enseignant")
    for fiche in Enseignant.objects.select_related("id_utilisateur"):
        compte = fiche.id_utilisateur
        if compte is not None:
            fiche.nom = fiche.nom or compte.nom or ""
            fiche.prenom = fiche.prenom or compte.prenom or ""
            fiche.email = fiche.email or compte.email
            fiche.telephone = fiche.telephone or compte.telephone
        if fiche.date_embauche and not fiche.annee_recrutement:
            fiche.annee_recrutement = fiche.date_embauche.year
        fiche.save(update_fields=["nom", "prenom", "email", "telephone", "annee_recrutement"])


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_scolaire', '0024_ecole_code_ecole'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='classe',
            options={'ordering': ['id_niveau__ordre', 'ordre', 'nom']},
        ),
        migrations.AlterModelOptions(
            name='emploidutemps',
            options={'ordering': ['heure_debut']},
        ),
        migrations.AlterModelOptions(
            name='enseignant',
            options={'ordering': ['nom', 'prenom', 'matricule']},
        ),
        migrations.AlterModelOptions(
            name='matiere',
            options={'ordering': ['ordre', 'nom']},
        ),
        migrations.AlterModelOptions(
            name='niveau',
            options={'ordering': ['ordre', 'libelle']},
        ),
        migrations.RemoveConstraint(
            model_name='emploidutemps',
            name='uq_seance_classe_creneau',
        ),
        migrations.RemoveConstraint(
            model_name='emploidutemps',
            name='uq_seance_enseignant_creneau',
        ),
        migrations.RemoveConstraint(
            model_name='emploidutemps',
            name='uq_seance_salle_creneau',
        ),
        migrations.AddField(
            model_name='absence',
            name='heure_debut',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='absence',
            name='heure_fin',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='absence',
            name='justificatif_url',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='classe',
            name='ordre',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='emploidutemps',
            name='date_debut',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='emploidutemps',
            name='date_fin',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='emploidutemps',
            name='periodicite',
            field=models.CharField(choices=[('annuelle', 'Annuelle'), ('mensuelle', 'Mensuelle'), ('hebdomadaire', 'Hebdomadaire')], default='annuelle', max_length=15),
        ),
        migrations.AddField(
            model_name='enseignant',
            name='annee_recrutement',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='enseignant',
            name='email',
            field=models.EmailField(blank=True, max_length=150, null=True),
        ),
        migrations.AddField(
            model_name='enseignant',
            name='fonction',
            field=models.CharField(choices=[('enseignant', 'Enseignant'), ('directeur', 'Directeur'), ('censeur', 'Censeur'), ('surveillant', 'Surveillant'), ('secretaire', 'Secrétaire'), ('comptable', 'Comptable'), ('caissier', 'Caissier'), ('gardien', 'Gardien'), ('entretien', "Agent d'entretien"), ('chauffeur', 'Chauffeur'), ('autre', 'Autre')], default='enseignant', max_length=20),
        ),
        migrations.AddField(
            model_name='enseignant',
            name='nom',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='enseignant',
            name='prenom',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='enseignant',
            name='telephone',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AddField(
            model_name='enseignant',
            name='type_contrat',
            field=models.CharField(blank=True, choices=[('cdi', 'CDI'), ('cdd', 'CDD'), ('vacataire', 'Vacataire'), ('stagiaire', 'Stagiaire'), ('prestataire', 'Prestataire'), ('benevole', 'Bénévole')], max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='matiere',
            name='ordre',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='niveau',
            name='ordre',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(reprendre, migrations.RunPython.noop),
    ]
