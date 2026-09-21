"""Budget alloué à chaque catégorie de dépense ; le disponible s'en déduit."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0016_cloture_caisse"),
    ]

    operations = [
        migrations.AddField(
            model_name="typedepense",
            name="budget_alloue",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
