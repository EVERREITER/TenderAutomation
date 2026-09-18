"""Erstellt die ausgewählten Dataverse-Tabellen in TenderAutomation."""

import os
from dataclasses import dataclass

import requests
from azure.identity import InteractiveBrowserCredential

SOLUTION = "TenderAutomation"
LEBENSZYKLUS = ["Entwurf", "geplant", "aktiv", "ruhend", "eingestellt"]
ANTWORTTYPEN = ["Text", "Zahl", "Datum", "Ja/Nein", "Einfachauswahl", "Mehrfachauswahl", "Dokument"]
FREIGABESTATUS = ["Entwurf", "In Prüfung", "Freigegeben", "Abgelehnt", "Abgelöst", "Zurückgezogen"]


@dataclass
class Dataverse:
    session: requests.Session
    api_url: str
    prefix: str = ""

    def request(self, method: str, path: str, **kwargs) -> dict:
        response = self.session.request(
            method, f"{self.api_url}/{path}", timeout=60, **kwargs
        )
        if not response.ok:
            raise RuntimeError(
                f"Dataverse {method} {path}: HTTP {response.status_code}\n{response.text}"
            )
        return response.json() if response.content else {}


def connect_dataverse() -> Dataverse:
    """Browser-Anmeldung und Publisher-Präfix der Solution."""
    url = os.environ.get("DATAVERSE_URL", "").strip().rstrip("/")
    if not url:
        url = input("Dataverse-URL (https://...crm4.dynamics.com): ").strip().rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("Bitte eine Dataverse-URL mit https:// angeben.")
    options = {}
    for variable, option in [("AZURE_TENANT_ID", "tenant_id"), ("AZURE_CLIENT_ID", "client_id")]:
        if os.environ.get(variable):
            options[option] = os.environ[variable]
    print("Bitte im Browser mit deinem Dataverse-Konto anmelden.")
    with InteractiveBrowserCredential(**options) as credential:
        token = credential.get_token(f"{url}/.default").token
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "MSCRM.SolutionUniqueName": SOLUTION,
    })
    db = Dataverse(session, f"{url}/api/data/v9.2")
    try:
        solutions = db.request("GET", "solutions", params={
            "$select": "solutionid",
            "$filter": f"uniquename eq '{SOLUTION}'",
            "$expand": "publisherid($select=customizationprefix)",
        })["value"]
        if not solutions:
            raise RuntimeError(f"Solution {SOLUTION!r} wurde nicht gefunden.")
        db.prefix = solutions[0]["publisherid"]["customizationprefix"]
        return db
    except Exception:
        session.close()
        raise


def label(text: str) -> dict:
    return {"LocalizedLabels": [{"Label": text, "LanguageCode": 1031}]}


def text_column(db: Dataverse, name: str, primary: bool = False, *,
                title: str | None = None, required: bool = False) -> dict:
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title or name),
        "RequiredLevel": {"Value": "ApplicationRequired" if required else "None"},
        "MaxLength": 200,
        "FormatName": {"Value": "Text"},
        "IsPrimaryName": primary,
    }


def choice_column(db: Dataverse, name: str, title: str, choices: list[str], *,
                  required: bool = False) -> dict:
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title),
        "RequiredLevel": {"Value": "ApplicationRequired" if required else "None"},
        "OptionSet": {
            "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
            "IsGlobal": False,
            "OptionSetType": "Picklist",
            "Options": [
                {"Value": value, "Label": label(text)}
                for value, text in enumerate(choices, start=100000000)
            ],
        },
    }


def integer_column(db: Dataverse, name: str, title: str, *, required: bool = False) -> dict:
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.IntegerAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title),
        "RequiredLevel": {"Value": "ApplicationRequired" if required else "None"},
        "Format": "None",
        "MinValue": 0,
        "MaxValue": 2147483647,
    }


