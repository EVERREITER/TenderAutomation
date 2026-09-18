# Extraktionsvertrag v3: Excel mit Sol

## Ablauf

Eine `.xlsx` innerhalb `sample_inputs/` wird validiert und in eine temporäre, bytegleiche Momentaufnahme kopiert. SHA-256 bindet den Lauf an die Quelle. Expat liest gespeicherte Zell-XML-Elemente; openpyxl stellt gemeinsame Zeichenketten und Stilmetadaten bereit. Ein Sol-Request erhält das kompakte Manifest der gesamten Arbeitsmappe. Danach erfolgt ausschließlich lokale Quellenvalidierung.

Formeln, Makros und externe Verknüpfungen werden nicht ausgeführt. Zellwerte, Formelattribute und vorhandene Caches bleiben im lokalen Manifest erhalten, fehlende Caches bleiben null. Leere Formatierungszellen werden je Spalte/Stil zu Bereichen zusammengefasst. Zahlenformate, Sichtbarkeit, Kommentare, Merges, benannte Bereiche und Datenvalidierungen bleiben soweit unterstützt erhalten. Reine Leerformatierungen werden nicht an das Modell gesendet.

Statische Dropdownquellen werden auch über Blattgrenzen und lokale/globale Namen aufgelöst. Dynamische/externe Regeln und Optionsbereiche über 100000 Zellen bleiben mit Einschränkung unaufgelöst. Echte Inhaltszellen werden dadurch nicht abgeschnitten. Nicht ausgewertete Zeichnungen, Steuerelemente und Erweiterungen werden als Einschränkungen vermerkt.

## Modellantwort und Endergebnis

Das strenge Wire-Schema enthält flache Listen `questions`, `answer_fields`, `options`, `source_references`, `positions`, `attributes` und `limitations`. Alle Felder sind erforderlich, unbekannte Einzelwerte nullable. Zusätzliche Eigenschaften sind nicht erlaubt. Python erzeugt daraus das finale `Result` mit verschachtelten Antwortfeldern, Optionen, technischen Schlüsseln und Prüfungen.

| Feld | Bedeutung |
|---|---|
| `questions[].original` | Originalwortlaut der Frage oder verlangten Angabe |
| `normalized` | Bereinigter Wortlaut ohne erfundene Ergänzungen |
| `notes` | Antwortanweisungen im Originalwortlaut; mehrere Hinweise durch Zeilenumbrüche verbunden |
| `note_source_ids` | Quellen mit vollständigen Hinweiszitaten; ohne Hinweis leere Liste und `notes=null` |
| `source_ids` | Konkrete Belege für den Fragetext |
| `context_source_ids` | Belege für Produkt-, Los-, Organisations- oder anderen Antwortkontext |
| `position_ids` | Verweise auf `positions`, etwa Medikament, Stärke, Packung und Form |
| `answer_fields` | Zugehörige Antwortziele einschließlich Rollen, Typen, Optionen und Adressen |

Das Modell erkennt die Zuordnung aus dem jeweiligen Layout. Es gibt keine fest codierten Überschriften, Produktnamen oder erwarteten Fragenzahlen. Gleicher Fragetext für verschiedene Produkte bleibt nach tatsächlich getrenntem Antwortkontext getrennt. Gemeinsame Unternehmensfragen werden nicht künstlich vervielfacht.

Adressen besitzen ausschließlich `document="original"`, `sheet` und `cell_range`. Ein Blattname kann als Kontextquelle mit `cell_range=null` und exakt diesem Namen als Zitat belegt werden; Antwortfelder benötigen eine Zelladresse. Schema-Version 3 entfernt die früheren PDF-/Word-Adressfelder. Vorhandene Ergebnisdateien bleiben unverändert und behalten ihre jeweilige Version.

## Lokale Prüfung

Referenzen müssen existieren. Quellenzitate und Anmerkungen werden nach Normalisierung von Leerraum und Excel-Zeilenumbruch-Escapes verglichen. Höchstens zwei lange Wörter mit jeweils einem abweichenden Zeichen können als Schreibhinweis eingestuft werden; sie gelten dann ausdrücklich nicht als verifiziert. Zahlen, kurze Einheiten und ausgelassene Wörter fallen nicht unter diese Toleranz. Zielfelder werden gegen tatsächliche Zellen, komprimierte Leerbereiche, Merges und Datenvalidierungen geprüft.

Dropdownwerte werden typisiert verglichen; leere Einträge entfallen auf beiden Seiten. Null und leere Strings sind leer, 0 und false bleiben echte Werte. Fehlende oder geänderte Werte sind Fehler, reine Reihenfolgeabweichungen Warnungen. Statische numerische Regeln sind prüfbar; dynamische Formeln werden nicht geraten. Formelziele und fehlende Quellen sind Fehler; Zellschutz, Merge-Unterzellen und möglicherweise gemeinsam verwendete Antwortfelder bleiben Hinweise für den Human Review. Semantische Feldzugehörigkeit und vollständige Fragenentdeckung kann Python nicht beweisen.

Frageschlüssel beruhen auf Dokumentfamilie (relativer Eingabepfad), kanonischen Quellenadressen, belegtem Kontext, Produktpositionen und Unterfragenkennung. Modell-IDs und Listenreihenfolge sind keine Identitätsgrundlage. Feld- und Optionsschlüssel berücksichtigen Frage, Ziel, Rolle und exakte Werte. Kollisionen werden gemeldet, nicht still entfernt.

Jede lokale Prüfung hat `severity` (`info`, `warning`, `error`) zusätzlich zu ihrem Prüfergebnis. Die Statusregel `material_errors_only_v1` setzt nur bei `error` den Status `needs_review`. Unklare Kontexte, nicht verifizierbare Ziele, Schreibdetails und allgemeine Einschränkungen blockieren die Extraktion nicht. `completed` bedeutet ohne erkannten schwerwiegenden Fehler abgeschlossen, keine fachliche Freigabe. Technische/API-/Schemafehler sowie leere Extraktionen mit gemeldeten Einschränkungen ergeben weiterhin `failed`. Es gibt keine unabhängige semantische Modellprüfung.

## Artefakte und Limits

Pro Lauf werden nur `sol_questions.json` (vollständiger schemafähiger Kandidat) und `result.json` gespeichert. Bei abgeschnittener Antwort fehlt der Kandidat. Quellenkopie und Strukturmanifest sind temporär. Ein Kandidat ist keine fachliche Freigabe.

Das Ausgabelimit umfasst Reasoning und Antworttext; die Gesamttokenzahl enthält zusätzlich die Eingabe. Laufdaten speichern tatsächlich gesendetes Limit, Reasoning, Timeout, Nutzung, Status und Abbruchgrund. Die Projekt-`.env` wird bei jedem Start frisch gelesen und hat Vorrang vor geerbten Umgebungsvariablen. Fehlt ein Eintrag, gilt die Umgebungsvariable, danach der Standardwert. Ein Terminal-Neustart ist bei ?nderungen an `.env` nicht erforderlich. Defaults: 125000 Ausgabetokens, Reasoning `high`, Timeout 900 Sekunden. Es erfolgt kein automatischer weiterer Modellaufruf zur Reparatur oder Vervollständigung.
