"""Official default message template catalog (seed data).

Pure data separated from the MessageTemplate entity/rendering logic.
"""

from __future__ import annotations

from app.domain.enums import Channel
from app.domain.message_template import MessageTemplate

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
            "Hi [Name], hope you're having a productive week at [Company Name]."
            + "\n"
            + "\n"
            + "I'm "
            + _SNAME
            + ", a "
            + _SDEG
            + " '"
            + _SYEAR
            + " grad and "
            + _SEXP
            + ". I specialize in building "
            + "scalable systems and AI/ML solutions (Codeforces "
            + _SCF
            + ")."
            + "\n"
            + "\n"
            + "I'm very interested in the work [Company Name] is doing. If referrals are open, I'd "
            + "appreciate your help. If not, no worries at all—I'd just love to connect and stay on "
            + "your radar for the future."
            + "\n"
            + "\n"
            + "Portfolio: "
            + _SPORT
            + "\n"
            + "LinkedIn: "
            + _SLINK
            + "\n"
            + "GitHub: "
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
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
            "Hey [Name], hope your day is going great!"
            + "\n"
            + "\n"
            + "I'm reaching out because I admire the engineering culture at [Company Name]. I'm "
            + _SNAME
            + " ("
            + _SCOLL
            + " '"
            + _SYEAR
            + ", CGPA "
            + _SCGPA
            + "), and I recently finished an internship at "
            + _SEXP
            + ". "
            + "I also build AI agents and solve competitive programming problems (CF "
            + _SCF
            + ")."
            + "\n"
            + "\n"
            + "I'd love to explore opportunities with your team. If you have a moment, could you take a look at my profiles?"
            + "\n"
            + "\n"
            + "Portfolio: "
            + _SPORT
            + "\n"
            + "LinkedIn: "
            + _SLINK
            + "\n"
            + "GitHub: "
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
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
            "Hi [Name], sorry for the random message!"
            + "\n"
            + "\n"
            + "I'm "
            + _SNAME
            + ", a final year student at "
            + _SCOLL
            + " and "
            + _SEXP
            + ". I've been following "
            + "[Company Name] for a while and would love to be part of the team someday."
            + "\n"
            + "\n"
            + "Even if there are no openings right now, I'd be grateful if you could save my contact. "
            + "I'm always looking to learn from people working at companies like [Company Name]."
            + "\n"
            + "\n"
            + "Portfolio: "
            + _SPORT
            + "\n"
            + "LinkedIn: "
            + _SLINK
            + "\n"
            + "GitHub: "
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
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
            "Hello [Name], I hope you're doing well!"
            + "\n"
            + "\n"
            + "I'm "
            + _SNAME
            + ". I combine systems engineering (internship at "
            + _SEXP
            + ") with AI/ML experience. "
            + "I'm interested in what [Company Name] is building and would love to contribute."
            + "\n"
            + "\n"
            + "Could you please review my profile for a potential referral?"
            + "\n"
            + "\n"
            + "Portfolio: "
            + _SPORT
            + "\n"
            + "LinkedIn: "
            + _SLINK
            + "\n"
            + "GitHub: "
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
            + "\n"
            + "\n"
            + "Thanks for reading!"
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
        subject="Exploring opportunities at [Company Name] | " + _SEXP + " | " + _SCOLL + " " + _SYEAR,
        body=(
            "Hi [Name],"
            + "\n"
            + "\n"
            + "I hope you are having a good week."
            + "\n"
            + "\n"
            + "My name is "
            + _SNAME
            + ". I'm a "
            + _SDEG
            + " "
            + _SYEAR
            + " graduate from "
            + _SCOLL
            + " (CGPA "
            + _SCGPA
            + "). "
            + "I recently completed an internship at "
            + _SEXP
            + "."
            + "\n"
            + "\n"
            + "Alongside software engineering, I work extensively with AI/ML and have built projects "
            + "involving Transformers, multi-agent LLM applications and research automation."
            + "\n"
            + "\n"
            + "I'm reaching out to explore potential opportunities at [Company Name]. If you are open to it, "
            + "I would greatly appreciate a referral or guidance toward relevant roles. If there are no suitable "
            + "openings right now, I completely understand and would be glad to stay connected for future opportunities."
            + "\n"
            + "\n"
            + "Portfolio:"
            + "\n"
            + _SPORT
            + "\n"
            + "\n"
            + "LinkedIn:"
            + "\n"
            + _SLINK
            + "\n"
            + "\n"
            + "GitHub:"
            + "\n"
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
            + "\n"
            + "\n"
            + "My resume is attached."
            + "\n"
            + "\n"
            + "Best regards,"
            + "\n"
            + _SNAME
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="EMAIL-02",
        name="EMAIL_TEMPLATE_02 — From internship to building AI Agents",
        channel=Channel.EMAIL,
        subject="From " + _SEXP + " to building AI Agents — " + _SNAME,
        body=(
            "Hi [Name],"
            + "\n"
            + "\n"
            + "I'm "
            + _SNAME
            + ", a "
            + _SYEAR
            + " "
            + _SDEG
            + " graduate from "
            + _SCOLL
            + " and a recent "
            + _SEXP
            + "."
            + "\n"
            + "\n"
            + "My work sits at the intersection of systems and AI. I've worked on production-oriented software "
            + "engineering and also build AI systems such as research agents, LLM workflows and machine-learning projects."
            + "\n"
            + "\n"
            + "A few things that describe my background:"
            + "\n"
            + "\n"
            + "Big Tech Experience:"
            + "\n"
            + "Intern at "
            + _SEXP
            + "."
            + "\n"
            + "\n"
            + "AI / ML:"
            + "\n"
            + "Multi-agent LLM applications, Transformers and ML systems."
            + "\n"
            + "\n"
            + "Problem Solving:"
            + "\n"
            + "Codeforces "
            + _SCF
            + " and JEE "
            + _SJEE
            + "."
            + "\n"
            + "\n"
            + "I'm currently looking for full-time opportunities and would be grateful if you could point me "
            + "toward relevant openings at [Company Name]."
            + "\n"
            + "\n"
            + "Portfolio:"
            + "\n"
            + _SPORT
            + "\n"
            + "\n"
            + "LinkedIn:"
            + "\n"
            + _SLINK
            + "\n"
            + "\n"
            + "GitHub:"
            + "\n"
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
            + "\n"
            + "\n"
            + "Resume attached."
            + "\n"
            + "\n"
            + "Best,"
            + "\n"
            + _SNAME
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
    MessageTemplate(
        id="EMAIL-03",
        name="EMAIL_TEMPLATE_03 — Deep Learning & Systems",
        channel=Channel.EMAIL,
        subject=_SCOLL + " " + _SYEAR + " | " + _SEXP + " | Deep Learning & Systems",
        body=(
            "Hi [Name],"
            + "\n"
            + "\n"
            + "I'm "
            + _SNAME
            + ", a final-year "
            + _SDEG
            + " student from "
            + _SCOLL
            + "."
            + "\n"
            + "\n"
            + "My interests are centered around building efficient software systems and applying machine learning "
            + "to difficult technical problems. I recently completed an internship and also work on AI/ML systems and "
            + "advanced LLM applications."
            + "\n"
            + "\n"
            + "I'm currently looking for a team where I can combine systems engineering with AI/ML to work on challenging problems."
            + "\n"
            + "\n"
            + "If [Company Name] is hiring "
            + _SYEAR
            + " graduates for SDE, ML, AI or related engineering roles, "
            + "I would appreciate being considered."
            + "\n"
            + "\n"
            + "Portfolio:"
            + "\n"
            + _SPORT
            + "\n"
            + "\n"
            + "LinkedIn:"
            + "\n"
            + _SLINK
            + "\n"
            + "\n"
            + "GitHub:"
            + "\n"
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
            + "\n"
            + "\n"
            + "Resume attached."
            + "\n"
            + "\n"
            + "Regards,"
            + "\n"
            + _SNAME
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
            "Hi [Name],"
            + "\n"
            + "\n"
            + "I'm "
            + _SNAME
            + ", a "
            + _SYEAR
            + " "
            + _SCOLL
            + " graduate who recently completed an internship."
            + "\n"
            + "\n"
            + "My background combines software engineering, AI/ML and competitive programming, "
            + "including a Codeforces rating of "
            + _SCF
            + "."
            + "\n"
            + "\n"
            + "I'm currently exploring full-time engineering opportunities and would be interested in "
            + "contributing to [Company Name]."
            + "\n"
            + "\n"
            + "I've attached my resume and included my work below:"
            + "\n"
            + "\n"
            + "Portfolio:"
            + "\n"
            + _SPORT
            + "\n"
            + "\n"
            + "LinkedIn:"
            + "\n"
            + _SLINK
            + "\n"
            + "\n"
            + "GitHub:"
            + "\n"
            + _SGH
            + "\n"
            + "\n"
            + "Phone: "
            + _SPHONE
            + "\n"
            + "\n"
            + "Thank you for your time."
            + "\n"
            + "\n"
            + "Best regards,"
            + "\n"
            + _SNAME
        ),
        attachment_ref=None,
        phone_number=_SPHONE,
        active=True,
    ),
]

ALL_OFFICIAL_TEMPLATES = OFFICIAL_WHATSAPP_TEMPLATES + OFFICIAL_EMAIL_TEMPLATES
