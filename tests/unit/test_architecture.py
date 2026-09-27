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
UI_DIR = ROOT_DIR / "ui"

# Modules imported by the runtime entry point rather than by another app/ module.
APP_ENTRYPOINTS = {"app.main", "app.composition"}

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


def app_module_name(file_path: Path) -> str:
    """Dotted module name for a file under app/ (package __init__ maps to its package)."""
    parts = list(file_path.relative_to(APP_DIR).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["app", *parts])


def app_package_name(file_path: Path) -> str:
    """Dotted package a file under app/ belongs to (for resolving relative imports)."""
    parts = list(file_path.relative_to(APP_DIR).parts)
    if parts[-1] != "__init__.py":
        parts = parts[:-1]
    return ".".join(["app", *parts]) if parts else "app"


def iter_python_sources(*roots: Path) -> List[Path]:
    """All python sources under the given roots, skipping caches and virtualenvs."""
    skip = {".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"}
    found: List[Path] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if skip & set(path.parts):
                continue
            found.append(path)
    return sorted(found)


def iter_ui_scripts() -> List[Path]:
    """All dashboard javascript modules, excluding any vendored node_modules."""
    return sorted(p for p in UI_DIR.rglob("*.js") if "node_modules" not in p.parts)


class TestNoDeadCode:
    """Guards that fail when code becomes unreachable, so dead code cannot silently accumulate.

    These are the regression checks for a one-off dead-code sweep that removed an
    orphaned domain policy module, a test-only contact filter, a test-only entity
    mutator, and two unused test doubles. Each guard was verified to flag the original
    offender and to pass on the cleaned tree.
    """

    def test_no_orphan_app_modules(self):
        """Every app/ module must be imported by another app/ module (or be an entrypoint)."""
        sources = [p for p in iter_python_sources(APP_DIR) if p.name != "__init__.py"]
        assert sources, "No app/ modules discovered"

        imported: Set[str] = set()
        for path in iter_python_sources(APP_DIR):
            package = app_package_name(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = package.split(".")
                        base = base[: len(base) - (node.level - 1)]
                        if node.module:
                            base = base + node.module.split(".")
                        module = ".".join(base)
                    else:
                        module = node.module or ""
                    if module.split(".")[0] == "app":
                        imported.add(module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] == "app":
                            imported.add(alias.name)

        orphans = sorted(
            app_module_name(p)
            for p in sources
            if app_module_name(p) not in imported and app_module_name(p) not in APP_ENTRYPOINTS
        )
        assert not orphans, (
            "Orphaned app/ modules (never imported by app/ code - delete or wire them up):\n" + "\n".join(orphans)
        )

    def test_app_module_functions_have_production_callers(self):
        """Module-level app/ functions must be called by app/ code, not only by tests.

        A function whose sole references live in tests/ is dead weight: the test exists
        only to cover it. Decorated functions are exempt because the decorator (route
        handler, middleware, SQLAlchemy hook) is itself the caller.
        """
        prod = iter_python_sources(APP_DIR)
        prod_sources = {p: p.read_text(encoding="utf-8") for p in prod}
        everything = {
            p: p.read_text(encoding="utf-8", errors="ignore")
            for p in iter_python_sources(ROOT_DIR / "app", ROOT_DIR / "tests", ROOT_DIR / "scripts")
        }
        assert prod_sources, "No app/ modules discovered"

        orphans: List[str] = []
        for path in prod:
            tree = ast.parse(prod_sources[path], filename=str(path))
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.decorator_list or node.name.startswith("__"):
                    continue
                pattern = re.compile(r"(?<![\w.])" + re.escape(node.name) + r"\b")
                has_prod_caller = False
                for p, text in prod_sources.items():
                    for match in pattern.finditer(text):
                        if p == path and text[: match.start()].count("\n") + 1 == node.lineno:
                            continue  # the declaration itself is not a call
                        has_prod_caller = True
                        break
                    if has_prod_caller:
                        break
                if not has_prod_caller:
                    elsewhere = any(pattern.search(text) for p, text in everything.items() if p != path)
                    orphans.append(
                        f"{path.relative_to(ROOT_DIR).as_posix()}:{node.lineno} {node.name}()"
                        + ("  (referenced only by tests - dead code)" if elsewhere else "  (no references at all)")
                    )

        assert not orphans, "Dead app/ module-level functions:\n" + "\n".join(orphans)

    def test_no_unused_ui_js_exports(self):
        """Every exported dashboard symbol must be consumed by another UI module or the HTML."""
        scripts = iter_ui_scripts()
        assert scripts, "No UI JS modules discovered"

        html_file = UI_DIR / "crm_dashboard.html"
        html = html_file.read_text(encoding="utf-8") if html_file.exists() else ""
        sources = {p: p.read_text(encoding="utf-8") for p in scripts}

        unused: List[str] = []
        for path in scripts:
            for match in re.finditer(
                r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)", sources[path], re.M
            ):
                name = match.group(1)
                pattern = re.compile(r"(?<![\w$])" + re.escape(name) + r"\b")
                consumed = any(pattern.search(text) for other, text in sources.items() if other != path)
                if not consumed and not pattern.search(html):
                    lineno = sources[path][: match.start()].count("\n") + 1
                    unused.append(f"{path.relative_to(ROOT_DIR).as_posix()}:{lineno} export {name}")

        assert not unused, "Unused exports in ui/ (delete them or wire them up):\n" + "\n".join(unused)

    def test_every_api_client_method_is_called(self):
        """Every method on the api client must be called by the dashboard."""
        api_file = UI_DIR / "modules" / "api.js"
        assert api_file.exists(), "ui/modules/api.js not found"

        client = api_file.read_text(encoding="utf-8")
        block = re.search(r"export const api = \{(.*?)\n\};", client, re.S)
        assert block, "Could not locate the `export const api = {...}` client in ui/modules/api.js"

        consumers = "\n".join(p.read_text(encoding="utf-8") for p in iter_ui_scripts() if p != api_file)
        html_file = UI_DIR / "crm_dashboard.html"
        if html_file.exists():
            consumers += "\n" + html_file.read_text(encoding="utf-8")

        unused = [
            name
            for name in re.findall(r"^  ([A-Za-z_$][\w$]*):", block.group(1), re.M)
            if not re.search(r"\." + re.escape(name) + r"\b", consumers)
        ]
        assert not unused, f"Unused api client methods in ui/modules/api.js (no caller found): {unused}"
