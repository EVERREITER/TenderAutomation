"""Read complete Excel source structure without scanning a rectangular grid.

read_excel(path) streams sparse OOXML and uses openpyxl for shared strings and
styles, retaining cell contents, formulas, caches, comments, merges, visibility,
and validation rules. resolve_list() resolves supported static option sources;
bounds() and contains() support address checks. Empty styled cells are compressed
into ranges. The reader never evaluates formulas or saves the source workbook.
"""
import json
import posixpath
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from openpyxl import load_workbook
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


def read_excel(path):
    wb = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    result = {"kind": "excel", "sheets": [], "defined_names": [], "styles": {}, "limitations": [], "date_epoch": str(wb.epoch)}
    try:
        with ZipFile(path) as z:
            book = ET.fromstring(z.read("xl/workbook.xml"))
            rels = {x.get("Id"): x.get("Target") for x in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
            for x in book.findall("s:definedNames/s:definedName", NS):
                result["defined_names"].append({**x.attrib, "formula": x.text})
            for index, item in enumerate(book.findall("s:sheets/s:sheet", NS)):
                target = rels[item.get(R)]
                part = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/"+target)
                ws = wb[item.get("name")]
                s = {"name": ws.title, "state": item.get("state", "visible"), "part": part, "cells": {}, "blank_ranges": [], "row_ranges": [], "columns": [], "merges": [], "validations": [], "protected": False, "raw_structure": []}
                blank_runs, row_runs = {}, {}
                used_styles = {0}
                with z.open(part) as stream:
                    for _, el in ET.iterparse(stream, events=("end",)):
                        tag = el.tag.split("}")[-1]
                        if tag == "row":
                            row = int(el.get("r"))
                            attrs = {k:v for k,v in el.attrib.items() if k not in ("r", "spans")}
                            if attrs:
                                _runs_add(row_runs, json.dumps(attrs, sort_keys=True), row, row)
                            for c in el.findall("s:c", NS):
                                address, style = c.get("r"), int(c.get("s", "0"))
                                used_styles.add(style)
                                value, raw = _value(c, ws._shared_strings)
                                f = c.find("s:f", NS)
                                if value is None and f is None:
                                    rr, cc = coordinate_to_tuple(address)
                                    _runs_add(blank_runs, (cc, style), rr, rr)
                                else:
                                    s["cells"][address] = {"value": value if f is None else None, "data_type": c.get("t", "n"), "raw_value": raw, "style": style,
                                        "formula": None if f is None else {"text": f.text, "attributes": f.attrib}, "cached_value": value if f is not None else None}
                            el.clear()
                        elif tag == "mergeCell":
                            s["merges"].append({"range": el.get("ref"), "anchor": el.get("ref").split(":")[0]})
                        elif tag == "dataValidation":
                            s["validations"].append({**el.attrib, "formula1": el.findtext("s:formula1", namespaces=NS), "formula2": el.findtext("s:formula2", namespaces=NS)})
                        elif tag == "col":
                            s["columns"].append(el.attrib.copy())
                        elif tag == "sheetProtection":
                            s["protected"] = el.get("sheet", "0") in ("1", "true")
                        elif tag in ("dimension", "sheetViews", "sheetFormatPr", "autoFilter", "tableParts", "extLst", "controls", "oleObjects", "drawing", "legacyDrawing"):
                            s["raw_structure"].append(ET.tostring(el, encoding="unicode"))
                            if tag in ("extLst", "controls", "oleObjects", "drawing"):
                                result["limitations"].append(f"{ws.title}: {tag} nicht semantisch aufgelöst; Rohstruktur erhalten")
                for (col, style), runs in blank_runs.items():
                    letter = get_column_letter(col)
                    s["blank_ranges"].extend({"range": f"{letter}{a}:{letter}{b}", "style": style} for a,b in runs)
                for attrs, runs in row_runs.items():
                    s["row_ranges"].extend({"start":a,"end":b,"attributes":json.loads(attrs)} for a,b in runs)
                # Comments reside in related package parts, not in the cell XML.
                relpart = posixpath.dirname(part)+"/_rels/"+posixpath.basename(part)+".rels"
                s["comments"] = {}
                if relpart in z.namelist():
                    for rel in ET.fromstring(z.read(relpart)):
                        if rel.get("TargetMode") == "External":
                            result["limitations"].append(f"Externe Referenz nicht geladen: {rel.get('Target')}")
                        elif rel.get("Type", "").endswith("/comments"):
                            t = rel.get("Target")
                            cp = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.dirname(part)+"/"+t)
                            cr = ET.fromstring(z.read(cp))
                            authors = [a.text for a in cr.findall("s:authors/s:author", NS)]
                            for c in cr.findall("s:commentList/s:comment", NS):
                                s["comments"][c.get("ref")] = {"text":"".join(t.text or "" for t in c.findall(".//s:t", NS)), "author":authors[int(c.get("authorId", "0"))]}
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
