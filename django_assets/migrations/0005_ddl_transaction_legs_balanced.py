"""DDL migration (PADR-0008; Product ADR-0004).

Loads the canonical file django_assets/sql/triggers/001_transaction_legs_balanced.sql (reverse: triggers/down_001_transaction_legs_balanced.sql).
In "external" install mode both directions no-op: the host's own tooling
owns applying the .sql files (ADR-0004).
"""

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
    dependencies = [("django_assets", "0004_ddl_assert_transaction_balanced")]

    operations = [
        migrations.RunPython(
            _mode_aware("triggers/001_transaction_legs_balanced.sql"),
            reverse_code=_mode_aware("triggers/down_001_transaction_legs_balanced.sql"),
            elidable=False,
        ),
    ]
