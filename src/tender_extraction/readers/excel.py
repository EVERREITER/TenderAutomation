"""Read complete Excel source structure without scanning a rectangular grid.

read_excel(path) streams stored <c> elements and uses openpyxl for shared strings and
styles, retaining cell contents, formulas, caches, comments, merges, visibility,
and validation rules. resolve_list() resolves supported static option sources;
bounds() and contains() support address checks. Empty styled cells are compressed
into ranges. The reader never evaluates formulas or saves the source workbook.
"""
import json
import os
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import posixpath
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from xml.parsers import expat
from openpyxl import load_workbook
from openpyxl.styles.numbers import BUILTIN_FORMATS
from openpyxl.utils.cell import range_boundaries, coordinate_to_tuple, get_column_letter
from openpyxl.xml.functions import tostring
from ..errors import ExtractionError

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def bounds(address):
    try:
        b = range_boundaries(address.replace("$", "").upper())
        if any(x is None for x in b) or not (1 <= b[0] <= b[2] <= 16384 and 1 <= b[1] <= b[3] <= 1048576):
            return None
        return b
    except (ValueError, TypeError):
        return None


def contains(outer, inner):
    a, b = bounds(outer), bounds(inner)
    return bool(a and b and a[0] <= b[0] <= b[2] <= a[2] and a[1] <= b[1] <= b[3] <= a[3])


def _value(c, strings):
    t = c.get("t", "n")
    v = c.find("s:v", NS)
    raw = None if v is None else v.text
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.findall(".//s:t", NS)), raw
    if raw is None:
        return None, raw
    if t == "s":
        return strings[int(raw)], raw
    if t == "b":
        return raw == "1", raw
    if t == "n":
        try:
            return int(raw) if re.fullmatch(r"[+-]?\d+", raw) else float(raw), raw
        except ValueError:
            pass
    return raw, raw


def _runs_add(runs, key, start, end):
    seq = runs.setdefault(key, [])
    if seq and seq[-1][1] + 1 == start:
        seq[-1][1] = end
    else:
        seq.append([start, end])


def _sheet_elements(stream, blank_runs, row_runs):
    """Yield stored cells and selected metadata with bounded XML-tree memory.

    Expat scans the XML bytes without building a worksheet tree. Only cells with
    child content and selected metadata get small element trees. Empty <c>
    elements become compressed local evidence; no grid or dimension is visited.
    """
    prefix = NS["s"] + "}"
    cell_tag, row_tag = prefix + "c", prefix + "row"
    metadata_tags = {prefix + name for name in ("mergeCell", "dataValidation", "col",
        "sheetProtection", "dimension", "sheetViews", "sheetFormatPr", "autoFilter",
        "tableParts", "extLst", "controls", "oleObjects", "drawing", "legacyDrawing")}
    ready = []
    builder, pending_cell, depth = None, None, 0
    row, previous_address = 0, None

    def expanded(name):
        return "{" + name if "}" in name else name

    def start(name, attrs):
        nonlocal builder, pending_cell, depth, row, previous_address
        if builder is not None:
            builder.start(expanded(name), {expanded(k): v for k, v in attrs.items()})
            depth += 1
        elif pending_cell is not None:
            builder = ET.TreeBuilder()
            builder.start("{" + cell_tag, pending_cell)
            builder.start(expanded(name), attrs)
            pending_cell = None
            depth = 2
        elif name == cell_tag:
            if "r" not in attrs:
                column = coordinate_to_tuple(previous_address)[1] if previous_address else 0
                attrs["r"] = f"{get_column_letter(column + 1)}{row}"
            # Most cells have explicit addresses; only parse column numbers when
            # a subsequent cell actually needs an implicit coordinate.
            pending_cell = attrs
        elif name == row_tag:
            row = int(attrs.get("r", row + 1))
            previous_address = None
            visibility = {k: attrs[k] for k in ("hidden", "outlineLevel", "collapsed") if k in attrs}
            if visibility:
                _runs_add(row_runs, json.dumps(visibility, sort_keys=True), row, row)
        elif name in metadata_tags:
            builder = ET.TreeBuilder()
            builder.start(expanded(name), {expanded(k): v for k, v in attrs.items()})
            depth = 1

    def end(name):
        nonlocal builder, pending_cell, depth, previous_address
        if builder is not None:
            builder.end(expanded(name))
            depth -= 1
            if not depth:
                element = builder.close()
                if name == cell_tag:
                    previous_address = element.get("r")
                ready.append(element)
                builder = None
        elif name == cell_tag and pending_cell is not None:
            address = pending_cell["r"]
            letter = address.rstrip("0123456789")
            cell_row = int(address[len(letter):])
            _runs_add(blank_runs, (letter, int(pending_cell.get("s", "0"))), cell_row, cell_row)
            previous_address = address
            pending_cell = None

    def data(value):
        if builder is not None:
            builder.data(value)

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = data
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.ExternalEntityRefHandler = lambda *args: 0
    while chunk := stream.read(1024 * 1024):
        parser.Parse(chunk, False)
        yield from ready
        ready.clear()
    parser.Parse(b"", True)
    yield from ready


