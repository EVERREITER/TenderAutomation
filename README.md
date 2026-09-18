# Tenderfragen aus Excel extrahieren

Das lokale Python-Paket verarbeitet eine `.xlsx` aus `sample_inputs/`: XML-Streaming, ein Sol-Aufruf für die gesamte Arbeitsmappe und lokale Quellenprüfung. Es erzeugt keine Antworten und verändert die Eingabedatei nicht. Die separate Azure Function App gehört nicht zu diesem Ablauf.

## Installation und Start

Python 3.10 oder neuer, aus dem Projektverzeichnis:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Eine `.env` im Projektverzeichnis anlegen:

```dotenv
AZURE_OPENAI_BASE_URL=https://YOUR-RESOURCE.openai.azure.com/openai/v1/
AZURE_OPENAI_API_KEY=YOUR-KEY
AZURE_OPENAI_DEPLOYMENT_SOL=YOUR-SOL-DEPLOYMENT
REASONING_SOL_EXTRACTION=high
EXTRACTION_MAX_OUTPUT_TOKENS=125000
OPENAI_TIMEOUT_SECONDS=900
```

Die Projekt-`.env` wird bei jedem Start frisch gelesen und hat Vorrang vor geerbten Umgebungsvariablen. Fehlt ein Eintrag, gilt die Umgebungsvariable, danach der Standardwert. Ein Terminal-Neustart ist bei ?nderungen an `.env` nicht erforderlich. `local.settings.json` wird nicht gelesen. Keine weiteren Modelldeployments, Document Intelligence oder LibreOffice sind nötig.

```powershell
.\.venv\Scripts\python.exe -m tender_extraction --input "sample_inputs/Questions EVER Pharma Norwegian tender.xlsx" --output-dir outputs
```

Dieser Befehl ruft Azure auf. Der Startpunkt ist `src/tender_extraction/__main__.py`, die Argumentverarbeitung liegt in `cli.py`. Optionen:

| Option | Bedeutung |
|---|---|
| `--input` | Erforderlich: eine `.xlsx` innerhalb `sample_inputs/` |
| `--output-dir` | Ausgabeordner, Standard `outputs` |
| `--help` | Hilfe ohne Verarbeitung |

Keine Stapelverarbeitung und keine Unterstützung für `.xls`, PDF oder Word. Die Konsole zeigt vor dem Request Deployment, Reasoning, tatsächlich gesendetes Ausgabelimit und Timeout.

## Verarbeitung und Ergebnisse

Expat liest die gespeicherten `<c>`-Elemente der Worksheet-XMLs. Es gibt keinen rechteckigen Scan über `max_row × max_column` und keinen vollständigen Blattbaum. Leere Formatierungszellen werden zu lokalen Bereichen komprimiert und nicht an Sol gesendet. Ihre XML-Bytes müssen weiterhin aus der ZIP-Datei gelesen werden.

Ab 64 MiB entpacktem Tabellen-XML werden mehrere Blätter mit bis zu vier lokalen Prozessen parallel gelesen. Kleine Arbeitsmappen werden ohne Prozessstart verarbeitet. Die Blattreihenfolge und blattübergreifenden Dropdownquellen bleiben erhalten. Das verändert nicht die Anzahl der Modellanfragen: Es bleibt genau ein Sol-Aufruf. `run.excel_read_seconds` zeigt die Lesezeit getrennt von der Modelllaufzeit.

Alle tatsächlichen Zellinhalte bleiben erhalten, auch weit unten oder rechts und auf versteckten Blättern. Die kompakte Übergabe enthält Zelladressen und Werte, Formeln mit Caches, relevante Zahlenformate, Kommentare, Merges, Sichtbarkeit und Dropdownregeln. Der Parser filtert nicht semantisch nach vermeintlichen Fragen. Feste Spaltenüberschriften, Medikamentnamen, Sprachen oder Soll-Fragenzahlen sind nicht eingebaut.

Sol erhält die gesamte Arbeitsmappe in einem Request. Python prüft anschließend Quellenzitate, Adressen, Verweise, Optionen und weitere strukturell belegbare Angaben. Es gibt keine zweite Modellprüfung oder Eskalation.