def ensure_table(db: Dataverse, name: str, plural: str, columns: list[dict],
                 display_name: str | None = None) -> str:
    """Ergänzt fehlende Tabellen/Spalten und benennt den GUID-Schlüssel als ID."""
    logical_name = f"{db.prefix}_{name}".lower()
    path = f"EntityDefinitions(LogicalName='{logical_name}')"
    existing = db.request("GET", "EntityDefinitions", params={
        "$select": "LogicalName", "$filter": f"LogicalName eq '{logical_name}'",
    })["value"]
    if not existing:
        db.request("POST", "EntityDefinitions", json={
            "@odata.type": "Microsoft.Dynamics.CRM.EntityMetadata",
            "SchemaName": f"{db.prefix}_{name}",
            "DisplayName": label(display_name or name),
            "DisplayCollectionName": label(plural),
            "OwnershipType": "OrganizationOwned",
            "IsActivity": False,
            "HasActivities": False,
            "HasNotes": False,
            "Attributes": columns,
        })
    else:
        attributes = db.request("GET", f"{path}/Attributes", params={
            "$select": "LogicalName",
        })["value"]
        names = {attribute["LogicalName"] for attribute in attributes}
        for column in columns:
            if column["SchemaName"].lower() not in names:
                db.request("POST", f"{path}/Attributes", json=column)
    primary_id = db.request("GET", path, params={
        "$select": "PrimaryIdAttribute",
    })["PrimaryIdAttribute"]
    attribute_path = f"{path}/Attributes(LogicalName='{primary_id}')"
    definition = db.request(
        "GET", f"{attribute_path}/Microsoft.Dynamics.CRM.UniqueIdentifierAttributeMetadata"
    )
    definition.pop("@odata.context", None)
    definition["DisplayName"] = label("ID")
    db.request("PUT", attribute_path, json=definition, headers={
        "MSCRM.MergeLabels": "true", "If-None-Match": "null",
    })
    print(f"{name}: Tabelle und Spalten vorhanden, Primärschlüssel heißt ID.")
    return logical_name


def add_produktgruppe(db: Dataverse) -> str:
    """PRODUKTGRUPPE: ID (GUID-Primärschlüssel), name (Text)."""
    return ensure_table(db, "PRODUKTGRUPPE", "PRODUKTGRUPPEN", [
        text_column(db, "name", primary=True),
    ])


def add_produkt(db: Dataverse) -> str:
    """PRODUKT mit Textfeldern, Lebenszyklus und optionaler Produktgruppe."""
    produktgruppe = f"{db.prefix}_produktgruppe".lower()
    gruppen = db.request("GET", "EntityDefinitions", params={
        "$select": "PrimaryIdAttribute",
        "$filter": f"LogicalName eq '{produktgruppe}'",
    })["value"]
    if not gruppen:
        raise RuntimeError(
            "PRODUKTGRUPPE fehlt in Dataverse. Bitte add_produktgruppe(db) "
            "einmal vor add_produkt(db) ausfuehren, damit der Lookup angelegt werden kann."
        )
    primary_id = gruppen[0]["PrimaryIdAttribute"]
    lebenszyklus = choice_column(db, "lebenszyklus", "Lebenszyklus", LEBENSZYKLUS)
    produkt = ensure_table(db, "PRODUKT", "PRODUKTE", [
        text_column(db, "name", primary=True),
        text_column(db, "produktnummer"),
        text_column(db, "wirkstoff"),
        lebenszyklus,
    ])
    relationship_name = f"{db.prefix}_produktgruppe_produkt"
    relationships = db.request("GET", "RelationshipDefinitions", params={
        "$select": "SchemaName",
        "$filter": f"SchemaName eq '{relationship_name}'",
    })["value"]
    if not relationships:
        db.request("POST", "RelationshipDefinitions", json={
            "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
            "SchemaName": relationship_name,
            "ReferencedEntity": produktgruppe,
            "ReferencedAttribute": primary_id,
            "ReferencingEntity": produkt,
            "Lookup": {
                "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
                "SchemaName": f"{db.prefix}_produktgruppeid",
                "DisplayName": label("Produktgruppe ID"),
                "RequiredLevel": {"Value": "None"},
            },
            "CascadeConfiguration": {
                "Assign": "NoCascade", "Delete": "RemoveLink",
                "Merge": "NoCascade", "Reparent": "NoCascade",
                "Share": "NoCascade", "Unshare": "NoCascade",
            },
        })
    return produkt


