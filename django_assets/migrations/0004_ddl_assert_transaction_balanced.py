"""DDL migration (PADR-0008; Product ADR-0004).

Loads the canonical file django_assets/sql/functions/001_assert_transaction_balanced.sql (reverse: functions/down_001_assert_transaction_balanced.sql).
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
    dependencies = [("django_assets", "0003_ddl_dec_domains")]

    operations = [
        migrations.RunPython(
            _mode_aware("functions/001_assert_transaction_balanced.sql"),
            reverse_code=_mode_aware("functions/down_001_assert_transaction_balanced.sql"),
            elidable=False,
        ),
    ]
