"""Unit tests for attachment path resolution."""

from pathlib import Path

from app.config import ROOT_DIR
from app.domain.enums import Channel
from app.domain.message_template import MessageTemplate, normalize_attachment_ref
from app.infrastructure.providers.attachments import resolve_attachment_path
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository


class TestResolveAttachmentPath:
    def test_empty_references_resolve_to_none(self):
        assert resolve_attachment_path(None) is None
        assert resolve_attachment_path("") is None
        assert resolve_attachment_path("   ") is None

    def test_absolute_path_passes_through(self):
        assert resolve_attachment_path(r"D:\Resume\cv.pdf") == Path(r"D:\Resume\cv.pdf")

    def test_surrounding_double_quotes_are_stripped(self):
        assert resolve_attachment_path(r'"D:\Resume\cv.pdf"') == Path(r"D:\Resume\cv.pdf")

    def test_surrounding_single_quotes_are_stripped(self):
        assert resolve_attachment_path(r"'D:\Resume\cv.pdf'") == Path(r"D:\Resume\cv.pdf")

    def test_quoted_path_with_inner_whitespace(self):
        assert resolve_attachment_path('  "D:\\Resume\\cv.pdf"  ') == Path(r"D:\Resume\cv.pdf")

    def test_mismatched_quotes_are_not_stripped(self):
        # A leading quote makes the path relative, so normal repo-root
        # resolution applies; only matching pairs are unwrapped.
        assert resolve_attachment_path(r'"D:\Resume\cv.pdf') == ROOT_DIR / r'"D:\Resume\cv.pdf'

    def test_interior_quotes_are_preserved(self):
        assert resolve_attachment_path(r'D:\Resume\my "final" cv.pdf') == Path(r'D:\Resume\my "final" cv.pdf')

    def test_relative_path_resolves_against_repo_root(self):
        assert resolve_attachment_path("data/resume.pdf") == ROOT_DIR / "data/resume.pdf"

    def test_resolved_existing_file(self, tmp_path):
        target = tmp_path / "cv.pdf"
        target.write_bytes(b"%PDF-1.4 test")
        resolved = resolve_attachment_path(f'"{target}"')
        assert resolved is not None
        assert resolved == target
        assert resolved.exists()


class TestNormalizeAttachmentRef:
    def test_none_stays_none(self):
        assert normalize_attachment_ref(None) is None

    def test_quoted_ref_is_unwrapped(self):
        assert normalize_attachment_ref('"D:\\Resume\\cv.pdf"') == r"D:\Resume\cv.pdf"

    def test_plain_ref_is_only_stripped(self):
        assert normalize_attachment_ref("  data/resume.pdf  ") == "data/resume.pdf"

    def test_entity_normalizes_on_construction(self):
        tmpl = MessageTemplate.create(
            name="t",
            channel=Channel.WHATSAPP,
            body="hi",
            template_id="tmpl_norm_1",
            attachment_ref='"D:\\Resume\\cv.pdf"',
        )
        assert tmpl.attachment_ref == r"D:\Resume\cv.pdf"


class TestTemplateSaveWarnsOnUnresolvableAttachment:
    def test_save_warns_but_persists(self, db_session):
        from unittest.mock import patch

        from app.infrastructure.repositories import sqlite_template_repository as repo_module

        repo = SqliteTemplateRepository(db_session)
        with patch.object(repo_module.logger, "warning") as warn_mock:
            repo.save(
                MessageTemplate.create(
                    name="t",
                    channel=Channel.WHATSAPP,
                    body="hi",
                    template_id="tmpl_warn_1",
                    attachment_ref="/nonexistent-dir/missing.pdf",
                )
            )
        assert warn_mock.call_count == 1
        assert warn_mock.call_args.args[1] == "tmpl_warn_1"
        assert repo.get_by_id("tmpl_warn_1") is not None