def add_praesentation(db: Dataverse) -> str:
    """Präsentation mit Produkt-Lookup und Lebenszyklus als Einfachauswahl."""
    produkt = f"{db.prefix}_produkt".lower()
    products = db.request("GET", "EntityDefinitions", params={
        "$select": "PrimaryIdAttribute", "$filter": f"LogicalName eq '{produkt}'",
    })["value"]
    if not products:
        raise RuntimeError("PRODUKT fehlt. Bitte zuerst add_produkt(db) ausführen.")
    praesentation = ensure_table(db, "PRAESENTATION", "Präsentationen", [
        text_column(db, "artikelnummer", primary=True),
        text_column(db, "staerke"),
        text_column(db, "darreichungsform"),
        text_column(db, "behaeltnistyp"),
        text_column(db, "behaeltnismaterial"),
        integer_column(db, "behaeltnisvolumen", "behaeltnisvolumen"),
        choice_column(db, "volumeneinheit", "volumeneinheit", ["ml", "L"]),
        text_column(db, "behaeltnisbeschreibung"),
        integer_column(db, "packungsgroesse", "Packungsgröße"),
        choice_column(db, "lebenszyklus", "Lebenszyklus", LEBENSZYKLUS),
    ], display_name="Präsentation")
    relationship_name = f"{db.prefix}_produkt_praesentation"
    existing = db.request("GET", "RelationshipDefinitions", params={
        "$select": "SchemaName", "$filter": f"SchemaName eq '{relationship_name}'",
    })["value"]
    if not existing:
        db.request("POST", "RelationshipDefinitions", json={
            "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
            "SchemaName": relationship_name,
            "ReferencedEntity": produkt,
            "ReferencedAttribute": products[0]["PrimaryIdAttribute"],
            "ReferencingEntity": praesentation,
            "Lookup": {
                "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
                "SchemaName": f"{db.prefix}_produktid",
                "DisplayName": label("ProduktID"),
                "RequiredLevel": {"Value": "None"},
            },
            "CascadeConfiguration": {
                "Assign": "NoCascade", "Delete": "RemoveLink",
                "Merge": "NoCascade", "Reparent": "NoCascade",
                "Share": "NoCascade", "Unshare": "NoCascade",
            },
        })
    return praesentation


def date_column(db: Dataverse, name: str, title: str) -> dict:
    """Optionales Datum ohne Uhrzeit und Zeitzonenverschiebung."""
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title),
        "RequiredLevel": {"Value": "None"},
        "Format": "DateOnly",
        "DateTimeBehavior": {"Value": "DateOnly"},
    }


def memo_column(db: Dataverse, name: str, title: str, *, required: bool = False) -> dict:
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.MemoAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title),
        "RequiredLevel": {"Value": "ApplicationRequired" if required else "None"},
        "Format": "Text",
        "MaxLength": 100000,
    }


def boolean_column(db: Dataverse, name: str, title: str) -> dict:
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.BooleanAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title),
        "RequiredLevel": {"Value": "ApplicationRequired"},
        "DefaultValue": False,
        "OptionSet": {
            "@odata.type": "Microsoft.Dynamics.CRM.BooleanOptionSetMetadata",
            "OptionSetType": "Boolean",
            "TrueOption": {"Value": 1, "Label": label("Ja")},
            "FalseOption": {"Value": 0, "Label": label("Nein")},
        },
    }


def decimal_column(db: Dataverse, name: str, title: str) -> dict:
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.DecimalAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title),
        "RequiredLevel": {"Value": "None"},
        "Precision": 10,
        "MinValue": -100000000000,
        "MaxValue": 100000000000,
    }


def datetime_column(db: Dataverse, name: str, title: str, *, required: bool = False) -> dict:
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
        "SchemaName": f"{db.prefix}_{name}",
        "DisplayName": label(title),
        "RequiredLevel": {"Value": "ApplicationRequired" if required else "None"},
        "Format": "DateAndTime",
        "DateTimeBehavior": {"Value": "UserLocal"},
    }


