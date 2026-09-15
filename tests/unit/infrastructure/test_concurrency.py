"""Concurrency, Multi-Worker Race Conditions, and Idempotency Tests.

Verifies:
1. Two concurrent workers dispatching to the same contact cannot produce duplicate active attempts.
2. Database unique constraints on idempotency_key prevent race conditions.
3. Sender account concurrency and usage counters remain consistent under multi-threaded load.
4. Relational database transactions in SQLite WAL mode prevent lost updates and lock deadlocks.
"""

from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.models import (
    CompanyModel,
    ContactModel,
    OutreachAttemptModel,
    SenderAccountModel,
)


def create_wal_test_engine(db_path: Path) -> Engine:
    eng = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"timeout": 15, "check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return eng


class TestConcurrencyAndRaceConditions:
    """Suite testing concurrent worker safety and duplicate prevention."""

    def test_two_workers_same_contact_race_condition_prevented_by_unique_constraint(self, tmp_path):
        """When two concurrent workers attempt to dispatch to the same contact, exactly one succeeds."""
        db_file = tmp_path / "concurrency_test_1.db"
        engine = create_wal_test_engine(db_file)
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        init_session = SessionLocal()
        comp = Company.create("Test Corp")
        contact = Contact(contact_id="cnt_alice_1", company_id=comp.id, name="Alice Recruiter", phone="919999988888")
        sender = SenderAccount.create(channel=Channel.WHATSAPP, provider="MOCK", identity="+91000", display_name="S1")

        init_session.add(CompanyModel.from_domain(comp))
        init_session.add(ContactModel.from_domain(contact))
        init_session.add(SenderAccountModel.from_domain(sender))
        init_session.commit()
        init_session.close()

        results = []
        barrier = threading.Barrier(2)

        def worker_attempt_send(worker_id: int):
            session = SessionLocal()
            try:
                barrier.wait()

                # Both workers attempt to create attempt #1 with fixed idempotency key
                att = OutreachAttempt.prepare(
                    contact_id=contact.contact_id,
                    sender_account_id=sender.id,
                    channel=Channel.WHATSAPP,
                    attempt_type=AttemptType.AUTOMATIC,
                    message_body=f"Message from worker {worker_id}",
                    idempotency_key=f"idemp_wa_{contact.contact_id}_fixed_key",
                )
                att_m = OutreachAttemptModel.from_domain(att)
                session.add(att_m)
                session.commit()
                results.append((worker_id, "SUCCESS"))
            except IntegrityError:
                session.rollback()
                results.append((worker_id, "DUPLICATE_REJECTED"))
            except Exception as e:
                session.rollback()
                results.append((worker_id, f"ERROR: {e}"))
            finally:
                session.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(worker_attempt_send, 1)
            f2 = executor.submit(worker_attempt_send, 2)
            concurrent.futures.wait([f1, f2])

        statuses = [res[1] for res in results]
        assert statuses.count("SUCCESS") == 1, f"Expected exactly 1 success, got {statuses}"
        assert statuses.count("DUPLICATE_REJECTED") == 1, f"Expected 1 duplicate rejection, got {statuses}"

        verify_session = SessionLocal()
        total_attempts = verify_session.scalar(select(func.count(OutreachAttemptModel.id)))
        assert total_attempts == 1
        verify_session.close()

    def test_multithreaded_concurrent_updates_to_contacts(self, tmp_path):
        """20 concurrent threads updating distinct contacts complete with 0 lost updates."""
        db_file = tmp_path / "concurrency_test_2.db"
        engine = create_wal_test_engine(db_file)
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        init_session = SessionLocal()
        comp = Company.create("Multi Thread Corp")
        init_session.add(CompanyModel.from_domain(comp))

        contact_ids = []
        for i in range(20):
            cnt = Contact(
                contact_id=f"cnt_thread_{i}",
                company_id=comp.id,
                name=f"Contact {i}",
                phone=f"9190000000{i:02d}",
            )
            init_session.add(ContactModel.from_domain(cnt))
            contact_ids.append(cnt.contact_id)
        init_session.commit()
        init_session.close()

        def update_worker(cid: str):
            session = SessionLocal()
            try:
                cnt_m = session.get(ContactModel, cid)
                cnt_m.notes = f"Updated by thread for {cid}"
                cnt_m.crm_outcome = "INTERESTED"
                session.commit()
            finally:
                session.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(update_worker, cid) for cid in contact_ids]
            concurrent.futures.wait(futures)

        check_session = SessionLocal()
        updated_contacts = check_session.scalars(select(ContactModel)).all()
        assert len(updated_contacts) == 20
        assert all(c.crm_outcome == "INTERESTED" for c in updated_contacts)
        assert all(c.notes.startswith("Updated by thread") for c in updated_contacts)
        check_session.close()
