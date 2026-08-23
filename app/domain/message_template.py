"""Message Template domain entity and rendering engine.

Supports multi-template variants, rotational outreach, channel-specific
template schemas with stable IDs, and variable validation.

Templates reference the sender's personal identity via {sender_*} placeholders,
which are resolved from the git-ignored .env / config (SENDER_PROFILE). No
personal data is committed to the repository.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import SENDER_PROFILE
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import Channel


@dataclass(frozen=True)
class RenderedMessage:
    """Immutable snapshot of a fully interpolated message ready for dispatch."""
    body: str
    subject: Optional[str] = None
    attachment_ref: Optional[str] = None


@dataclass
class MessageTemplate:
    """Represents a parameterized outreach copy template.

    Attributes:
        id: Stable unique template identifier (e.g. 'WA-01' or 'EMAIL-01').
        name: Human-readable template description.
        channel: Applicable channel (WHATSAPP or EMAIL).
        body: Message body text containing placeholders (e.g. [Name], [Company Name], {first_name}, {company}, {sender_name}).
        subject: Email subject line containing placeholders (optional for WhatsApp).
        attachment_ref: Identifier or relative path to resume/attachment.
        phone_number: Default phone number associated with template sender identity.
        active: Boolean flag indicating if template is enabled for rotation.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """
    id: str
    name: str
    channel: Channel
    body: str
    subject: Optional[str] = None
    attachment_ref: Optional[str] = None
    phone_number: Optional[str] = None
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"tmpl_{self.channel.value.lower()}_{uuid.uuid4().hex[:8]}"
        if self.channel == Channel.EMAIL and not self.subject:
            self.subject = "Exploring opportunities at [Company Name]"

    @classmethod
    def create(
        cls,
        name: str,
        channel: Channel,
        body: str,
        template_id: Optional[str] = None,
        subject: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        phone_number: Optional[str] = None,
        active: bool = True,
    ) -> MessageTemplate:
        tid = template_id or f"tmpl_{channel.value.lower()}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        return cls(
            id=tid,
            name=name.strip(),
            channel=channel,
            body=body,
            subject=subject,
            attachment_ref=attachment_ref,
            phone_number=phone_number,
            active=active,
            created_at=now,
            updated_at=now,
        )

    def validate(self, contact: Contact, company: Optional[Company] = None) -> List[str]:
        """Validate that contact and company data provide all required placeholders for this template.

        Returns a list of error messages (empty if valid).
        """
        errors: List[str] = []
        raw_name = (contact.name or "").strip()
        name_val = "" if raw_name.lower() in ("there", "na", "n/a", "null", "none", "") else contact.first_name

        comp_name = (company.name if company and company.name else (contact.company_id or "")).strip()
        if comp_name.lower() in ("na", "n/a", "null", "none", "unknown", ""):
            comp_name = ""

        needs_name = (
            "[Name]" in self.body
            or "{first_name}" in self.body
            or "{name}" in self.body
            or (self.subject and ("[Name]" in self.subject or "{first_name}" in self.subject or "{name}" in self.subject))
        )
        if needs_name and not name_val:
            errors.append("Contact name is missing or cannot be resolved")

        needs_comp = (
            "[Company Name]" in self.body
            or "[Company]" in self.body
            or "{company}" in self.body
            or "{company_name}" in self.body
            or (self.subject and (
                "[Company Name]" in self.subject
                or "[Company]" in self.subject
                or "{company}" in self.subject
                or "{company_name}" in self.subject
            ))
        )
        if needs_comp and not comp_name:
            errors.append("Company name is missing or cannot be resolved")

        rendered = self.render(contact=contact, company=company)
        for token in ["[Name]", "[Company Name]", "[Company]"]:
            if token in rendered.body or (rendered.subject and token in rendered.subject):
                errors.append(f"Unresolved placeholder '{token}' remaining in rendered message")

        return errors

    def render(
        self,
        contact: Contact,
        company: Optional[Company] = None,
        extra_vars: Optional[Dict[str, Any]] = None,
    ) -> RenderedMessage:
        """Interpolate variables into template body and subject safely."""
        company_name = company.name if company and company.name else (contact.company_id or "")
        first_name = contact.first_name or contact.name or ""
        full_name = contact.name or first_name

        context: Dict[str, str] = {
            "name": full_name,
            "first_name": first_name,
            "company": company_name,
            "company_name": company_name,
            "designation": contact.designation or "",
            "phone": contact.phone or "",
            "email": contact.email or "",
            **SENDER_PROFILE,
        }
        if extra_vars:
            for k, v in extra_vars.items():
                context[k] = str(v)

        def _format_str(template_str: str) -> str:
            result = template_str
            if first_name:
                result = result.replace("[Name]", first_name)
            if company_name:
                result = result.replace("[Company Name]", company_name)
                result = result.replace("[Company]", company_name)
            if contact.designation:
                result = result.replace("[Designation]", contact.designation)
            if contact.phone:
                result = result.replace("[Phone]", contact.phone)
            if contact.email:
                result = result.replace("[Email]", contact.email)

            def replacer(match: re.Match) -> str:
                key = match.group(1).strip()
                return context.get(key, match.group(0))

            result = re.sub(r"\{([a-zA-Z0-9_]+)\}", replacer, result)
            return result

        rendered_body = _format_str(self.body)
        rendered_subject = _format_str(self.subject) if self.subject else None

        return RenderedMessage(
            body=rendered_body,
            subject=rendered_subject,
            attachment_ref=self.attachment_ref or (SENDER_PROFILE.get("sender_resume") if self.channel == Channel.EMAIL else None),
        )


# Canonical Phase 6 Official Templates (personal identity via {sender_*} from .env/config)
_SNAME = "{sender_name}"
_SPHONE = "{sender_phone}"
_SPORT = "{sender_portfolio}"
_SLINK = "{sender_linkedin}"
_SGH = "{sender_github}"
_SCOLL = "{sender_college}"
_SDEG = "{sender_degree}"
_SYEAR = "{sender_year}"
_SCGPA = "{sender_cgpa}"
_SEXP = "{sender_experience}"
_SCF = "{sender_codeforces}"
_SJEE = "{sender_jee}"

OFFICIAL_WHATSAPP_TEMPLATES = [
    MessageTemplate(
        id="WA-01",
        name="WA_TEMPLATE_01 — Respectful / Polite",
        channel=Channel.WHATSAPP,
        body=(
            "Hi [Name], hope you're having a productive week at [Company Name]."+ "\n" + "\n" +
            "I'm "+_SNAME+", a "+_SDEG+" '"+_SYEAR+" grad and "+_SEXP+". I specialize in building "+
            "scalable systems and AI/ML solutions (Codeforces "+_SCF+")."+ "\n" + "\n" +
            "I'm very interested in the work [Company Name] is doing. If referrals are open, I'd "+
            "appreciate your help. If not, no worries at all—I'd just love to connect and stay on "+
            "your radar for the future."+ "\n" + "\n" +
            "Portfolio: "+_SPORT+ "\n" +
            "LinkedIn: "+_SLINK+ "\n" +
            "GitHub: "+_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="WA-02",
        name="WA_TEMPLATE_02 — Value First",
        channel=Channel.WHATSAPP,
        body=(
            "Hey [Name], hope your day is going great!"+ "\n" + "\n" +
            "I'm reaching out because I admire the engineering culture at [Company Name]. I'm "+
            _SNAME+" ("+_SCOLL+" '"+_SYEAR+", CGPA "+_SCGPA+"), and I recently finished an internship at "+_SEXP+". "+
            "I also build AI agents and solve competitive programming problems (CF "+_SCF+")."+ "\n" + "\n" +
            "I'd love to explore opportunities with your team. If you have a moment, could you take a look at my profiles?"+ "\n" + "\n" +
            "Portfolio: "+_SPORT+ "\n" +
            "LinkedIn: "+_SLINK+ "\n" +
            "GitHub: "+_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="WA-03",
        name="WA_TEMPLATE_03 — Long-Term Connector",
        channel=Channel.WHATSAPP,
        body=(
            "Hi [Name], sorry for the random message!"+ "\n" + "\n" +
            "I'm "+_SNAME+", a final year student at "+_SCOLL+" and "+_SEXP+". I've been following "+
            "[Company Name] for a while and would love to be part of the team someday."+ "\n" + "\n" +
            "Even if there are no openings right now, I'd be grateful if you could save my contact. "+
            "I'm always looking to learn from people working at companies like [Company Name]."+ "\n" + "\n" +
            "Portfolio: "+_SPORT+ "\n" +
            "LinkedIn: "+_SLINK+ "\n" +
            "GitHub: "+_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="WA-04",
        name="WA_TEMPLATE_04 — Casual / Direct",
        channel=Channel.WHATSAPP,
        body=(
            "Hello [Name], I hope you're doing well!"+ "\n" + "\n" +
            "I'm "+_SNAME+". I combine systems engineering (internship at "+_SEXP+") with AI/ML experience. "+
            "I'm interested in what [Company Name] is building and would love to contribute."+ "\n" + "\n" +
            "Could you please review my profile for a potential referral?"+ "\n" + "\n" +
            "Portfolio: "+_SPORT+ "\n" +
            "LinkedIn: "+_SLINK+ "\n" +
            "GitHub: "+_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE+ "\n" + "\n" +
            "Thanks for reading!"
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
]

OFFICIAL_EMAIL_TEMPLATES = [
    MessageTemplate(
        id="EMAIL-01",
        name="EMAIL_TEMPLATE_01 — Exploring opportunities",
        channel=Channel.EMAIL,
        subject="Exploring opportunities at [Company Name] | "+_SEXP+" | "+_SCOLL+" "+_SYEAR,
        body=(
            "Hi [Name],"+ "\n" + "\n" +
            "I hope you are having a good week."+ "\n" + "\n" +
            "My name is "+_SNAME+". I'm a "+_SDEG+" "+_SYEAR+" graduate from "+_SCOLL+" (CGPA "+_SCGPA+"). "+
            "I recently completed an internship at "+_SEXP+"."+ "\n" + "\n" +
            "Alongside software engineering, I work extensively with AI/ML and have built projects "+
            "involving Transformers, multi-agent LLM applications and research automation."+ "\n" + "\n" +
            "I'm reaching out to explore potential opportunities at [Company Name]. If you are open to it, "+
            "I would greatly appreciate a referral or guidance toward relevant roles. If there are no suitable "+
            "openings right now, I completely understand and would be glad to stay connected for future opportunities."+ "\n" + "\n" +
            "Portfolio:"+ "\n" +_SPORT+ "\n" + "\n" +
            "LinkedIn:"+ "\n" +_SLINK+ "\n" + "\n" +
            "GitHub:"+ "\n" +_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE+ "\n" + "\n" +
            "My resume is attached."+ "\n" + "\n" +
            "Best regards,"+ "\n" +_SNAME
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="EMAIL-02",
        name="EMAIL_TEMPLATE_02 — From internship to building AI Agents",
        channel=Channel.EMAIL,
        subject="From "+_SEXP+" to building AI Agents — "+_SNAME,
        body=(
            "Hi [Name],"+ "\n" + "\n" +
            "I'm "+_SNAME+", a "+_SYEAR+" "+_SDEG+" graduate from "+_SCOLL+" and a recent "+_SEXP+"."+ "\n" + "\n" +
            "My work sits at the intersection of systems and AI. I've worked on production-oriented software "+
            "engineering and also build AI systems such as research agents, LLM workflows and machine-learning projects."+ "\n" + "\n" +
            "A few things that describe my background:"+ "\n" + "\n" +
            "Big Tech Experience:"+ "\n" +
            "Intern at "+_SEXP+"."+ "\n" + "\n" +
            "AI / ML:"+ "\n" +"Multi-agent LLM applications, Transformers and ML systems."+ "\n" + "\n" +
            "Problem Solving:"+ "\n" +"Codeforces "+_SCF+" and JEE "+_SJEE+"."+ "\n" + "\n" +
            "I'm currently looking for full-time opportunities and would be grateful if you could point me "+
            "toward relevant openings at [Company Name]."+ "\n" + "\n" +
            "Portfolio:"+ "\n" +_SPORT+ "\n" + "\n" +
            "LinkedIn:"+ "\n" +_SLINK+ "\n" + "\n" +
            "GitHub:"+ "\n" +_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE+ "\n" + "\n" +
            "Resume attached."+ "\n" + "\n" +
            "Best,"+ "\n" +_SNAME
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="EMAIL-03",
        name="EMAIL_TEMPLATE_03 — Deep Learning & Systems",
        channel=Channel.EMAIL,
        subject=_SCOLL+" "+_SYEAR+" | "+_SEXP+" | Deep Learning & Systems",
        body=(
            "Hi [Name],"+ "\n" + "\n" +
            "I'm "+_SNAME+", a final-year "+_SDEG+" student from "+_SCOLL+"."+ "\n" + "\n" +
            "My interests are centered around building efficient software systems and applying machine learning "+
            "to difficult technical problems. I recently completed an internship and also work on AI/ML systems and "+
            "advanced LLM applications."+ "\n" + "\n" +
            "I'm currently looking for a team where I can combine systems engineering with AI/ML to work on challenging problems."+ "\n" + "\n" +
            "If [Company Name] is hiring "+_SYEAR+" graduates for SDE, ML, AI or related engineering roles, "+
            "I would appreciate being considered."+ "\n" + "\n" +
            "Portfolio:"+ "\n" +_SPORT+ "\n" + "\n" +
            "LinkedIn:"+ "\n" +_SLINK+ "\n" + "\n" +
            "GitHub:"+ "\n" +_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE+ "\n" + "\n" +
            "Resume attached."+ "\n" + "\n" +
            "Regards,"+ "\n" +_SNAME
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="EMAIL-04",
        name="EMAIL_TEMPLATE_04 — Exploring SDE / AI opportunities",
        channel=Channel.EMAIL,
        subject="Exploring SDE / AI opportunities at [Company Name]",
        body=(
            "Hi [Name],"+ "\n" + "\n" +
            "I'm "+_SNAME+", a "+_SYEAR+" "+_SCOLL+" graduate who recently completed an internship."+ "\n" + "\n" +
            "My background combines software engineering, AI/ML and competitive programming, "+
            "including a Codeforces rating of "+_SCF+"."+ "\n" + "\n" +
            "I'm currently exploring full-time engineering opportunities and would be interested in "+
            "contributing to [Company Name]."+ "\n" + "\n" +
            "I've attached my resume and included my work below:"+ "\n" + "\n" +
            "Portfolio:"+ "\n" +_SPORT+ "\n" + "\n" +
            "LinkedIn:"+ "\n" +_SLINK+ "\n" + "\n" +
            "GitHub:"+ "\n" +_SGH+ "\n" + "\n" +
            "Phone: "+_SPHONE+ "\n" + "\n" +
            "Thank you for your time."+ "\n" + "\n" +
            "Best regards,"+ "\n" +_SNAME
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
]

ALL_OFFICIAL_TEMPLATES = OFFICIAL_WHATSAPP_TEMPLATES + OFFICIAL_EMAIL_TEMPLATES