def ensure_lookup(db: Dataverse, source: str, target: str, name: str,
                  title: str, *, required: bool = False) -> None:
    """Legt einen Lookup auf eine bestehende Tabelle an, auch auf sich selbst."""
    target_name = "systemuser" if target == "systemuser" else f"{db.prefix}_{target}".lower()
    targets = db.request("GET", "EntityDefinitions", params={
        "$select": "PrimaryIdAttribute", "$filter": f"LogicalName eq '{target_name}'",
    })["value"]
    if not targets:
        if target == "systemuser":
            raise RuntimeError("Die Dataverse-Systemtabelle systemuser ist nicht verfügbar.")
        raise RuntimeError(
            f"Lookup {title}: Zieltabelle {target_name} fehlt. "
            f"Bitte zuerst add_{target.lower()}(db) ausführen."
        )
    relationship_name = f"{source}_{name}"
    existing = db.request("GET", "RelationshipDefinitions", params={
        "$select": "SchemaName", "$filter": f"SchemaName eq '{relationship_name}'",
    })["value"]
    if existing:
        return
    db.request("POST", "RelationshipDefinitions", json={
        "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
        "SchemaName": relationship_name,
        "ReferencedEntity": target_name,
        "ReferencedAttribute": targets[0]["PrimaryIdAttribute"],
        "ReferencingEntity": source,
        "Lookup": {
            "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
            "SchemaName": f"{db.prefix}_{name}",
            "DisplayName": label(title),
            "RequiredLevel": {"Value": "ApplicationRequired" if required else "None"},
        },
        "CascadeConfiguration": {
            "Assign": "NoCascade", "Delete": "Restrict" if required else "RemoveLink",
            "Merge": "NoCascade", "Reparent": "NoCascade",
            "Share": "NoCascade", "Unshare": "NoCascade",
        },
    })


def add_organisation(db: Dataverse) -> str:
    """Organisation mit optionalem Verweis auf ihre Mutterorganisation."""
    organisation = ensure_table(db, "ORGANISATION", "Organisationen", [
        text_column(db, "name", primary=True, title="Name", required=True),
        text_column(db, "organisationsnummer", title="Organisationsnummer", required=True),
        text_column(db, "land", title="Land", required=True),
        text_column(db, "rollen", title="Rollen", required=True),
        choice_column(db, "lebenszyklusstatus", "Lebenszyklusstatus",
                      ["Entwurf", "Aktiv", "Ruhend", "Beendet"], required=True),
    ], display_name="Organisation")
    ensure_lookup(db, organisation, "organisation", "mutterorganisationid", "Mutterorganisation")
    return organisation


def add_standort(db: Dataverse) -> str:
    """Standort mit zugehöriger Organisation."""
    standort = ensure_table(db, "STANDORT", "Standorte", [
        text_column(db, "name", primary=True, title="Name", required=True),
        text_column(db, "strasseundhausnummer", title="Straße und Hausnummer", required=True),
        text_column(db, "postleitzahl", title="Postleitzahl"),
        text_column(db, "ort", title="Ort", required=True),
        text_column(db, "land", title="Land", required=True),
        text_column(db, "standorttyp", title="Standorttyp", required=True),
        choice_column(db, "lebenszyklusstatus", "Lebenszyklusstatus",
                      ["Entwurf", "Geplant", "Aktiv", "Ruhend", "Geschlossen"], required=True),
    ], display_name="Standort")
    ensure_lookup(db, standort, "organisation", "organisationid", "Organisation", required=True)
    return standort


def add_produktbeziehung(db: Dataverse) -> str:
    """Beziehung einer Produktpräsentation zu Organisation und optional Standort."""
    beziehung = ensure_table(db, "PRODUKTBEZIEHUNG", "Produktbeziehungen", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        integer_column(db, "version", "Version", required=True),
        text_column(db, "rolle", title="Rolle", required=True),
        text_column(db, "prozessstufe", title="Prozessstufe"),
        text_column(db, "markt", title="Markt"),
        date_column(db, "gueltigab", "Gültig ab"),
        date_column(db, "gueltigbis", "Gültig bis"),
        choice_column(db, "beziehungsstatus", "Beziehungsstatus",
                      ["Entwurf", "Geplant", "Aktiv", "Ausgesetzt", "Beendet"], required=True),
    ], display_name="Produktbeziehung")
    ensure_lookup(db, beziehung, "praesentation", "produktpraesentationid",
                  "Produktpräsentation", required=True)
    ensure_lookup(db, beziehung, "organisation", "organisationid", "Organisation", required=True)
    ensure_lookup(db, beziehung, "standort", "standortid", "Standort")
    return beziehung


