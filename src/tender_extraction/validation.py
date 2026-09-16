"""Check extraction claims against source evidence and enrich final results.

validate() returns technical checks for references, source quotations, fields,
options, document structure, and shared targets. enrich() adds deterministic
keys and nests answer fields and options in the final Result. Supporting entry
points include canonical_address(), covered(), and ocr_polygon_to_pdf() for
address normalization, sparse Excel coverage, and supported OCR transformations.
These checks do not prove semantic correctness or complete question discovery.
"""
import hashlib
import json
from collections import Counter
from .schemas import Check, Result, FinalQuestion, FinalField
from .services.openai_client import canonical
from .readers.excel import bounds, contains


def norm(text):
    return " ".join(str(text).split())


def ocr_polygon_to_pdf(polygon, ocr_page, pdf_page):
    """DI top-left inches -> unrotated MediaBox top-left points.

    No guessed scaling for pixels, unusual UserUnit, crop/dimension mismatch.
    DI's text angle is not a page-coordinate rotation and is not reapplied.
    """
    if ocr_page.get("unit") != "inch" or pdf_page.get("user_unit",1) != 1:
        return None
    w,h = pdf_page["width"],pdf_page["height"]
    rotation = pdf_page.get("rotation",0) % 360
    if rotation not in (0,90,180,270): return None
    dw,dh = (h,w) if rotation in (90,270) else (w,h)
    if abs(ocr_page.get("width",0)*72-dw)>2 or abs(ocr_page.get("height",0)*72-dh)>2: return None
    if not polygon or len(polygon)%2: return None
    points=[]
    for i in range(0,len(polygon),2):
        x,y = polygon[i]*72,polygon[i+1]*72
        if rotation==90: x,y = y,h-x
        elif rotation==180: x,y = w-x,h-y
        elif rotation==270: x,y = w-y,x
        points.append((x,y))
    return [min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)]


def key(*parts):
    return hashlib.sha256(canonical(parts).encode()).hexdigest()


def covered(address, ranges):
    """Check union coverage without enumerating potentially millions of cells."""
    target = bounds(address)
    if not target: return False
    left,top,right,bottom = target
    rects = [b for r in ranges if (b := bounds(r)) and b[0]<=right and b[2]>=left and b[1]<=bottom and b[3]>=top]
    breaks = sorted({left,right+1} | {max(left,b[0]) for b in rects} | {min(right+1,b[2]+1) for b in rects})
    for x in breaks[:-1]:
        intervals = sorted((max(top,b[1]),min(bottom,b[3])) for b in rects if b[0]<=x<=b[2])
        next_row = top
        for start,end in intervals:
            if start > next_row: return False
            next_row = max(next_row,end+1)
        if next_row<=bottom: return False
    return True


def canonical_address(address, manifest):
    a = address.model_dump() if hasattr(address, "model_dump") else dict(address)
    if a.get("cell_range"):
        a["cell_range"] = a["cell_range"].replace("$", "").upper()
        if ":" in a["cell_range"]:
            start,end = a["cell_range"].split(":")
            if start == end: a["cell_range"] = start
        sheet = next((s for s in manifest.get("sheets",[]) if s["name"] == a.get("sheet")),{})
        for merge in sheet.get("merges",[]):
            if contains(merge["range"],a["cell_range"]): a["cell_range"] = merge["anchor"]
    return a


