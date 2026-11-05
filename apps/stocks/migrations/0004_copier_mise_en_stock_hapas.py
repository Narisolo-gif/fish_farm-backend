from django.db import migrations


def copier_hapas_existants(apps, schema_editor):
    MiseEnStock = apps.get_model("stocks", "MiseEnStock")
    MiseEnStockHapa = apps.get_model("stocks", "MiseEnStockHapa")
    db_alias = schema_editor.connection.alias
    lignes = []
    mises = MiseEnStock.objects.using(db_alias).exclude(
        hapa__isnull=True
    ).iterator()
    for mise in mises:
        lignes.append(MiseEnStockHapa(
            mise_en_stock_id=mise.pk,
            hapa_id=mise.hapa_id,
            effectif_entrant=mise.effectif_entrant,
        ))
        if len(lignes) == 500:
            MiseEnStockHapa.objects.using(db_alias).bulk_create(lignes)
            lignes = []
    if lignes:
        MiseEnStockHapa.objects.using(db_alias).bulk_create(lignes)


class Migration(migrations.Migration):

    dependencies = [
        ("stocks", "0003_miseenstockhapa"),
    ]

    operations = [
        migrations.RunPython(copier_hapas_existants, migrations.RunPython.noop),
    ]