def _read_sheet(task):
    """Read one worksheet in an isolated ZIP handle; never call any model."""
    path, part, name, state, strings = task
    limitations = []
    with ZipFile(path) as z:
        s = {"name": name, "state": state, "part": part, "cells": {}, "blank_ranges": [], "row_ranges": [], "columns": [], "merges": [], "validations": [], "protected": False, "raw_structure": []}
        blank_runs, row_runs = {}, {}
        used_styles = {0}
        with z.open(part) as stream:
            for el in _sheet_elements(stream, blank_runs, row_runs):
                tag = el.tag.split("}")[-1]
                if tag == "c":
                    address, style = el.get("r"), int(el.get("s", "0"))
                    used_styles.add(style)
                    value, raw = _value(el, strings)
                    f = el.find("s:f", NS)
                    if (value is None or isinstance(value, str) and not value.strip()) and f is None:
                        row, _ = coordinate_to_tuple(address)
                        _runs_add(blank_runs, (address.rstrip("0123456789"), style), row, row)
                    else:
                        s["cells"][address] = {"value": value if f is None else None, "data_type": el.get("t", "n"), "raw_value": raw, "style": style,
                            "formula": None if f is None else {"text": f.text, "attributes": dict(f.attrib)}, "cached_value": value if f is not None else None}
                elif tag == "mergeCell":
                    s["merges"].append({"range": el.get("ref"), "anchor": el.get("ref").split(":")[0]})
                elif tag == "dataValidation":
                    s["validations"].append({**el.attrib, "formula1": el.findtext("s:formula1", namespaces=NS), "formula2": el.findtext("s:formula2", namespaces=NS)})
                elif tag == "col":
                    s["columns"].append(dict(el.attrib))
                elif tag == "sheetProtection":
                    s["protected"] = el.get("sheet", "0") in ("1", "true")
                elif tag in ("dimension", "sheetViews", "sheetFormatPr", "autoFilter", "tableParts", "extLst", "controls", "oleObjects", "drawing", "legacyDrawing"):
                    s["raw_structure"].append(ET.tostring(el, encoding="unicode"))
                    if tag in ("extLst", "controls", "oleObjects", "drawing"):
                        limitations.append(f"{name}: {tag} nicht semantisch aufgelöst; Rohstruktur erhalten")
        for (letter, style), runs in blank_runs.items():
            used_styles.add(style)
            s["blank_ranges"].extend({"range": f"{letter}{a}:{letter}{b}", "style": style} for a,b in runs)
        for attrs, runs in row_runs.items():
            s["row_ranges"].extend({"start":a,"end":b,"attributes":json.loads(attrs)} for a,b in runs)
        # Comments reside in related package parts, not in the cell XML.
        relpart = posixpath.dirname(part)+"/_rels/"+posixpath.basename(part)+".rels"
        s["comments"] = {}
        if relpart in z.namelist():
            for rel in ET.fromstring(z.read(relpart)):
                if rel.get("TargetMode") == "External":
                    limitations.append(f"Externe Referenz nicht geladen: {rel.get('Target')}")
                elif rel.get("Type", "").endswith("/comments"):
                    t = rel.get("Target")
                    cp = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.dirname(part)+"/"+t)
                    cr = ET.fromstring(z.read(cp))
                    authors = [a.text for a in cr.findall("s:authors/s:author", NS)]
                    for c in cr.findall("s:commentList/s:comment", NS):
                        s["comments"][c.get("ref")] = {"text":"".join(t.text or "" for t in c.findall(".//s:t", NS)), "author":authors[int(c.get("authorId", "0"))]}
    return s, used_styles, limitations


