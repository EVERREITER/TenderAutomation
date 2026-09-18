"""Validate Excel evidence and enrich questions with stable keys and nested fields."""
import hashlib
import json
import re
from difflib import SequenceMatcher
from collections import Counter
from . import SCHEMA_VERSION
from .schemas import Check, Result, FinalQuestion, FinalField
from .services.openai_client import canonical
from .readers.excel import bounds, contains


def norm(text):
    # Decode Excel whitespace escapes once; escaped literal underscores stay literal.
    text = re.sub(r"_x([0-9a-fA-F]{4})_", lambda m:
        chr(int(m[1], 16)) if int(m[1], 16) in (9, 10, 13) else m[0], str(text))
    return " ".join(text.split())


def text_outcome(observed, claim, *, substring=False):
    """Exact normalized evidence, a narrowly bounded typo, or a real mismatch.

    Never fuzzy-accept numbers, missing words, negations, or short units.
    Minor spelling differences remain warnings, not verified quotations.
    """
    source, quoted = norm(observed), norm(claim)
    if quoted and (quoted in source if substring else quoted == source):
        return "passed"
    left, right = source.split(), quoted.split()
    if not quoted or len(left) != len(right):
        return "failed"
    differences = [(a,b) for a,b in zip(left,right) if a != b]
    if not 1 <= len(differences) <= 2:
        return "failed"
    for a,b in differences:
        if min(len(a),len(b)) < 8 or a[:4] != b[:4] or not a.rstrip('.,;:()').isalpha() or not b.rstrip('.,;:()').isalpha():
            return "failed"
        edits = sum(max(j-i,l-k) for op,i,j,k,l in SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op != 'equal')
        if edits > 1:
            return "failed"
    return "not_verifiable"


def validation_status(checks):
    """Only definite, material errors block technical completion."""
    return "needs_review" if any(c.severity == "error" for c in checks) else "completed"


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


def validate(candidate, manifest):
    if manifest["kind"] != "excel":
        raise ValueError("Excel manifest required")
    checks = []
    def add(subject, code, outcome, observed, claim, reason):
        advisory = code in {"merge_anchor", "protected_target", "confidence_range"}
        severity = "error" if outcome == "failed" and not advisory else "warning" if outcome in ("failed", "not_verifiable") else "info"
        checks.append(Check(subject=subject,code=code,result=outcome,severity=severity,observed=str(observed),claim=str(claim),reason=reason))
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
    def source_check(subject, address, quote, target=False):
        s = next((s for s in manifest["sheets"] if s["name"] == address.sheet),None)
        if s and address.document == "original" and address.cell_range is None and not target:
            add(subject,"sheet_name_quote","passed" if quote == s["name"] else "failed",
                s["name"],quote,"Blattname als exakter Kontextbeleg, kein Zellinhalt")
            return
        b = bounds(address.cell_range or "")
        if address.document != "original" or not s or not b:
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
            add(subject,"source_quote",text_outcome(text,quote,substring=True),text,quote,
                "Excel-Zeilenumbrüche normalisiert; minimale Schreibabweichungen sind Hinweise, keine bestätigten Zitate")
        if target:
            styles = [c["style"] for _,c in cells]+[r["style"] for r in s["blank_ranges"] if contains(r["range"],address.cell_range)]
            if any(c.get("formula") for _,c in cells): add(subject,"formula_target","failed","Formelzelle",address.cell_range,"Formeln nicht überschreiben")
            if s["protected"] and any(manifest["styles"][str(st)]["locked"] for st in styles): add(subject,"protected_target","failed","Aktiver Blattschutz + locked",address.cell_range,"Geschütztes Ziel")
        return
    for ref in candidate.source_references:
        source_check(ref.id,ref.address,ref.quote)
    for q in candidate.questions:
        if not q.source_ids: add(q.id,"missing_source","failed",[],q.original,"Frage benötigt eine Quelle")
        if any(sid not in refs for sid in q.note_source_ids):
            add(q.id,"reference_integrity","failed",q.note_source_ids,"note_source_ids","Hinweisquelle fehlt")
        if q.notes or q.note_source_ids:
            quoted = "\n".join(refs[sid].quote for sid in q.note_source_ids if sid in refs)
            outcome = text_outcome(quoted,q.notes) if q.notes and q.note_source_ids else "failed"
            add(q.id,"notes_evidence",outcome,quoted,q.notes,
                "Vollständige Hinweistexte; minimale Schreibabweichungen bleiben Hinweise für den Human Review")
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
        s = next((s for s in manifest["sheets"] if s["name"] == f.address.sheet),{})
        rules = [r for r in s.get("validations",[]) if any(contains(rg,f.address.cell_range or "") for rg in r.get("sqref","").split())]
        lists = [r for r in rules if r.get("type") == "list"]
        if f.control_type == "excel_dropdown": add(f.id,"dropdown_type","passed" if lists else "failed",rules,f.control_type,"Nur echte list-Datavalidierung belegt Dropdown")
        if lists:
            expected = lists[0]["options"]
            values = [o.value for o in options]
            nonempty = lambda items: [x for x in items if x is not None and not (isinstance(x,str) and not x.strip())]
            outcome = "not_verifiable"
            if expected is not None:
                actual_items, expected_items = nonempty(values), nonempty(expected)
                outcome = "passed" if canonical(actual_items)==canonical(expected_items) else "not_verifiable" if Counter(map(canonical,actual_items))==Counter(map(canonical,expected_items)) else "failed"
            add(f.id,"dropdown_options",outcome,expected,values,
                "Leere Listenzellen ignoriert; fehlende oder veränderte nichtleere Werte sind Fehler, reine Reihenfolgeabweichungen Hinweise")
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
                source_ids = getattr(item,"source_ids",[]) + getattr(item,"note_source_ids",[]) + getattr(item,"context_source_ids",[])
                if check.subject in source_ids: checks.append(check.model_copy(update={"subject":item.id}))
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
        if counts[k]>1: checks.append(Check(subject=ident,code="key_collision",result="failed",severity="error",observed=k,claim=ident,reason="Identische kanonische Identität; keine textbasierte Deduplizierung"))
    for q in questions:
        q.checks = [c for c in checks if c.subject==q.id]
        for f in q.answer_fields: f.checks = [c for c in checks if c.subject==f.id]
    return Result(schema_version=SCHEMA_VERSION,document=document,run=run,questions=questions,positions=candidate.positions,source_references=candidate.source_references,
        document_metadata=attrs("document","document"),limitations=candidate.limitations+manifest.get("limitations",[]))