def add_geltungsbereich(db: Dataverse) -> str:
    """Geltungsbereich mit optionalen Verweisen auf Produkte und Organisationen."""
    bereich = ensure_table(db, "GELTUNGSBEREICH", "Geltungsbereiche", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        text_column(db, "markt", title="Markt"),
        text_column(db, "akteursrolle", title="Akteursrolle"),
        text_column(db, "prozessstufe", title="Prozessstufe"),
        text_column(db, "bauteilbezug", title="Bauteilbezug"),
        choice_column(db, "anwendungsregel", "Anwendungsregel",
                      ["Exakter Bezug", "Freigegebene Unterbereiche", "Einzelfallprüfung"],
                      required=True),
    ], display_name="Geltungsbereich")
    ensure_lookup(db, bereich, "produktgruppe", "produktgruppeid", "Produktgruppe")
    ensure_lookup(db, bereich, "produkt", "produktid", "Produkt")
    ensure_lookup(db, bereich, "praesentation", "produktpraesentationid", "Produktpräsentation")
    ensure_lookup(db, bereich, "organisation", "gesellschaftid", "Gesellschaft")
    ensure_lookup(db, bereich, "organisation", "partnerorganisationid", "Partnerorganisation")
    ensure_lookup(db, bereich, "standort", "standortid", "Standort")
    return bereich


def add_tender(db: Dataverse) -> str:
    return ensure_table(db, "TENDER", "Tender", [
        text_column(db, "name", primary=True, title="Name", required=True),
        text_column(db, "kunde", title="Kunde", required=True),
        text_column(db, "markt", title="Markt", required=True),
        datetime_column(db, "abgabefrist", "Abgabefrist"),
        choice_column(db, "bearbeitungsstatus", "Bearbeitungsstatus",
                      ["Entwurf", "In Bearbeitung", "In Prüfung", "Bereit zur Abgabe",
                       "Abgegeben", "Abgebrochen"], required=True),
    ], display_name="Tender")


def add_standardfrage(db: Dataverse) -> str:
    return ensure_table(db, "STANDARDFRAGE", "Standardfragen", [
        text_column(db, "kurzbezeichnung", primary=True, title="Kurzbezeichnung", required=True),
        memo_column(db, "fragetext", "Fragetext", required=True),
        text_column(db, "kategorie", title="Kategorie", required=True),
        choice_column(db, "zielfeldtyp", "Zielfeldtyp", ANTWORTTYPEN, required=True),
        boolean_column(db, "aktiv", "Aktiv"),
    ], display_name="Standardfrage")


def add_informationstyp(db: Dataverse) -> str:
    return ensure_table(db, "INFORMATIONSTYP", "Informationstypen", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        memo_column(db, "fachdefinition", "Fachdefinition", required=True),
        choice_column(db, "datentyp", "Datentyp",
                      ["Text", "Zahl", "Datum", "Ja/Nein", "Einfachauswahl"], required=True),
        memo_column(db, "erlaubtewertejson", "Erlaubte Werte JSON"),
        text_column(db, "kategorie", title="Kategorie", required=True),
    ], display_name="Informationstyp")


def add_fragebedarf(db: Dataverse) -> str:
    table = ensure_table(db, "FRAGEBEDARF", "Fragebedarfe", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        text_column(db, "erwarteteakteursrolle", title="Erwartete Akteursrolle"),
        text_column(db, "erwarteterbezug", title="Erwarteter Bezug"),
        boolean_column(db, "erforderlich", "Erforderlich"),
    ], display_name="Fragebedarf")
    ensure_lookup(db, table, "standardfrage", "standardfrageid", "Standardfrage", required=True)
    ensure_lookup(db, table, "informationstyp", "informationstypid", "Informationstyp", required=True)
    return table


def add_tenderfrage(db: Dataverse) -> str:
    table = ensure_table(db, "TENDERFRAGE", "Tenderfragen", [
        text_column(db, "kurzbezeichnung", primary=True, title="Kurzbezeichnung", required=True),
        memo_column(db, "originaltext", "Originaltext", required=True),
        text_column(db, "fragennummer", title="Fragennummer"),
        memo_column(db, "fundstelle", "Fundstelle", required=True),
        choice_column(db, "antworttyp", "Antworttyp", ANTWORTTYPEN, required=True),
        choice_column(db, "bearbeitungsstatus", "Bearbeitungsstatus",
                      ["Neu", "In Bearbeitung", "Klärung erforderlich", "Antwort vorgeschlagen",
                       "In Prüfung", "Abgeschlossen"], required=True),
    ], display_name="Tenderfrage")
    ensure_lookup(db, table, "tender", "tenderid", "Tender", required=True)
    ensure_lookup(db, table, "standardfrage", "standardfrageid", "Standardfrage")
    ensure_lookup(db, table, "geltungsbereich", "geltungsbereichid", "Geltungsbereich")
    return table


