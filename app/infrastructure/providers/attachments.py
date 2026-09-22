"""Attachment path resolution shared by the messaging providers.

Attachment references may be stored as absolute paths or as paths relative to
the repository root. Resolving them here (rather than against the process
working directory) keeps behaviour stable regardless of how the app is launched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import ROOT_DIR
from app.domain.message_template import normalize_attachment_ref


def resolve_attachment_path(ref: Optional[str]) -> Optional[Path]:
    """Resolve an attachment reference to an absolute path.

    Returns the resolved path whether or not it exists, or ``None`` when no
    reference was supplied. Callers decide how to handle a missing file.
    """
    text = normalize_attachment_ref(ref)
    if not text:
        return None
    # Template refs are user-supplied and sometimes arrive wrapped in literal
    # quotes (e.g. pasted from a spreadsheet cell as `"D:\\Resume\\cv.pdf"`).
    # Normalization strips one pair of matching surrounding quotes so the
    # path can resolve instead of failing every dispatch.
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path
