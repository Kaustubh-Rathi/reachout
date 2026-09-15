"""Unit tests for MessageTemplate domain entity and rendering."""

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import Channel
from app.domain.message_template import MessageTemplate, RenderedMessage


class TestMessageTemplateModel:
    def test_whatsapp_template_creation_and_rendering(self):
        tmpl = MessageTemplate.create(
            name="WhatsApp Tech Outreach v1",
            channel=Channel.WHATSAPP,
            body="Hey {first_name},\nI'm exploring SDE roles at *{company}*.\nRegards,\nTest User",
            attachment_ref="resume_2026.pdf",
        )
        assert tmpl.channel == Channel.WHATSAPP
        assert tmpl.attachment_ref == "resume_2026.pdf"

        contact = Contact(
            contact_id="c1",
            company_id="google",
            name="Mr. Sundar Pichai",
            designation="CEO",
        )
        company = Company.create(name="Google LLC", company_id="google")

        rendered = tmpl.render(contact=contact, company=company)
        assert isinstance(rendered, RenderedMessage)
        assert rendered.subject is None
        assert "Hey Sundar," in rendered.body
        assert "roles at *Google LLC*." in rendered.body
        assert rendered.attachment_ref == "resume_2026.pdf"

    def test_email_template_rendering(self):
        tmpl = MessageTemplate.create(
            name="Email SDE Referral Request",
            channel=Channel.EMAIL,
            subject="Exploring Engineering Opportunities at {company}",
            body="Hi {first_name},\n\nI noticed your role as {designation} at {company}...",
        )
        contact = Contact(
            contact_id="c2",
            company_id="amazon",
            name="Satya Nadella",
            designation="Engineering Director",
        )
        company = Company.create(name="Amazon", company_id="amazon")

        rendered = tmpl.render(contact=contact, company=company)
        assert rendered.subject == "Exploring Engineering Opportunities at Amazon"
        assert "Hi Satya," in rendered.body
        assert "role as Engineering Director at Amazon" in rendered.body

    def test_rendering_missing_placeholders_safe(self):
        tmpl = MessageTemplate.create(
            name="Template with unsupplied var",
            channel=Channel.WHATSAPP,
            body="Hello {first_name}, {custom_variable_not_supplied}",
        )
        contact = Contact(contact_id="c3", company_id="c", name="Jane Doe")
        rendered = tmpl.render(contact=contact)
        assert "Hello Jane, {custom_variable_not_supplied}" in rendered.body
