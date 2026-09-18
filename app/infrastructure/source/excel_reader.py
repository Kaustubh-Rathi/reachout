"""Non-destructive source file reader supporting Excel XLSX and CSV formats.

Implements the SourceReader port to parse tabular rows without modifying the
underlying source files.
"""

from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from app.domain.errors import SourceError
from app.domain.source_record import compute_source_fingerprint
from app.ports.source import SourceReader, SourceRow

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": DOC_REL_NS, "pr": PKG_REL_NS}


def clean_text(value: object) -> str:
    """Normalize whitespace and strip leading/trailing spaces."""
    if value is None:
        return ""
    text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def column_name(cell_reference: str) -> str:
    """Extract column letters from an Excel cell reference (e.g. 'AA12' -> 'AA')."""
    match = re.match(r"[A-Z]+", cell_reference.upper())
    return match.group(0) if match else ""


class TabularSourceReader(SourceReader):
    """Implements SourceReader port for .xlsx and .csv files."""

    def read_source(self, source_path: str, sheet_name: Optional[str] = None) -> List[SourceRow]:
        path = Path(source_path).resolve()
        if not path.exists():
            raise SourceError(f"Source file not found: {path}")

        file_ext = path.suffix.lower()
        if file_ext == ".csv":
            return self._read_csv(path)
        elif file_ext in (".xlsx", ".xlsm"):
            return self._read_xlsx(path, sheet_name)
        else:
            raise SourceError(f"Unsupported source file format: {file_ext}")

    def _read_csv(self, path: Path) -> List[SourceRow]:
        rows: List[SourceRow] = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row_idx, raw_dict in enumerate(reader, start=2):
                cleaned_dict = {k.strip(): clean_text(v) for k, v in raw_dict.items() if k}
                fp = compute_source_fingerprint(
                    source_file=path.name,
                    source_sheet=None,
                    source_row=row_idx,
                    raw_payload=cleaned_dict,
                )
                rows.append(
                    SourceRow(
                        source_file=path.name,
                        source_row=row_idx,
                        raw_values=cleaned_dict,
                        source_sheet=None,
                        fingerprint=fp,
                    )
                )
        return rows

    def _read_xlsx(self, path: Path, target_sheet: Optional[str]) -> List[SourceRow]:
        archive = zipfile.ZipFile(path)
        with archive:
            names = set(archive.namelist())
            shared_strings: List[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in root.findall("m:si", NS)
                ]

            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {
                item.attrib["Id"]: item.attrib["Target"] for item in relationships.findall("pr:Relationship", NS)
            }

            sheets = workbook.findall(".//m:sheet", NS)
            sheet = None
            if target_sheet:
                sheet = next(
                    (
                        s
                        for s in sheets
                        if s.attrib.get("name", "").strip().casefold() == target_sheet.strip().casefold()
                    ),
                    None,
                )
            if sheet is None:
                sheet = next(
                    (
                        s
                        for s in sheets
                        if s.attrib.get("name", "").strip().casefold() in ("mnc_cleaned", "mnc", "sheet1")
                    ),
                    sheets[0] if sheets else None,
                )

            if sheet is None:
                raise SourceError(f"No readable sheet found in workbook {path.name}")

            actual_sheet_name = sheet.attrib.get("name", "Sheet1")
            relationship_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
            target = targets[relationship_id].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            worksheet = ET.fromstring(archive.read(target))

            rows: List[SourceRow] = []
            for row_node in worksheet.findall(".//m:sheetData/m:row", NS):
                row_number = int(row_node.attrib.get("r", len(rows) + 1))
                values: Dict[str, str] = {}
                for cell in row_node.findall("m:c", NS):
                    col = column_name(cell.attrib.get("r", ""))
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find("m:v", NS)
                    t_nodes = cell.findall(".//m:t", NS)

                    if t_nodes:
                        val = "".join(node.text or "" for node in t_nodes)
                    elif cell_type == "s" and value_node is not None:
                        val = shared_strings[int(value_node.text or "0")]
                    elif value_node is not None:
                        val = value_node.text or ""
                    else:
                        val = ""
                    values[col] = clean_text(val)

                fp = compute_source_fingerprint(
                    source_file=path.name,
                    source_sheet=actual_sheet_name,
                    source_row=row_number,
                    raw_payload=values,
                )
                rows.append(
                    SourceRow(
                        source_file=path.name,
                        source_sheet=actual_sheet_name,
                        source_row=row_number,
                        raw_values=values,
                        fingerprint=fp,
                    )
                )

        return rows
