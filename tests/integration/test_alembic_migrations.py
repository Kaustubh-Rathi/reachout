"""Integration tests for Alembic database migrations up and down."""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database import Base
from app.infrastructure.models import SenderAccountModel

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

    def test_configured_database_url_is_used_without_ini_override(self, tmp_path, monkeypatch):
        db_file = tmp_path / "configured_url.db"
        db_url = f"sqlite:///{db_file.as_posix()}"
        monkeypatch.setenv("DATABASE_URL", db_url)

        alembic_cfg = Config(str(ROOT_DIR / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(ROOT_DIR / "migrations"))
        command.upgrade(alembic_cfg, "head")

        assert "alembic_version" in inspect(create_engine(db_url)).get_table_names()

    def test_sender_identity_uniqueness_matches_orm(self, tmp_path):
        db_file = tmp_path / "sender_identity_parity.db"
        db_url = f"sqlite:///{db_file.as_posix()}"
        alembic_cfg = Config(str(ROOT_DIR / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        alembic_cfg.set_main_option("script_location", str(ROOT_DIR / "migrations"))
        command.upgrade(alembic_cfg, "head")

        engine = create_engine(db_url)
        migrated_index = next(
            index
            for index in inspect(engine).get_indexes("sender_accounts")
            if index["name"] == "uq_sender_channel_identity"
        )
        orm_index = next(
            index for index in SenderAccountModel.__table__.indexes if index.name == "uq_sender_channel_identity"
        )
        assert migrated_index["unique"] == orm_index.unique
        assert str(migrated_index["dialect_options"]["sqlite_where"]) == str(
            orm_index.dialect_options["sqlite"]["where"]
        )

        def insert_sender(connection, sender_id, channel, identity):
            connection.execute(
                text(
                    "INSERT INTO sender_accounts "
                    "(id, channel, provider, identity, display_name, status, created_at) "
                    "VALUES (:id, :channel, 'test', :identity, 'Test', 'ACTIVE', '2026-01-01 00:00:00')"
                ),
                {"id": sender_id, "channel": channel, "identity": identity},
            )

        with engine.begin() as connection:
            insert_sender(connection, "sender_1", "WHATSAPP", "+919999999999")
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_sender(connection, "sender_2", "WHATSAPP", "+919999999999")
        with engine.begin() as connection:
            insert_sender(connection, "sender_3", "WHATSAPP", "")
            insert_sender(connection, "sender_4", "WHATSAPP", "")
            insert_sender(connection, "sender_5", "EMAIL", "+919999999999")

        orm_engine = create_engine(f"sqlite:///{(tmp_path / 'orm_sender_identity.db').as_posix()}")
        Base.metadata.create_all(bind=orm_engine)
        with orm_engine.begin() as connection:
            insert_sender(connection, "orm_sender_1", "WHATSAPP", "+919999999999")
        with pytest.raises(IntegrityError):
            with orm_engine.begin() as connection:
                insert_sender(connection, "orm_sender_2", "WHATSAPP", "+919999999999")
        with orm_engine.begin() as connection:
            insert_sender(connection, "orm_sender_3", "WHATSAPP", "")
            insert_sender(connection, "orm_sender_4", "WHATSAPP", "")
            insert_sender(connection, "orm_sender_5", "EMAIL", "+919999999999")