def read_excel(path, *, workers=None):
    """Stream all sheets; auto-parallelize large XML across at most four CPUs.

    workers=1 forces serial parsing for diagnostics; an explicit larger value
    forces parallel parsing even for small files. Results retain workbook order.
    """
    wb = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    result = {"kind": "excel", "sheets": [], "defined_names": [], "styles": {}, "limitations": [], "date_epoch": str(wb.epoch)}
    try:
        with ZipFile(path) as z:
            book = ET.fromstring(z.read("xl/workbook.xml"))
            rels = {x.get("Id"): x.get("Target") for x in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
            for x in book.findall("s:definedNames/s:definedName", NS):
                result["defined_names"].append({**x.attrib, "formula": x.text})
            tasks = []
            for item in book.findall("s:sheets/s:sheet", NS):
                target = rels[item.get(R)]
                part = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/"+target)
                ws = wb[item.get("name")]
                tasks.append((str(path), part, ws.title, item.get("state", "visible"), ws._shared_strings))
            total_xml_bytes = sum(z.getinfo(task[1]).file_size for task in tasks)
            worker_count = min(len(tasks), max(1, workers if workers is not None else min(4, os.cpu_count() or 1)))
            # Small workbooks are faster without process startup and IPC overhead.
            parallel = worker_count > 1 and (workers is not None or total_xml_bytes >= 64 * 1024 * 1024)
            if parallel:
                with ProcessPoolExecutor(max_workers=worker_count, mp_context=get_context("spawn")) as pool:
                    sheets = list(pool.map(_read_sheet, tasks))
            else:
                sheets = map(_read_sheet, tasks)
            for s, used_styles, limitations in sheets:
                result["limitations"].extend(limitations)
                result["sheets"].append(s)
                for sid in used_styles:
                    st = wb._cell_styles[sid]
                    result["styles"][str(sid)] = {"font":tostring(wb._fonts[st.fontId].to_tree()).decode(), "border":tostring(wb._borders[st.borderId].to_tree()).decode(), "fill":tostring(wb._fills[st.fillId].to_tree()).decode(), "alignment":tostring(wb._alignments[st.alignmentId].to_tree()).decode(), "locked":wb._protections[st.protectionId].locked, "numFmtId":st.numFmtId}
            result["number_formats"] = list(wb._number_formats)
            for p in z.namelist():
                if any(token in p for token in ("externalLinks/", "ctrlProps/", "activeX/", "drawings/")) and p.endswith(".xml"):
                    result.setdefault("unsupported_parts", {})[p] = z.read(p).decode("utf-8", errors="replace")
                    result["limitations"].append(f"Nicht unterstützter/externer Inhalt: {p}")
        for i,s in enumerate(result["sheets"]):
            for rule in s["validations"]:
                rule["options"], rule["option_sources"] = resolve_list(rule, i, result)
                if rule.get("type") == "list" and rule["options"] is None:
                    result["limitations"].append(f"{s['name']} {rule.get('sqref')}: dynamische/mehrdeutige Optionsquelle nicht auflösbar")
        return result
    finally:
        wb.close()


def resolve_list(rule, sheet_index, manifest):
    formula = (rule.get("formula1") or "").lstrip("=")
    if rule.get("type") != "list":
        return None, []
    if formula.startswith('"') and formula.endswith('"'):
        return formula[1:-1].split(","), ["inline:"+formula]
    names = manifest["defined_names"]
    seen = set()
    while True:
        local = [n for n in names if n["name"] == formula and n.get("localSheetId") == str(sheet_index)]
        global_ = [n for n in names if n["name"] == formula and "localSheetId" not in n]
        hits = local or global_
        if not hits:
            break
        if len(hits) != 1 or formula in seen:
            return None, []
        seen.add(formula)
        formula = (hits[0]["formula"] or "").lstrip("=")
    sheet_name = manifest["sheets"][sheet_index]["name"]
    if "!" in formula:
        sheet_name, formula = formula.rsplit("!", 1)
        sheet_name = sheet_name.strip("'").replace("''", "'")
    if "[" in sheet_name or not bounds(formula):
        return None, []
    sheet = next((s for s in manifest["sheets"] if s["name"] == sheet_name), None)
    if sheet is None:
        return None, []
    a,b,c,d = bounds(formula)
    if (c-a+1)*(d-b+1) > 100000:
        return None, [f"{sheet_name}!{formula}: Optionsbereich >100000 Zellen"]
    vals, refs = [], []
    for row in range(b,d+1):
        for col in range(a,c+1):
            addr = f"{get_column_letter(col)}{row}"
            cell = sheet["cells"].get(addr, {})
            if cell.get("formula") and cell.get("cached_value") is None:
                return None, [f"{sheet_name}!{addr}: Formelcache fehlt"]
            vals.append(cell.get("cached_value") if cell.get("formula") else cell.get("value"))
            refs.append(f"{sheet_name}!{addr}")
    return vals, refs


def model_manifest(manifest):
    """Send values and semantic workbook structure, not formatting-only cells.

    All actual content survives regardless of location or layout. The full local
    manifest retains blank-cell evidence and styles for technical validation.
    """
    result = {k: v for k, v in manifest.items()
              if k not in ("sheets", "styles", "unsupported_parts", "number_formats")}
    result["sheets"] = []
    for sheet in manifest["sheets"]:
        item = {k: v for k, v in sheet.items() if k not in
                ("cells", "blank_ranges", "row_ranges", "columns", "raw_structure", "part")}
        item["cells"] = {}
        item["formulas"] = {}
        item["cell_formats"] = {}
        item["cell_types"] = {}
        for address, cell in sheet["cells"].items():
            item["cells"][address] = cell["cached_value"] if cell.get("formula") is not None else cell["value"]
            if cell.get("formula") is not None:
                item["formulas"][address] = cell["formula"]
            if cell.get("data_type") in ("e", "d"):
                item["cell_types"][address] = cell["data_type"]
            fmt_id = manifest["styles"][str(cell["style"])]["numFmtId"]
            custom_formats = manifest.get("number_formats", [])
            fmt = BUILTIN_FORMATS.get(fmt_id)
            if fmt is None and 0 <= fmt_id - 164 < len(custom_formats):
                fmt = custom_formats[fmt_id - 164]
            if fmt and fmt != "General":
                item["cell_formats"][address] = fmt
        item["row_ranges"] = []
        for row in sheet["row_ranges"]:
            attrs = {k: v for k, v in row["attributes"].items() if k in ("hidden", "outlineLevel", "collapsed")}
            if attrs:
                item["row_ranges"].append({"start": row["start"], "end": row["end"], "attributes": attrs})
        item["columns"] = [{k: v for k, v in col.items() if k in ("min", "max", "hidden", "outlineLevel", "collapsed")}
                           for col in sheet["columns"] if any(k in col for k in ("hidden", "outlineLevel", "collapsed"))]
        result["sheets"].append(item)
    result["layout_policy"] = "cells enthält Zellwerte nach A1-Adresse, bei Formeln den Cache oder null; formulas enthält die Formeln. cell_formats erhält Zahlen-/Datumsformate. Rein leere Formatierungszellen und Darstellungs-XML sind ausgelassen, alle echten Inhalte bleiben erhalten."
    return result