Promptversion 6 trennt Auftrag, Feldregeln, Kontextzuordnung und Eingabeformat und enthält ein vollständiges, synthetisches JSON-Beispiel. Die API bekommt weiterhin ein strenges [Structured-Outputs-Schema](https://developers.openai.com/api/docs/guides/structured-outputs); Antworten werden lokal gegen dasselbe Schema geprüft. Das sichert die Datenstruktur, nicht die fachliche Vollständigkeit jeder Modellantwort.

Jeder Lauf erstellt ein eigenes Verzeichnis `outputs/<run_id>/`:

- `sol_questions.json`: vollständige, schemafähige Sol-Antwort vor der lokalen Validierung.
- `result.json`: Endergebnis mit verschachtelten Antwortfeldern, Produktpositionen, Quellen, Prüfungen und Laufdaten.

Bei einer abgeschnittenen oder ungültigen Modellantwort entsteht kein Teilkandidat. `result.json` enthält dann `failed`, den Fehler und keine finalen Fragen. Quelldateikopie und Manifest bleiben temporär; bestehende Ergebnisse werden nicht verändert.

Auch eine technisch abgeschlossene Modellantwort ohne Fragen und mit gemeldeten Einschränkungen gilt als `failed` (`empty_extraction`). Die unveränderte Modellantwort bleibt dann in `sol_questions.json` zur Diagnose erhalten. `completed` vom API-Dienst allein bestätigt keine brauchbare Extraktion.

Jede Frage enthält `notes` und `note_source_ids` für originale Antwortanweisungen samt Belegen. `position_ids` verbindet sie mit `positions`, beispielsweise Medikament, Stärke oder Packung; `context_source_ids` belegt ihren Kontext. Unbekannte Zuordnungen werden nicht erfunden. Schema-Version 3 verwendet Excel-Adressen mit `document`, `sheet` und `cell_range`; frühere PDF-/Word-Adressfelder entfallen. Details: [Extraktionsvertrag](docs/extraction_contract.md).

Exitcodes: `0` = `completed`, `2` = `needs_review`, `1` = `failed`. Die Validierung verwendet `material_errors_only_v1`: Nur Prüfungen mit `severity="error"` lösen `needs_review` aus. Warnungen und allgemeine `limitations` bleiben sichtbar, blockieren aber nicht. `completed` bedeutet keine erkannten schwerwiegenden Fehler, keine fachliche Freigabe.

Excel-Zeilenumbruch-Escapes werden beim Vergleich normalisiert, leere Dropdown-Einträge ignoriert. Minimale Schreibabweichungen in langen Wörtern und reine Optionsreihenfolge-Abweichungen sind Warnungen. Fehlende Verweise, doppelte IDs, ungültige Adressen, fehlende echte Auswahlwerte und deutliche Textabweichungen bleiben Fehler. Zahlenänderungen, ausgelassene Wörter und veränderte kurze Einheiten werden nicht als Schreibdetails toleriert. Der abschließende Human Review bleibt für die fachliche Prüfung zuständig.

## Tokenbudget und Konfiguration

`EXTRACTION_MAX_OUTPUT_TOKENS=125000` begrenzt die Ausgabe einschließlich Reasoning. `total_tokens` umfasst dagegen Eingabe plus Ausgabe. Ein Lauf mit 46025 Eingabe- und 32768 Ausgabetokens hat 78793 Gesamttokens und kann trotzdem sein Ausgabelimit von 32768 erreicht haben.

`run.calls` speichert das gesendete `max_output_tokens`, Reasoning, Timeout, Antwortstatus, Abbruchgrund, Laufzeit und vom Dienst gemeldete Nutzung, einschließlich `reasoning_tokens`. Ein höheres Budget reserviert keine feste Antwortlänge; große Antworten können weiterhin das Limit erreichen. Requests werden weder aufgeteilt noch inhaltlich abgeschnitten.

Weitere Standardwerte:

| Variable | Standard |
|---|---|
| `OPENAI_MAX_RETRIES` | `2` – begrenzte SDK-Transportwiederholungen |
| `MAX_SOURCE_BYTES` | `100000000` – lokale Eingabedatei |
| `MAX_INPUT_BYTES` | `20000000` – serialisierter Request einschließlich Schema |
| `OPENAI_PROMPT_CACHE_MODE` | `explicit`, alternativ `off` |
| `OPENAI_PROMPT_CACHE_NAMESPACE` | `tender-extraction-v1` |
| `OPENAI_PROMPT_CACHE_TTL` | `30m` |

Cachepräfixe hängen von Prompt, Schema, Deployment und Reasoning ab, nicht von Dateinhalten oder Geheimnissen. Es gibt keine Cache-Warm-ups oder Fallback-Aufrufe. Gemeldete Cache-Nutzung steht im Ergebnis. Die Anwendung sendet `store=false`. `.env`, lokale Eingaben und Ergebnisse gehören nicht in Git.

## Offline testen

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Die Tests blockieren Netzwerkzugriffe und verwenden synthetische Arbeitsmappen sowie HTTP-Mocks. Sie prüfen Streaming, Inhaltserhalt, den einzelnen Sol-Request, Limits, Abbruchverhalten, Anmerkungen, Quellen und Pfadgrenzen. Ein Live-Azure-Test der aktuellen Bereinigung wurde nicht durchgeführt.

Lokaler Vergleich: Norwegian-Beispiel seriell 43,95 Sekunden, automatisch parallel 16,40 Sekunden, jeweils 986 Inhaltszellen. Atosiban-Beispiel 0,19 bzw. 0,21 Sekunden, jeweils 5089 Inhaltszellen; dort bleibt die Verarbeitung wegen der geringen XML-Größe seriell. Die vollständigen Quellenmanifeste beider Verarbeitungspfade stimmten exakt überein. Dies sind Einzelmessungen des Lesers, keine Modell-Qualitätsmessungen. Aktuell: 65 Offline-Tests bestanden, einschließlich echter lokaler Parallelverarbeitung und Schema-/Quellenprüfung des Promptbeispiels.