def add_antwort(db: Dataverse) -> str:
    table = ensure_table(db, "ANTWORT", "Antworten", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        text_column(db, "antwortnummer", title="Antwortnummer", required=True),
        integer_column(db, "version", "Version", required=True),
        memo_column(db, "antworttext", "Antworttext", required=True),
        text_column(db, "sprache", title="Sprache", required=True),
        choice_column(db, "freigabestatus", "Freigabestatus", FREIGABESTATUS, required=True),
        date_column(db, "gueltigab", "Gültig ab"),
        date_column(db, "gueltigbis", "Gültig bis"),
    ], display_name="Antwort")
    ensure_lookup(db, table, "standardfrage", "standardfrageid", "Standardfrage", required=True)
    ensure_lookup(db, table, "geltungsbereich", "geltungsbereichid", "Geltungsbereich", required=True)
    return table


def add_information(db: Dataverse) -> str:
    table = ensure_table(db, "INFORMATION", "Informationen", [
        text_column(db, "titel", primary=True, title="Titel", required=True),
        text_column(db, "informationsnummer", title="Informationsnummer", required=True),
        integer_column(db, "version", "Version", required=True),
        choice_column(db, "wertstatus", "Wertstatus",
                      ["Vorhanden", "Unbekannt", "Nicht anwendbar"], required=True),
        memo_column(db, "werttext", "Wert Text"),
        decimal_column(db, "wertzahl", "Wert Zahl"),
        date_column(db, "wertdatum", "Wert Datum"),
        choice_column(db, "wertjanein", "Wert Ja/Nein", ["Ja", "Nein"]),
        text_column(db, "wertauswahl", title="Wert Auswahl"),
        text_column(db, "einheit", title="Einheit"),
        memo_column(db, "bedingungen", "Bedingungen"),
        date_column(db, "berichtsperiodevon", "Berichtsperiode von"),
        date_column(db, "berichtsperiodebis", "Berichtsperiode bis"),
        text_column(db, "methode", title="Methode"),
        text_column(db, "methodenversion", title="Methodenversion"),
        memo_column(db, "systemgrenze", "Systemgrenze"),
        text_column(db, "bezugsgroesse", title="Bezugsgröße"),
        memo_column(db, "datenqualitaetsbeschreibung", "Datenqualitätsbeschreibung"),
        choice_column(db, "freigabestatus", "Freigabestatus", FREIGABESTATUS, required=True),
        date_column(db, "gueltigab", "Gültig ab"),
        date_column(db, "gueltigbis", "Gültig bis"),
        date_column(db, "naechstesreviewdatum", "Nächstes Reviewdatum"),
        memo_column(db, "nachweisausnahmebegruendung", "Nachweisausnahmebegründung"),
    ], display_name="Information")
    ensure_lookup(db, table, "informationstyp", "informationstypid", "Informationstyp", required=True)
    ensure_lookup(db, table, "geltungsbereich", "geltungsbereichid", "Geltungsbereich", required=True)
    ensure_lookup(db, table, "systemuser", "verantwortlicherid", "Verantwortlicher", required=True)
    return table


def add_freigabe(db: Dataverse) -> str:
    table = ensure_table(db, "FREIGABE", "Freigaben", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        choice_column(db, "entscheidung", "Entscheidung",
                      ["Freigegeben", "Abgelehnt", "Überarbeitung erforderlich"], required=True),
        datetime_column(db, "geprueftam", "Geprüft am", required=True),
        memo_column(db, "kommentar", "Kommentar"),
    ], display_name="Freigabe")
    ensure_lookup(db, table, "information", "informationid", "Information")
    ensure_lookup(db, table, "antwort", "antwortid", "Antwort")
    ensure_lookup(db, table, "systemuser", "prueferid", "Prüfer", required=True)
    return table


def add_antwortinformation(db: Dataverse) -> str:
    table = ensure_table(db, "ANTWORTINFORMATION", "Antwortinformationen", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        choice_column(db, "verwendungsart", "Verwendungsart",
                      ["Direkte Grundlage", "Berechnungsgrundlage", "Kontext"], required=True),
        memo_column(db, "belegterantwortteil", "Belegter Antwortteil", required=True),
    ], display_name="Antwortinformation")
    ensure_lookup(db, table, "antwort", "antwortid", "Antwort", required=True)
    ensure_lookup(db, table, "information", "informationid", "Information", required=True)
    return table


