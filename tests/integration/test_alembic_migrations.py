"""Integration tests for Alembic database migrations up and down."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class TestAlembicMigrations:
    def test_migration_upgrade_and_downgrade_cycle(self, tmp_path):
        db_file = tmp_path / "test_migration.db"
        db_url = f"sqlite:///{db_file.as_posix()}"

        alembic_cfg = Config(str(ROOT_DIR / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        alembic_cfg.set_main_option("script_location", str(ROOT_DIR / "migrations"))

        # 1. Run Upgrade to head
        command.upgrade(alembic_cfg, "head")

        # Inspect generated tables
        engine = create_engine(db_url)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        expected_tables = {
            "companies",
            "contacts",
            "source_records",
            "sender_accounts",
            "campaigns",
            "message_templates",
            "outreach_attempts",
            "follow_up_reminders",
            "crm_events",
            "suppression_records",
            "alembic_version",
        }
        assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

        # 2. Run Downgrade to base
        command.downgrade(alembic_cfg, "base")
        inspector_after = inspect(engine)
        tables_after = set(inspector_after.get_table_names())
        assert "contacts" not in tables_after
        assert "companies" not in tables_after
        assert "outreach_attempts" not in tables_after

        # 3. Re-upgrade cleanly
        command.upgrade(alembic_cfg, "head")
        inspector_final = inspect(engine)
        assert "contacts" in inspector_final.get_table_names()
        columns = {c["name"] for c in inspector_final.get_columns("outreach_attempts")}
        assert "destination" in columns
