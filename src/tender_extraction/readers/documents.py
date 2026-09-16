"""Prepare native PDF evidence and local Word document representations.

read_pdf(path) returns page metadata, text, and native form-field information.
word_index(path) indexes Word paragraphs, table cells, and content controls by
OOXML path. convert_word(path, run_dir, converter, timeout) runs LibreOffice with
an isolated temporary profile, validates the resulting PDF, and retains a copy
for the run. Original documents are never overwritten.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from pypdf import PdfReader
from ..errors import ExtractionError

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def word_index(path):
    items = []
    with ZipFile(path) as z:
        for part in z.namelist():
            if not (part.startswith("word/") and part.endswith(".xml")):
                continue
            root = ET.fromstring(z.read(part))
            def walk(el, xpath):
                tag = el.tag.split("}")[-1]
                if el.tag in (W+"p", W+"tc", W+"sdt"):
                    items.append({"path":part+":"+xpath, "kind":tag,
                        "text":"".join((x.text or "") if x.tag == W+"t" else "\n" if x.tag == W+"br" else "\t" for x in el.iter() if x.tag in (W+"t",W+"br",W+"tab")),
                        "control_ids":[x.get(W+"val") for x in el.findall("./"+W+"sdtPr/"+W+"id")]})
                counts = {}
                for child in el:
                    ct = child.tag.split("}")[-1]
                    counts[ct] = counts.get(ct,0)+1
                    walk(child, xpath+f"/{ct}[{counts[ct]}]")
            walk(root, "/"+root.tag.split("}")[-1]+"[1]")
    return items


def read_pdf(path):
    try:
        r = PdfReader(path)
        if r.is_encrypted:
            raise ValueError("Verschlüsselte PDF")
        pages = [{"number":i+1, "width":float(p.mediabox.width), "height":float(p.mediabox.height), "media_origin":[float(p.mediabox.left),float(p.mediabox.bottom)], "rotation":int(p.get("/Rotate",0)), "user_unit":float(p.get("/UserUnit",1)), "text":p.extract_text() or ""} for i,p in enumerate(r.pages)]
        if not pages:
            raise ValueError("Keine Seiten")
        fields = {name:{"type":str(f.get("/FT")),"options":[list(v) if isinstance(v,list) else str(v) for v in f.get("/Opt",[])],"value":str(f.get("/V")) if f.get("/V") is not None else None} for name,f in (r.get_fields() or {}).items()}
        for number,p in enumerate(r.pages,1):
            for reference in p.get("/Annots",[]):
                widget = reference.get_object()
                current,parts = widget,[]
                visited = set()
                while current is not None and id(current) not in visited:
                    visited.add(id(current))
                    if current.get("/T") is not None: parts.insert(0,str(current["/T"]))
                    parent = current.get("/Parent")
                    current = parent.get_object() if parent else None
                field = fields.get(".".join(parts))
                if field is None: continue
                field.setdefault("pages",[])
                if number not in field["pages"]: field["pages"].append(number)
                if field["type"] == "/Btn":
                    appearance = widget.get("/AP")
                    normal = appearance.get_object().get("/N") if appearance else None
                    normal = normal.get_object() if normal else None
                    if isinstance(normal,dict) and not hasattr(normal,"get_data"):
                        for state in normal:
                            if str(state) not in field["options"]: field["options"].append(str(state))
        return {"kind":"pdf", "pages":pages, "form_fields":fields, "limitations":[], "coordinate_system":"1-based pages; unrotated media-box, top-left, PDF points; model bbox never a validated writing position"}
    except Exception as e:
        raise ExtractionError("invalid_pdf", "PDF nicht lesbar oder verschlüsselt") from e


def convert_word(path, run_dir, converter, timeout):
    exe = shutil.which(converter)
    if not exe:
        raise ExtractionError("converter_missing", "LibreOffice installieren und LIBREOFFICE_PATH auf soffice.exe setzen")
    with tempfile.TemporaryDirectory(prefix="tender-word-") as temp:
        t = Path(temp)
        # Process only a local copy; isolate profile and conversion output.
        copy = t/"input.docx"
        shutil.copyfile(path, copy)
        try:
            completed = subprocess.run([exe, "-env:UserInstallation="+(t/"profile").as_uri(), "--headless", "--convert-to", "pdf", "--outdir", str(t), str(copy)], capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise ExtractionError("conversion_timeout", "Word-Konvertierung überschritt Timeout") from e
        pdf = t/"input.pdf"
        if completed.returncode or not pdf.is_file():
            raise ExtractionError("conversion_failed", "LibreOffice hat keine PDF erzeugt")
        read_pdf(pdf)
        dest = run_dir/"rendered.pdf"
        shutil.copyfile(pdf, dest)
    return dest
