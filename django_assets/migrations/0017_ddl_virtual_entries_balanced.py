"""DDL migration (PADR-0008; ADR-0031, trades spec 2.4)."""

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
    dependencies = [("django_assets", "0016_add_model_virtualtransfer_virtualentry")]

    operations = [
        migrations.RunPython(
            _mode_aware("functions/003_assert_virtual_entries_balanced.sql"),
            reverse_code=_mode_aware("functions/down_003_assert_virtual_entries_balanced.sql"),
            elidable=False,
        ),
        migrations.RunPython(
            _mode_aware("triggers/003_virtual_entries_balanced.sql"),
            reverse_code=_mode_aware("triggers/down_003_virtual_entries_balanced.sql"),
            elidable=False,
        ),
    ]
