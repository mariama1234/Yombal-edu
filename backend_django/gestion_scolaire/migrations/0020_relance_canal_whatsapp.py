from django.db import migrations, models


def sms_vers_whatsapp(apps, schema_editor):
    """Les relances déjà envoyées par SMS gardent un canal qui existe encore."""
    apps.get_model("gestion_scolaire", "Relance").objects.filter(canal="sms").update(canal="whatsapp")


def whatsapp_vers_sms(apps, schema_editor):
    apps.get_model("gestion_scolaire", "Relance").objects.filter(canal="whatsapp").update(canal="sms")


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_scolaire", "0019_roles_du_fichier_autorisations"),
    ]

    operations = [
        migrations.AlterField(
            model_name="relance",
            name="canal",
            field=models.CharField(choices=[("whatsapp", "WhatsApp"), ("email", "E-mail")], max_length=10),
        ),
        migrations.RunPython(sms_vers_whatsapp, whatsapp_vers_sms),
    ]
