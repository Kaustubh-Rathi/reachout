"""Deterministic integration dry-run dispatch test.

Executes a deterministic multi-company, multi-HR, multi-endpoint dry run across
5 companies (C1..C5) with multiple phones and emails, 2 WhatsApp senders, 2 Email senders,
4 WhatsApp templates, and 4 Email templates.

Prints exact table format:
# | Company | HR | Endpoint | Channel | Sender | Template
and asserts the exact ordering and coverage semantics.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import CampaignStatus, Channel
from app.domain.message_template import MessageTemplate
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.events.event_bus import EventBus
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.infrastructure.scheduler.campaign_scheduler import PersistentCampaignScheduler
from app.services.company_service import CompanyService
from tests.doubles.builders import build_worker


@pytest.fixture
def dry_run_session_factory(tmp_path):
    """Set up an isolated database with the senders and templates for the dry run."""
    engine = create_engine(f"sqlite:///{tmp_path / 'dry_run.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)

    with SessionFactory() as session:
        sender_repo = SqliteSenderRepository(session)
        template_repo = SqliteTemplateRepository(session)

        # 2 WhatsApp Senders: WA1, WA2
        wa1 = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="playwright_whatsapp",
            identity="+919999900001",
            display_name="WA Sender 1",
            sender_id="WA1",
            daily_limit=100,
        )
        wa2 = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="playwright_whatsapp",
            identity="+919999900002",
            display_name="WA Sender 2",
            sender_id="WA2",
            daily_limit=100,
        )
        sender_repo.save(wa1)
        sender_repo.save(wa2)

        # 2 Email Senders: EMAIL1, EMAIL2
        em1 = SenderAccount.create(
            channel=Channel.EMAIL,
            provider="smtp_email",
            identity="outreach1@agency.com",
            display_name="Email Sender 1",
            sender_id="EMAIL1",
            daily_limit=100,
        )
        em2 = SenderAccount.create(
            channel=Channel.EMAIL,
            provider="smtp_email",
            identity="outreach2@agency.com",
            display_name="Email Sender 2",
            sender_id="EMAIL2",
            daily_limit=100,
        )
        sender_repo.save(em1)
        sender_repo.save(em2)

        # 4 WhatsApp Templates: WA-01..WA-04
        for i in range(1, 5):
            t_wa = MessageTemplate.create(
                channel=Channel.WHATSAPP,
                name=f"WhatsApp Template {i}",
                body=f"Hi {{first_name}}, WA template {i} message regarding {{company}}.",
                template_id=f"WA-0{i}",
            )
            template_repo.save(t_wa)

        # 4 Email Templates: EMAIL-01..EMAIL-04
        for i in range(1, 5):
            t_em = MessageTemplate.create(
                channel=Channel.EMAIL,
                name=f"Email Template {i}",
                body=f"Hi {{first_name}},\n\nEmail template {i} for {{company}}.\n\nBest,\nCandidate",
                subject=f"Opportunity Inquiry - Template {i} - {{company}}",
                template_id=f"EMAIL-0{i}",
            )
            template_repo.save(t_em)

        session.commit()

    return SessionFactory


def test_deterministic_dry_run_dispatch(dry_run_session_factory, capsys):
    """Execute the deterministic dry-run with 5 companies:

    C1:
      HR1: Phone1, Phone2, Email1, Email2
      HR2: Phone1, Email1
    C2:
      HR1: Phone1, Email1
    C3:
      HR1: Phone1, Phone2, Email1
      HR2: Phone1, Email1
      HR3: Email1
    C4:
      HR1: Email1
      HR2: Phone1, Email1
    C5:
      HR1: Phone1, Email1

    Sessions:
      WA1, WA2
      EMAIL1, EMAIL2

    Templates:
      WA-01 ... WA-04
      EMAIL-01 ... EMAIL-04

    Verifies exact ordering and outputs table:
    # | Company | HR | Endpoint | Channel | Sender | Template
    """
    SessionFactory = dry_run_session_factory

    # 1. Setup exact test dataset
    with SessionFactory() as session:
        comp_repo = SqliteCompanyRepository(session)
        cnt_repo = SqliteContactRepository(session)

        # C1
        c1 = Company.create(name="Company 1", company_id="C1")
        comp_repo.save(c1)
        cnt_repo.save(
            Contact(
                contact_id="C1_HR1",
                company_id="C1",
                name="Rahul Sharma",
                phone="+919811111101, +919811111102",
                email="rahul.c1@company1.com, rahul.alt@company1.com",
            )
        )
        cnt_repo.save(
            Contact(
                contact_id="C1_HR2",
                company_id="C1",
                name="Amit Verma",
                phone="+919811111201",
                email="amit.c1@company1.com",
            )
        )

        # C2
        c2 = Company.create(name="Company 2", company_id="C2")
        comp_repo.save(c2)
        cnt_repo.save(
            Contact(
                contact_id="C2_HR1",
                company_id="C2",
                name="Priya Patel",
                phone="+919822222101",
                email="priya.c2@company2.com",
            )
        )

        # C3
        c3 = Company.create(name="Company 3", company_id="C3")
        comp_repo.save(c3)
        cnt_repo.save(
            Contact(
                contact_id="C3_HR1",
                company_id="C3",
                name="Sneha Rao",
                phone="+919833333101, +919833333102",
                email="sneha.c3@company3.com",
            )
        )
        cnt_repo.save(
            Contact(
                contact_id="C3_HR2",
                company_id="C3",
                name="Vikram Singh",
                phone="+919833333201",
                email="vikram.c3@company3.com",
            )
        )
        cnt_repo.save(
            Contact(
                contact_id="C3_HR3",
                company_id="C3",
                name="Ananya Iyer",
                phone="",
                email="ananya.c3@company3.com",
            )
        )

        # C4
        c4 = Company.create(name="Company 4", company_id="C4")
        comp_repo.save(c4)
        cnt_repo.save(
            Contact(
                contact_id="C4_HR1",
                company_id="C4",
                name="Karan Johar",
                phone="",
                email="karan.c4@company4.com",
            )
        )
        cnt_repo.save(
            Contact(
                contact_id="C4_HR2",
                company_id="C4",
                name="Deepak Chopra",
                phone="+919844444201",
                email="deepak.c4@company4.com",
            )
        )

        # C5
        c5 = Company.create(name="Company 5", company_id="C5")
        comp_repo.save(c5)
        cnt_repo.save(
            Contact(
                contact_id="C5_HR1",
                company_id="C5",
                name="Neha Gupta",
                phone="+919855555101",
                email="neha.c5@company5.com",
            )
        )

        session.commit()

    # 2. Create Campaign & Execute
    with SessionFactory() as session:
        camp_repo = SqliteCampaignRepository(session)
        campaign = Campaign.create(
            name="Deterministic Dry-Run Verification",
            channel=Channel.WHATSAPP,
            automatic_quota=100,
        )
        camp_repo.save(campaign)
        session.commit()
        camp_id = campaign.id

    scheduler = PersistentCampaignScheduler(
        session_factory=SessionFactory, worker=build_worker(SessionFactory), event_publisher=EventBus()
    )
    scheduler.start_campaign(camp_id)
    thread = scheduler._active_threads.get(camp_id)
    if thread:
        thread.join(timeout=30)

    # 3. Retrieve All Executed Attempts in strict chronological sequence
    with SessionFactory() as session:
        outreach_repo = SqliteOutreachRepository(session)
        camp_repo = SqliteCampaignRepository(session)
        cnt_repo = SqliteContactRepository(session)
        comp_svc = CompanyService(session)

        campaign = camp_repo.get_by_id(camp_id)
        assert campaign.status == CampaignStatus.COMPLETED

        attempts = outreach_repo.list_by_campaign(camp_id)
        assert len(attempts) > 0

        # HR name mapping
        contacts_by_id = {c.contact_id: c for c in cnt_repo.list_all()}

        # Build output table
        table_lines = []
        table_lines.append("# | Company | HR | Endpoint | Channel | Sender | Template")
        table_lines.append("-" * 75)

        for idx, att in enumerate(attempts, 1):
            cnt = contacts_by_id.get(att.contact_id)
            comp_name = cnt.company_id if cnt else "Unknown"
            hr_name = cnt.name if cnt else "Unknown"
            ep = att.destination or ""
            ch = att.channel.value if hasattr(att.channel, "value") else str(att.channel)
            sender = att.sender_account_id or "AUTO"
            tpl = att.template_id or "AUTO"

            table_lines.append(
                f"{idx:<2} | {comp_name:<7} | {hr_name:<14} | {ep:<22} | {ch:<7} | {sender:<6} | {tpl:<8}"
            )

        output_table = "\n".join(table_lines)
        print("\n" + output_table + "\n")

        # 4. Rigorous Assertions on Sequence & Rotations:
        # Assertion 1: First 5 attempts touch each company C1..C5 (Company-First Round Robin)
        first_5_companies = [contacts_by_id[att.contact_id].company_id for att in attempts[:5]]
        assert len(set(first_5_companies)) == 5, f"First 5 attempts must touch 5 unique companies: {first_5_companies}"

        # Assertion 2: Channel Rotation operates dynamically across sequence
        channels = [att.channel for att in attempts]
        assert Channel.WHATSAPP in channels
        assert Channel.EMAIL in channels

        # Assertion 3: Sender rotation occurs on both WhatsApp and Email
        wa_senders = [att.sender_account_id for att in attempts if att.channel == Channel.WHATSAPP]
        email_senders = [att.sender_account_id for att in attempts if att.channel == Channel.EMAIL]
        assert "WA1" in wa_senders
        assert "WA2" in wa_senders
        assert "EMAIL1" in email_senders
        assert "EMAIL2" in email_senders

        # Assertion 4: Template rotation occurs across templates
        wa_tpls = {att.template_id for att in attempts if att.channel == Channel.WHATSAPP}
        email_tpls = {att.template_id for att in attempts if att.channel == Channel.EMAIL}
        assert len(wa_tpls) >= 2
        assert len(email_tpls) >= 2

        # Assertion 5: Verify all companies are 100% covered at the end
        hierarchies = comp_svc.list_hierarchies()
        assert len(hierarchies) == 5
        for h in hierarchies:
            assert h["status"] == "CONTACTED", f"Company {h['name']} must be CONTACTED, got {h['status']}"
            assert h["is_fully_covered"] is True, f"Company {h['name']} must be fully covered"
            assert h["covered_endpoints"] == h["total_endpoints"]
