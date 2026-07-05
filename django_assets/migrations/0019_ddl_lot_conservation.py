"""DDL migration (PADR-0008; ADR-0032 §8, lots spec 2.3)."""

from django.db import migrations


def _mode_aware(relative_path):
    def run(apps, schema_editor):
        from django_assets import conf
        from django_assets.core import ddl

        if conf.ddl_install_mode() == "external":
            return
        ddl.apply_file(schema_editor.connection.alias, relative_path)

    return run


class Migration(migrations.Migration):
    dependencies = [("django_assets", "0018_add_lots_models")]

    operations = [
        migrations.RunPython(
            _mode_aware("functions/004_assert_lot_conservation.sql"),
            reverse_code=_mode_aware("functions/down_004_assert_lot_conservation.sql"),
            elidable=False,
        ),
        migrations.RunPython(
            _mode_aware("triggers/004_lot_conservation.sql"),
            reverse_code=_mode_aware("triggers/down_004_lot_conservation.sql"),
            elidable=False,
        ),
    ]
