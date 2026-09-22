"""Integration tests for Alembic database migrations up and down."""

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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

    def test_migration_004_remaps_legacy_stopped(self, tmp_path):
        db_file = tmp_path / "test_migration_004.db"
        db_url = f"sqlite:///{db_file.as_posix()}"

        alembic_cfg = Config(str(ROOT_DIR / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        alembic_cfg.set_main_option("script_location", str(ROOT_DIR / "migrations"))

        # 1. Upgrade to pre-remap revision
        command.upgrade(alembic_cfg, "003_phase7_endpoint_coverage")

        # 2. Insert legacy rows (STOPPED, STOPPING, plus a RUNNING control)
        # covering all NOT NULL columns required by the 001 campaigns schema.
        engine = create_engine(db_url)
        with engine.begin() as conn:
            for camp_id, status, metadata_json in [
                ("cmp_stopped_01", "STOPPED", "{}"),
                ("cmp_stopping_01", "STOPPING", "{}"),
                ("cmp_running_01", "RUNNING", "{}"),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO campaigns (id, name, channel, status, "
                        "template_ids_json, sender_account_ids_json, created_at, metadata_json) "
                        "VALUES (:id, :name, :channel, :status, "
                        ":template_ids_json, :sender_account_ids_json, :created_at, :metadata_json)"
                    ),
                    {
                        "id": camp_id,
                        "name": f"Campaign {camp_id}",
                        "channel": "WHATSAPP",
                        "status": status,
                        "template_ids_json": "[]",
                        "sender_account_ids_json": "[]",
                        "created_at": "2026-01-01 10:00:00",
                        "metadata_json": metadata_json,
                    },
                )

        # 3. Upgrade to head (applies 004)
        command.upgrade(alembic_cfg, "head")

        # 4. Assert legacy rows remapped, control untouched
        with engine.connect() as conn:
            rows = {
                row[0]: (row[1], row[2])
                for row in conn.execute(text("SELECT id, status, metadata_json FROM campaigns")).fetchall()
            }

        for legacy_id, old_status in [("cmp_stopped_01", "STOPPED"), ("cmp_stopping_01", "STOPPING")]:
            status, metadata_json = rows[legacy_id]
            assert status == "PAUSED"
            meta = json.loads(metadata_json)
            assert meta["remapped_from"] == old_status

        running_status, running_meta = rows["cmp_running_01"]
        assert running_status == "RUNNING"
        assert json.loads(running_meta) == {}