def validate(candidate, manifest, di=None):
    checks = []
    def add(subject, code, outcome, observed, claim, reason):
        checks.append(Check(subject=subject,code=code,result=outcome,observed=str(observed),claim=str(claim),reason=reason))
    groups = {"question":candidate.questions,"field":candidate.answer_fields,"option":candidate.options,"source":candidate.source_references,"position":candidate.positions}
    ids = {kind:{x.id for x in items} for kind,items in groups.items()}
    for kind, items in groups.items():
        for ident,n in Counter(x.id for x in items).items():
            if n > 1: add(ident,"duplicate_local_id","failed",n,kind,"Lokale IDs müssen je Typ eindeutig sein")
    refs = {r.id:r for r in candidate.source_references}
    for kind,items in groups.items():
        for item in items:
            for ref in getattr(item,"source_ids",[]):
                if ref not in ids["source"]: add(item.id,"reference_integrity","failed",ref,"source_id","Quelle fehlt")
    for f in candidate.answer_fields:
        if f.question_id not in ids["question"]: add(f.id,"reference_integrity","failed",f.question_id,"question_id","Frage fehlt")
    for o in candidate.options:
        if o.field_id not in ids["field"]: add(o.id,"reference_integrity","failed",o.field_id,"field_id","Feld fehlt")
    for a in candidate.attributes:
        valid_owner = a.owner_id == "document" if a.owner_type == "document" else a.owner_id in ids[a.owner_type]
        if not valid_owner or any(x not in refs for x in a.source_ids): add(a.owner_id,"attribute_reference","failed",a.source_ids,a.name,"Attributzugehörigkeit oder Quellen ungültig")
        if a.value is not None and not a.source_ids: add(a.owner_id,"attribute_evidence","not_verifiable","Keine Quelle",a.name,"Attribut nicht belegt")
    if manifest["kind"] != "excel":
        actual = {p["number"] for p in manifest["pages"]}
        ocr_pages = (di or {}).get("pages",[])
        seen = [p.get("pageNumber",p.get("page_number")) for p in ocr_pages]
        add("document","ocr_page_coverage","passed" if set(seen)==actual and len(seen)==len(actual) else "failed",seen,sorted(actual),"Alle PDF-Seiten müssen genau einmal von OCR erfasst sein")

    def source_check(subject, address, quote, target=False):
        if manifest["kind"] == "excel":
            s = next((s for s in manifest["sheets"] if s["name"] == address.sheet),None)
            b = bounds(address.cell_range or "")
            if address.document != "original" or address.page is not None or address.word_path is not None or not s or not b:
                add(subject,"excel_address","failed", "Blatt/Adresse/Dateiversion ungültig",address,"Keine gültige Excel-Fundstelle")
                return
            cells = [(a,c) for a,c in s["cells"].items() if contains(address.cell_range,a)]
            known_ranges = [r["range"] for r in s["blank_ranges"]]+[r["range"] for r in s["merges"]]+[v for r in s["validations"] for v in r.get("sqref","").split()]
            observed = covered(address.cell_range,known_ranges+list(s["cells"])+list(s.get("comments",{})))
            add(subject,"excel_address","passed" if observed else "not_verifiable",observed,address.cell_range,"Strukturmanifest, kein ws.cell()-Existenztest; keine semantische Zielbestätigung")
            for merge in s["merges"]:
                if contains(merge["range"],address.cell_range):
                    add(subject,"merge_anchor","passed" if address.cell_range.replace("$","") in (merge["anchor"],merge["range"]) else "failed",merge,address.cell_range,"Verbundene Zellen verwenden den Anker")
            if not target:
                texts = []
                for a,c in cells:
                    texts.extend([c.get("value"),c.get("cached_value"), (c.get("formula") or {}).get("text"),s.get("comments",{}).get(a,{}).get("text")])
                texts.append(s.get("comments",{}).get(address.cell_range,{}).get("text"))
                text = "\n".join(str(t) for t in texts if t is not None)
                ok = bool(norm(quote)) and norm(quote) in norm(text)
                add(subject,"source_quote","passed" if ok else "failed",text,quote,"Exakter Teiltext nach reiner Leerraum-Normalisierung; keine unscharfe Bestätigung")
            if target:
                styles = [c["style"] for _,c in cells]+[r["style"] for r in s["blank_ranges"] if contains(r["range"],address.cell_range)]
                if any(c.get("formula") for _,c in cells): add(subject,"formula_target","failed","Formelzelle",address.cell_range,"Formeln nicht überschreiben")
                if s["protected"] and any(manifest["styles"][str(st)]["locked"] for st in styles): add(subject,"protected_target","failed","Aktiver Blattschutz + locked",address.cell_range,"Geschütztes Ziel")
            return
        # Native Word positions are only in the original document, never PDF points.
        if address.word_path is not None:
            item = next((x for x in manifest.get("word_index",[]) if x["path"] == address.word_path),None)
            valid = address.document == "original" and item is not None and address.page is None and address.bbox is None
            add(subject,"word_structure","passed" if valid else "failed",item,address,"Exakter OOXML-Pfad einschließlich Tabellen/Steuerelementen")
            if valid and not target:
                add(subject,"source_quote","passed" if norm(quote) and norm(quote) in norm(item["text"]) else "failed",item["text"],quote,"Leerraum-normalisierter Originaltext")
            if valid:
                duplicates = [x for x in manifest["word_index"] if x["kind"] == item["kind"] and norm(x["text"]) == norm(item["text"])]
                if not item["text"].strip() or len(duplicates)>1:
                    add(subject,"word_pdf_mapping","not_verifiable",len(duplicates),address.word_path,"Leere/doppelte Texte erlauben keinen eindeutigen visuellen Rückbezug")
            return
        expected_doc = "rendered_pdf" if "word_index" in manifest else "original"
        page = next((p for p in manifest["pages"] if p["number"] == address.page),None)
        valid = address.document == expected_doc and page is not None and address.sheet is None and address.cell_range is None
        add(subject,"pdf_page","passed" if valid else "failed",expected_doc,address,"Seite 1-basiert und an tatsächliche PDF-Version gebunden")
        if not valid: return
        op = next((p for p in (di or {}).get("pages",[]) if p.get("pageNumber",p.get("page_number")) == address.page),{})
        ocr_text = "\n".join(x.get("content","") for x in op.get("lines",[]))
        if not target:
            method = "native" if norm(quote) and norm(quote) in norm(page["text"]) else "ocr" if norm(quote) and norm(quote) in norm(ocr_text) else None
            add(subject,"source_quote","passed" if method else "not_verifiable",{"method":method,"native":page["text"],"ocr":ocr_text},quote,"Exakter Leerraumvergleich; OCR-Abweichungen bleiben unbestätigt")
        if address.bbox is not None:
            b = address.bbox
            inside = len(b)==4 and 0 <= b[0] < b[2] <= page["width"] and 0 <= b[1] < b[3] <= page["height"]
            add(subject,"pdf_bounds","passed" if inside else "failed",[page["width"],page["height"]],b,"Unrotierte MediaBox-Punkte; lediglich Bereichsgrenzen geprüft")
            if inside and not target:
                transformed = [(line.get("content",""),ocr_polygon_to_pdf(line.get("polygon"),op,page)) for line in op.get("lines",[])]
                within = [text for text,r in transformed if r and b[0]-2<=r[0] and b[1]-2<=r[1] and b[2]+2>=r[2] and b[3]+2>=r[3]]
                supported = any(r for _,r in transformed)
                match = bool(norm(quote)) and norm(quote) in norm("\n".join(within))
                add(subject,"pdf_region_quote","passed" if match else "failed" if supported else "not_verifiable",transformed,quote,"OCR-Inch ×72, inverse Seitenrotation; MediaBox-Maßprüfung, 2-Punkt-Toleranz; keine Pixel-Schätzung")
            add(subject,"pdf_precise_location","not_verifiable",op.get("unit"),b,"Modellkoordinaten sind keine präzise Schreibposition; OCR-Rotation/Einheiten werden nicht gleichgesetzt")
        if address.form_field:
            form = manifest["form_fields"].get(address.form_field)
            add(subject,"pdf_form_field","passed" if form else "failed",form,address.form_field,"Native Formularstruktur; keine erfundenen Felder")
            if form and form.get("pages"):
                add(subject,"pdf_form_page","passed" if address.page in form["pages"] else "failed",form["pages"],address.page,"Formularwidget muss auf der behaupteten Seite liegen")
        if target and "word_index" in manifest:
            add(subject,"word_pdf_mapping","not_verifiable","Nur Word-PDF-Adresse",address,"Originaladresse ungeklärt; PDF-Koordinaten sind keine Word-Schreibposition")

    for ref in candidate.source_references:
        source_check(ref.id,ref.address,ref.quote)
    for q in candidate.questions:
        if not q.source_ids: add(q.id,"missing_source","failed",[],q.original,"Frage benötigt eine Quelle")
        if any(x not in ids["position"] for x in q.position_ids) or any(x not in refs for x in q.context_source_ids): add(q.id,"reference_integrity","failed",q.position_ids,q.context_source_ids,"Kontext/Positionsverweis fehlt")
        if q.context_status == "unknown" or not q.context_source_ids: add(q.id,"unknown_context","not_verifiable",q.context_source_ids,q.context_status,"Kontext ist keine bestätigte Stammdatenzuordnung")
        if q.confidence is not None and not 0 <= q.confidence <= 1: add(q.id,"confidence_range","failed",q.confidence,"0..1","Unkalibrierte Modellangabe außerhalb Wertebereich")
        if not any(f.question_id == q.id for f in candidate.answer_fields): add(q.id,"missing_target","not_verifiable","Antwortziel nicht vorhanden",None,"Frage bleibt erhalten")
    for f in candidate.answer_fields:
        if f.target_status != "located" or f.address is None:
            add(f.id,"missing_target","not_verifiable",f.target_status,f.address,"Antwortziel nicht vorhanden" if f.target_status == "not_present" else "Antwortziel ungeklärt")
            if f.target_status == "located" or f.address is not None: add(f.id,"target_consistency","failed",f.address,f.target_status,"Zielstatus und Adresse widersprechen sich")
            continue
        source_check(f.id,f.address,"",target=True)
        options = sorted([o for o in candidate.options if o.field_id == f.id],key=lambda o:o.order)
        if manifest["kind"] == "excel":
            s = next((s for s in manifest["sheets"] if s["name"] == f.address.sheet),{})
            rules = [r for r in s.get("validations",[]) if any(contains(rg,f.address.cell_range or "") for rg in r.get("sqref","").split())]
            lists = [r for r in rules if r.get("type") == "list"]
            if f.control_type == "excel_dropdown": add(f.id,"dropdown_type","passed" if lists else "failed",rules,f.control_type,"Nur echte list-Datavalidierung belegt Dropdown")
            if lists:
                expected = lists[0]["options"]
                values = [o.value for o in options]
                add(f.id,"dropdown_options","not_verifiable" if expected is None else "passed" if canonical(values)==canonical(expected) else "failed",expected,values,"Exakte typisierte Originalwerte und Reihenfolge; dynamische Regeln bleiben unbekannt")
            elif options:
                add(f.id,"printed_options","not_verifiable",None,[o.value for o in options],"Gedruckte Auswahl ist kein echtes Dropdown; Quellen separat geprüft")
            for a in [a for a in candidate.attributes if a.owner_type == "field" and a.owner_id == f.id]:
                if a.name in ("minimum","maximum","max_length","validation_rule"):
                    matches = []
                    for r in rules:
                        if a.name == "validation_rule": matches.append(str(a.value) in (r.get("formula1"),r.get("formula2"),canonical({k:v for k,v in r.items() if k not in ("options","option_sources")})))
                        elif r.get("type") in ("whole","decimal","date","time","textLength"):
                            raw = r.get("formula2") if a.name in ("maximum","max_length") and r.get("operator","between") == "between" else r.get("formula1")
                            permitted = (a.name == "minimum" and r.get("operator","between") in ("between","greaterThanOrEqual")) or (a.name == "maximum" and r.get("operator","between") in ("between","lessThanOrEqual")) or (a.name == "max_length" and r.get("type") == "textLength" and r.get("operator","between") in ("between","lessThanOrEqual"))
                            try:
                                if permitted: matches.append(float(raw)==float(a.value))
                            except (ValueError,TypeError): pass
                    add(f.id,"validation_rule","passed" if any(matches) else "failed" if matches else "not_verifiable",rules,a.model_dump(),"Originalregeln erhalten; dynamische Formeln nicht ausgewertet; Datumsserien verwenden die Workbook-Epoche")
        elif f.address.form_field:
            form = manifest["form_fields"].get(f.address.form_field)
            if form:
                expected_type = {"/Tx":"pdf_text","/Ch":"pdf_choice","/Btn":"pdf_button"}.get(form["type"],"unknown")
                add(f.id,"pdf_control_type","passed" if f.control_type == expected_type else "failed",expected_type,f.control_type,"Nativer Formularfeldtyp")
                if form["options"]:
                    expected = [v[0] if isinstance(v,list) else v for v in form["options"]]
                    add(f.id,"pdf_options","passed" if [o.value for o in options]==expected else "failed",expected,[o.value for o in options],"Native Exportwerte")
        if f.control_type == "word_control" and f.address.word_path:
            control = next((x for x in manifest.get("word_index",[]) if x["path"]==f.address.word_path),{})
            add(f.id,"word_control","passed" if control.get("control_ids") else "failed",control.get("control_ids"),f.control_type,"Inhaltssteuerelement-ID muss in der Originalstruktur vorkommen")
    for i,f in enumerate(candidate.answer_fields):
        if not f.address: continue
        for g in candidate.answer_fields[i+1:]:
            if not g.address: continue
            a,b = canonical_address(f.address,manifest),canonical_address(g.address,manifest)
            shared = a==b
            if a.get("cell_range") and b.get("cell_range") and a["sheet"]==b["sheet"]:
                x,y = bounds(a["cell_range"]),bounds(b["cell_range"])
                shared = bool(x and y and max(x[0],y[0])<=min(x[2],y[2]) and max(x[1],y[1])<=min(x[3],y[3]))
            if shared:
                for item,other in ((f,g),(g,f)): add(item.id,"shared_physical_target","not_verifiable",other.id,item.address,"Mehrfachbelegung: vor späterem Schreiben fachlich auflösen")
    # Copy source outcomes onto owning question/field/option for per-object reports.
    source_checks = [c for c in checks if c.subject in refs]
    for kind,items in groups.items():
        if kind == "source": continue
        for item in items:
            for check in source_checks:
                if check.subject in getattr(item,"source_ids",[]): checks.append(check.model_copy(update={"subject":item.id}))
    return checks


