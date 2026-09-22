"""Architectural & Dependency Direction Verification Tests.

Uses Python AST parsing to statically enforce hexagonal architecture invariants:
1. Domain layer must NOT import web frameworks, ORMs, browser automation, or networking libraries.
2. Domain layer must NOT depend on ports, infrastructure, or adapters.
3. Ports layer must NOT depend on concrete infrastructure or external drivers.
4. Pure domain policies must be free of side-effects (no file I/O, no network calls).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List, Set

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
APP_DIR = ROOT_DIR / "app"
DOMAIN_DIR = APP_DIR / "domain"
PORTS_DIR = APP_DIR / "ports"
INFRA_DIR = APP_DIR / "infrastructure"

# Forbidden external libraries in pure Domain Layer
FORBIDDEN_DOMAIN_IMPORTS = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "playwright",
    "smtplib",
    "ssl",
    "uvicorn",
    "requests",
    "httpx",
    "urllib.request",
    "sqlite3",
    "openpyxl",
    "pydantic",  # Domain uses stdlib dataclasses for zero runtime framework lock-in
}

# Forbidden cross-layer imports in pure Domain Layer
FORBIDDEN_DOMAIN_INTERNAL_IMPORTS = {
    "app.infrastructure",
    "app.ports",
    "app.adapters",
    "app.services",
    "app.api",
    "app.config",
    "crm_server",
    "send_mnc_whatsapp",
    "send_people_email",
}

# Forbidden imports in Ports Layer
FORBIDDEN_PORTS_IMPORTS = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "playwright",
    "smtplib",
    "uvicorn",
    "app.infrastructure",
    "app.adapters",
    "app.services",
    "app.api",
}


def extract_imported_modules(file_path: Path) -> Set[str]:
    """Parse a python file with AST and extract all top-level and from-imported module names."""
    content = file_path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(file_path))
    modules: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)

    return modules


class TestArchitecturalBoundaries:
    """Verifies strict adherence to Hexagonal Architecture / Clean Architecture layer boundaries."""

    def test_domain_layer_has_no_infrastructure_or_framework_imports(self):
        """Domain Layer must be 100% pure Python standard library without framework couplings."""
        domain_files = list(DOMAIN_DIR.rglob("*.py"))
        assert len(domain_files) > 0, "No domain files found to inspect"

        violations: List[str] = []

        for file_path in domain_files:
            if file_path.name.startswith("__pycache__"):
                continue
            imported_modules = extract_imported_modules(file_path)
            for mod in imported_modules:
                base_mod = mod.split(".")[0]
                if base_mod in FORBIDDEN_DOMAIN_IMPORTS:
                    violations.append(f"VIOLATION in {file_path.name}: imports forbidden framework/infra '{mod}'")
                for forbidden in FORBIDDEN_DOMAIN_INTERNAL_IMPORTS:
                    if mod.startswith(forbidden):
                        violations.append(f"VIOLATION in {file_path.name}: domain imports outer layer '{mod}'")

        assert not violations, "\n".join(violations)

    def test_ports_layer_has_no_concrete_infrastructure_dependencies(self):
        """Ports layer defines abstract interfaces and must not depend on concrete infrastructure."""
        port_files = list(PORTS_DIR.rglob("*.py"))
        assert len(port_files) > 0, "No port files found to inspect"

        violations: List[str] = []

        for file_path in port_files:
            if file_path.name.startswith("__pycache__"):
                continue
            imported_modules = extract_imported_modules(file_path)
            for mod in imported_modules:
                base_mod = mod.split(".")[0]
                if base_mod in FORBIDDEN_PORTS_IMPORTS:
                    violations.append(f"VIOLATION in {file_path.name}: port imports concrete infrastructure '{mod}'")
                for forbidden in ["app.infrastructure", "app.adapters", "app.services", "app.api"]:
                    if mod.startswith(forbidden):
                        violations.append(
                            f"VIOLATION in {file_path.name}: port imports concrete adapter/service '{mod}'"
                        )

        assert not violations, "\n".join(violations)

    def test_sqlite_repositories_implement_their_ports(self):
        """Every concrete SQLite repository must explicitly implement its port."""
        from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
        from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
        from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
        from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
        from app.infrastructure.repositories.sqlite_reminder_repository import SqliteReminderRepository
        from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
        from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
        from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
        from app.ports.repositories import (
            CampaignRepository,
            CompanyRepository,
            ContactRepository,
            OutreachRepository,
            ReminderRepository,
            SenderRepository,
            SuppressionRepository,
            TemplateRepository,
        )

        pairs = [
            (SqliteContactRepository, ContactRepository),
            (SqliteCompanyRepository, CompanyRepository),
            (SqliteCampaignRepository, CampaignRepository),
            (SqliteOutreachRepository, OutreachRepository),
            (SqliteSenderRepository, SenderRepository),
            (SqliteTemplateRepository, TemplateRepository),
            (SqliteReminderRepository, ReminderRepository),
            (SqliteSuppressionRepository, SuppressionRepository),
        ]
        for impl, port in pairs:
            assert issubclass(impl, port), f"{impl.__name__} must implement port {port.__name__}"

    def test_services_do_not_import_concrete_infrastructure(self):
        """Application services must depend on ports; only context.py wires adapters."""
        services_dir = APP_DIR / "services"
        violations = []
        for file_path in services_dir.rglob("*.py"):
            if file_path.name == "context.py":
                continue  # the single composition root
            imported_modules = extract_imported_modules(file_path)
            for mod in imported_modules:
                if mod.startswith("app.infrastructure"):
                    violations.append(f"VIOLATION in {file_path.name}: service imports concrete infrastructure '{mod}'")
        assert not violations, "\n".join(violations)

    def test_api_does_not_import_concrete_infrastructure(self):
        """API routers must use app.api.dependencies adapters, not infrastructure directly."""
        api_dir = APP_DIR / "api"
        violations = []
        for file_path in api_dir.rglob("*.py"):
            if file_path.name == "dependencies.py":
                continue  # the single HTTP-to-infrastructure adapter
            imported_modules = extract_imported_modules(file_path)
            for mod in imported_modules:
                if mod.startswith("app.infrastructure"):
                    violations.append(f"VIOLATION in {file_path.name}: API imports concrete infrastructure '{mod}'")
        assert not violations, "\n".join(violations)

    def test_services_do_not_re_export_the_global_event_bus(self):
        """The services package must not re-export the global event bus singleton."""
        init_file = APP_DIR / "services" / "__init__.py"
        content = init_file.read_text(encoding="utf-8")
        assert "event_bus" not in content, "services/__init__.py must not re-export the global event_bus"

    def test_domain_policies_are_pure_functions(self):
        """Domain policy modules must not perform I/O, open files, or access sockets."""
        policy_files = list((DOMAIN_DIR / "policies").glob("*.py"))
        assert len(policy_files) > 0, "No policy files found"

        for p_file in policy_files:
            content = p_file.read_text(encoding="utf-8")
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        assert node.func.id not in {"open", "exec", "eval"}, (
                            f"Impure call '{node.func.id}()' in domain policy {p_file.name}"
                        )

    def test_domain_entities_use_value_semantics_and_encapsulation(self):
        """Key domain models enforce immutability or explicit mutation methods."""
        from app.domain.company import Company
        from app.domain.enums import AttemptType, CampaignStatus, Channel, CRMOutcome, InterviewState, OutreachStatus
        from app.domain.outreach_attempt import OutreachAttempt

        # Enums are unique strings
        assert isinstance(Channel.WHATSAPP, str)
        assert isinstance(OutreachStatus.SENT, str)
        assert isinstance(CampaignStatus.RUNNING, str)
        assert isinstance(CRMOutcome.INTERESTED, str)
        assert isinstance(InterviewState.PENDING, str)

        # Company entity derives stable ID
        c1 = Company.create(name="Acme Corporation")
        assert c1.id == "acme-corporation"
        assert c1.normalized_name == "acme corporation"

        # Outreach attempt requires idempotency key
        attempt = OutreachAttempt.prepare(
            contact_id="cnt_123",
            sender_account_id="snd_456",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello!",
        )
        assert attempt.idempotency_key.startswith("idemp_whatsapp_")
        assert attempt.status == OutreachStatus.PREPARED

    def test_application_services_have_no_concrete_provider_dependencies(self):
        """Application services must depend on abstract ports or factory, never direct concrete providers."""
        services_files = list((APP_DIR / "services").rglob("*.py"))
        assert len(services_files) > 0

        forbidden_concrete = {
            "PlaywrightWhatsAppProvider",
            "SmtpEmailProvider",
            "app.infrastructure.providers.playwright_whatsapp_provider",
            "app.infrastructure.providers.smtp_email_provider",
        }

        violations = []
        for file_path in services_files:
            if file_path.name.startswith("__pycache__"):
                continue
            content = file_path.read_text(encoding="utf-8")
            for forbidden in forbidden_concrete:
                if forbidden in content:
                    violations.append(
                        f"VIOLATION in {file_path.name}: service directly references concrete provider '{forbidden}'"
                    )

        assert not violations, "\n".join(violations)

    def test_api_layer_has_no_direct_playwright_or_smtp(self):
        """API endpoints must not directly instantiate or import Playwright or SMTP libraries."""
        api_files = list((APP_DIR / "api").rglob("*.py"))
        assert len(api_files) > 0

        forbidden_api_imports = {"playwright", "smtplib", "ssl"}
        violations = []

        for file_path in api_files:
            if file_path.name.startswith("__pycache__"):
                continue
            imported_modules = extract_imported_modules(file_path)
            for mod in imported_modules:
                base_mod = mod.split(".")[0]
                if base_mod in forbidden_api_imports:
                    violations.append(f"VIOLATION in {file_path.name}: API directly imports '{mod}'")

        assert not violations, "\n".join(violations)

    def test_scheduler_uses_ports_and_factories(self):
        """Scheduler must interact with providers via ports/factories rather than hardcoding drivers."""
        scheduler_files = list((INFRA_DIR / "scheduler").glob("*.py"))
        assert len(scheduler_files) > 0

        for s_file in scheduler_files:
            content = s_file.read_text(encoding="utf-8")
            # Should not hardcode smtplib or raw playwright in scheduler
            assert "import smtplib" not in content
            assert "from playwright" not in content

    def test_repositories_are_infrastructure_only(self):
        """Repositories must only handle persistence and not import outer API or CLI layers."""
        repo_files = list((INFRA_DIR / "repositories").glob("*.py"))
        assert len(repo_files) > 0

        for r_file in repo_files:
            imported_modules = extract_imported_modules(r_file)
            for mod in imported_modules:
                assert not mod.startswith("app.api"), f"Repository {r_file.name} imports API layer '{mod}'"
                assert not mod.startswith("crm_server"), f"Repository {r_file.name} imports server '{mod}'"

    def test_ui_communicates_via_api_endpoints_only(self):
        """Dashboard UI HTML/JS must communicate with backend strictly via /api/ endpoints."""
        ui_file = ROOT_DIR / "ui" / "crm_dashboard.html"
        if not ui_file.exists():
            ui_file = ROOT_DIR / "crm_dashboard.html"
        assert ui_file.exists()

        content = ui_file.read_text(encoding="utf-8")
        # Ensure UI calls fetch/API
        assert "/api/crm/kpis" in content or "/api/contacts" in content or "/api/" in content
        # Ensure no embedded backend database connection strings or secret tokens in UI
        assert "sqlite:" not in content
        assert "DATABASE_URL" not in content

    def test_ui_has_no_inline_event_handlers(self):
        """UI markup and scripts must use delegated data-* actions, not inline on* handlers."""
        pattern = re.compile(r"\son(?:click|change|input|keydown|keyup|focus|blur|submit|load|error)\s*=", re.I)
        violations = []
        for rel in ("ui/crm_dashboard.html", "ui/app.js"):
            path = ROOT_DIR / rel
            if path.exists():
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if pattern.search(line):
                        violations.append(f"{rel}:{lineno}: {line.strip()[:80]}")
        assert not violations, "Inline event handlers found:\n" + "\n".join(violations)

    def test_ui_fetches_only_through_api_module(self):
        """app.js must route HTTP through modules/api.js rather than calling fetch directly."""
        app_js = ROOT_DIR / "ui" / "app.js"
        if not app_js.exists():
            return
        content = app_js.read_text(encoding="utf-8")
        assert "fetch(" not in content, "app.js must use apiFetch() from modules/api.js, not fetch() directly"

    def test_production_code_never_imports_from_tests_directory(self):
        """Production code under app/ must never import from tests/ or tests.doubles."""
        app_files = list(APP_DIR.rglob("*.py"))
        assert len(app_files) > 0

        violations = []
        for file_path in app_files:
            if file_path.name.startswith("__pycache__"):
                continue
            imported_modules = extract_imported_modules(file_path)
            for mod in imported_modules:
                if mod.startswith("tests") or mod.startswith("tests.doubles"):
                    violations.append(f"VIOLATION: Production file {file_path.name} imports test module '{mod}'")

        assert not violations, "\n".join(violations)

    def test_production_code_has_no_test_doubles_references(self):
        """Production code under app/ must not reference test double classes."""
        app_files = list(APP_DIR.rglob("*.py"))
        assert len(app_files) > 0

        forbidden_tokens = ["FakeWhatsAppProvider", "FakeEmailProvider"]
        violations = []
        for file_path in app_files:
            if file_path.name.startswith("__pycache__"):
                continue
            content = file_path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                if token in content:
                    violations.append(f"VIOLATION: Production file {file_path.name} references test double '{token}'")

        assert not violations, "\n".join(violations)

    def test_all_ui_js_fetches_only_through_api_module(self):
        """Every UI JS file must route HTTP through modules/api.js; no direct fetch() calls."""
        ui_js_files = list((ROOT_DIR / "ui").rglob("*.js"))
        assert len(ui_js_files) > 0, "No UI JS files found to inspect"

        fetch_violations = []
        for file_path in ui_js_files:
            if file_path.name == "api.js" or "api.js" in str(file_path.relative_to(ROOT_DIR)):
                continue
            content = file_path.read_text(encoding="utf-8")
            if "fetch(" in content:
                fetch_violations.append(f"{file_path.relative_to(ROOT_DIR)}: contains direct fetch() call")

        assert not fetch_violations, (
            "Direct fetch() calls found (must use apiFetch from modules/api.js):\n" + "\n".join(fetch_violations)
        )

    def test_ui_js_has_no_inline_event_handlers(self):
        """UI markup and scripts must use delegated data-* actions, not inline on* handlers."""
        pattern = re.compile(r"\son(?:click|change|input|keydown|keyup|focus|blur|submit|load|error)\s*=", re.I)
        violations = []

        html_file = ROOT_DIR / "ui" / "crm_dashboard.html"
        if html_file.exists():
            for lineno, line in enumerate(html_file.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"ui/crm_dashboard.html:{lineno}: {line.strip()[:80]}")

        for js_file in (ROOT_DIR / "ui").rglob("*.js"):
            if js_file.name.startswith("__pycache__"):
                continue
            for lineno, line in enumerate(js_file.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    violations.append(f"{js_file.relative_to(ROOT_DIR)}:{lineno}: {line.strip()[:80]}")

        assert not violations, "Inline event handlers found:\n" + "\n".join(violations)

    def test_ui_action_references_are_registered(self):
        """Every action name referenced in HTML/JS must be registered; no duplicate registrations."""
        ui_js_files = list((ROOT_DIR / "ui").rglob("*.js"))
        assert len(ui_js_files) > 0, "No UI JS files found to inspect"

        registered_names: List[str] = []
        for file_path in ui_js_files:
            content = file_path.read_text(encoding="utf-8")
            for block in re.findall(r"registerActions\(\{([\s\S]*?)\}\)", content):
                for name in block.split(","):
                    name = name.strip().strip("\"'")
                    if name:
                        registered_names.append(name)

        duplicate_names = [name for name in set(registered_names) if registered_names.count(name) > 1]
        assert not duplicate_names, f"Duplicate action registrations: {duplicate_names}"

        registered_set = set(registered_names)

        referenced_names: Set[str] = set()

        html_file = ROOT_DIR / "ui" / "crm_dashboard.html"
        if html_file.exists():
            content = html_file.read_text(encoding="utf-8")
            for _, value in re.findall(r'data-(?:action|change|input)\s*=\s*(["\'])(.*?)\1', content, re.S):
                for name in value.split():
                    referenced_names.add(name)

        for file_path in ui_js_files:
            content = file_path.read_text(encoding="utf-8")
            for _, value in re.findall(r'data-(?:action|change|input)\s*=\s*(["\'])(.*?)\1', content, re.S):
                for name in value.split():
                    referenced_names.add(name)
            for name in re.findall(r"getAction\(\s*['\"]([^'\"]+)['\"]\s*\)", content):
                referenced_names.add(name)

        missing = sorted(name for name in referenced_names if name not in registered_set)
        assert not missing, f"Referenced actions not registered: {missing}"

    def test_ui_dispatcher_throws_on_unknown_action(self):
        """actions.js getAction must throw on unknown action; dispatch.js must call getAction."""
        actions_js = ROOT_DIR / "ui" / "modules" / "actions.js"
        dispatch_js = ROOT_DIR / "ui" / "modules" / "dispatch.js"

        assert actions_js.exists(), "ui/modules/actions.js not found"
        assert dispatch_js.exists(), "ui/modules/dispatch.js not found"

        actions_content = actions_js.read_text(encoding="utf-8")
        dispatch_content = dispatch_js.read_text(encoding="utf-8")

        assert "throw new Error" in actions_content and "Unknown action" in actions_content, (
            "actions.js getAction must throw Error for unknown action"
        )
        assert "getAction(" in dispatch_content, "dispatch.js must call getAction to resolve action names"
