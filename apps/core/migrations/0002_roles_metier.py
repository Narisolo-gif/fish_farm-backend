from django.db import migrations


ROLES = ("Administrateur", "Directeur", "Gestionnaire", "Employé")


def creer_roles(apps, schema_editor):
    """Rôles métier de la matrice des permissions (le Directeur est nouveau)."""
    Role = apps.get_model("core", "Role")
    for libelle in ROLES:
        Role.objects.get_or_create(libelle=libelle)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        # Retour arrière sans effet : des comptes peuvent déjà porter ces rôles.
        migrations.RunPython(creer_roles, migrations.RunPython.noop),
    ]
