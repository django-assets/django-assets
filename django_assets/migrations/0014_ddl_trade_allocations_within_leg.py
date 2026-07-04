"""DDL migration (PADR-0008; Product ADR-0004, trades spec 2.4).

Loads functions/002_assert_trade_allocations_within_leg.sql and
triggers/002_trade_allocations_within_leg.sql (reverse: down_ files).
External install mode no-ops both directions.
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
    dependencies = [("django_assets", "0013_add_model_trade_tradeallocation")]

    operations = [
        migrations.RunPython(
            _mode_aware("functions/002_assert_trade_allocations_within_leg.sql"),
            reverse_code=_mode_aware("functions/down_002_assert_trade_allocations_within_leg.sql"),
            elidable=False,
        ),
        migrations.RunPython(
            _mode_aware("triggers/002_trade_allocations_within_leg.sql"),
            reverse_code=_mode_aware("triggers/down_002_trade_allocations_within_leg.sql"),
            elidable=False,
        ),
    ]