def add_antwortbasis(db: Dataverse) -> str:
    table = ensure_table(db, "ANTWORTBASIS", "Antwortbasen", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        choice_column(db, "verwendungsart", "Verwendungsart",
                      ["Übernommen", "Angepasst", "Übersetzt", "Zusammengeführt"], required=True),
        memo_column(db, "verwendeterantwortteil", "Verwendeter Antwortteil", required=True),
    ], display_name="Antwortbasis")
    ensure_lookup(db, table, "antwort", "zielantwortid", "Zielantwort", required=True)
    ensure_lookup(db, table, "antwort", "quellantwortid", "Quellantwort", required=True)
    return table


def add_dokument(db: Dataverse) -> str:
    """Dokumentversion mit URL, Freigabestatus und optionalen Gültigkeitsdaten."""
    versionslink = text_column(db, "versionslink", title="Versionslink", required=True)
    versionslink["FormatName"] = {"Value": "Url"}
    versionslink["MaxLength"] = 4000
    return ensure_table(db, "DOKUMENT", "Dokumente", [
        integer_column(db, "version", "Version", required=True),
        text_column(db, "titel", primary=True, title="Titel", required=True),
        text_column(db, "dokumenttyp", title="Dokumenttyp", required=True),
        versionslink,
        choice_column(db, "freigabestatus", "Freigabestatus", FREIGABESTATUS, required=True),
        date_column(db, "gueltigab", "Gültig ab"),
        date_column(db, "gueltigbis", "Gültig bis"),
        date_column(db, "naechstesreviewdatum", "Nächstes Reviewdatum"),
    ], display_name="Dokument")


def add_dokumentbezug(db: Dataverse) -> str:
    """Verknüpft ein Dokument mit einem Geltungsbereich."""
    table = ensure_table(db, "DOKUMENTBEZUG", "Dokumentbezüge", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        memo_column(db, "kommentar", "Kommentar"),
    ], display_name="Dokumentbezug")
    ensure_lookup(db, table, "dokument", "dokumentid", "Dokument", required=True)
    ensure_lookup(db, table, "geltungsbereich", "geltungsbereichid", "Geltungsbereich", required=True)
    return table


def add_informationsnachweis(db: Dataverse) -> str:
    """Belegt eine Information durch Dokument, Fundstelle und Aussage."""
    table = ensure_table(db, "INFORMATIONSNACHWEIS", "Informationsnachweise", [
        text_column(db, "bezeichnung", primary=True, title="Bezeichnung", required=True),
        text_column(db, "fundstelle", title="Fundstelle", required=True),
        memo_column(db, "belegteaussage", "Belegte Aussage", required=True),
    ], display_name="Informationsnachweis")
    ensure_lookup(db, table, "information", "informationid", "Information", required=True)
    ensure_lookup(db, table, "dokument", "dokumentid", "Dokument", required=True)
    return table


def main() -> None:
    db = connect_dataverse()
    try:
        tables = []
        # Einzelne Zeilen nach Bedarf auskommentieren.
        #tables.append(add_produktgruppe(db))
        #tables.append(add_produkt(db))
        #tables.append(add_praesentation(db))
        #tables.append(add_organisation(db))
        #tables.append(add_standort(db))
        #tables.append(add_produktbeziehung(db))
        #tables.append(add_geltungsbereich(db))
        #tables.append(add_tender(db))
        #tables.append(add_standardfrage(db))
        #tables.append(add_informationstyp(db))
        #tables.append(add_fragebedarf(db))
        #tables.append(add_tenderfrage(db))
        #tables.append(add_antwort(db))
        #tables.append(add_information(db))
        #tables.append(add_freigabe(db))
        #tables.append(add_antwortinformation(db))
        #tables.append(add_antwortbasis(db))
        tables.append(add_dokument(db))
        tables.append(add_dokumentbezug(db))
        tables.append(add_informationsnachweis(db))
        if tables:
            entities = "".join(f"<entity>{name}</entity>" for name in tables)
            db.request("POST", "PublishXml", json={
                "ParameterXml": f"<importexportxml><entities>{entities}</entities></importexportxml>",
            })
            print(f"Fertig: {', '.join(tables)} in {SOLUTION} veröffentlicht.")
    finally:
        db.session.close()


if __name__ == "__main__":
    main()