def enrich(candidate, manifest, document, run, checks):
    refs = {r.id:r for r in candidate.source_references}
    positions = {p.id:p for p in candidate.positions}
    def locations(ids):
        return sorted([canonical_address(refs[x].address,manifest) for x in ids if x in refs],key=canonical)
    def attrs(kind,ident): return [a for a in candidate.attributes if a.owner_type==kind and a.owner_id==ident]
    questions, keys = [], []
    for q in candidate.questions:
        qa = attrs("question",q.id)
        context_names = {"product","lot","supplier","company","site","period","row_context","column_context"}
        context = {"state":q.context_status,"values":sorted([(a.name,a.value,locations(a.source_ids)) for a in qa if a.name in context_names],key=canonical),
            "sources":locations(q.context_source_ids),"positions":sorted([{k:v for k,v in positions[x].model_dump().items() if k not in ("id","source_ids")} for x in q.position_ids if x in positions],key=canonical)}
        qkey = key(document["family"],locations(q.source_ids),context,q.subquestion)
        keys.append((q.id,qkey))
        fields = []
        for f in candidate.answer_fields:
            if f.question_id != q.id: continue
            fkey = key(qkey,canonical_address(f.address,manifest) if f.address else None,f.role,locations(f.source_ids))
            keys.append((f.id,fkey))
            options = []
            for o in sorted([o for o in candidate.options if o.field_id==f.id],key=lambda x:x.order):
                okey = key(fkey,o.value,locations(o.source_ids))
                keys.append((o.id,okey))
                options.append({**o.model_dump(),"key":okey})
            fields.append(FinalField(**f.model_dump(),key=fkey,options=options,attributes=attrs("field",f.id),checks=[c for c in checks if c.subject==f.id]))
        questions.append(FinalQuestion(**q.model_dump(),key=qkey,answer_fields=fields,attributes=qa,checks=[c for c in checks if c.subject==q.id]))
    counts = Counter(k for _,k in keys)
    for ident,k in keys:
        if counts[k]>1: checks.append(Check(subject=ident,code="key_collision",result="failed",observed=k,claim=ident,reason="Identische kanonische Identität; keine textbasierte Deduplizierung"))
    for q in questions:
        q.checks = [c for c in checks if c.subject==q.id]
        for f in q.answer_fields: f.checks = [c for c in checks if c.subject==f.id]
    return Result(schema_version="1",document=document,run=run,questions=questions,positions=candidate.positions,source_references=candidate.source_references,
        document_metadata=attrs("document","document"),limitations=candidate.limitations+manifest.get("limitations",[]))
