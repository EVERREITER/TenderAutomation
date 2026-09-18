# Prüfstand – 17.09.2026

Neuester Stand: **65 Offline-Tests bestanden in 9,39 Sekunden**. Wirksamer Request geprüft: `gpt-5.6-sol`, Reasoning `high`, `max_output_tokens=125000`, Timeout 900 Sekunden. Promptversion 6 mit schemafähigem, quellenvalidiertem Beispiel. Norwegian-Leser: seriell 43,95 Sekunden, automatisch parallel 16,40 Sekunden; vollständige Quellenmanifeste exakt identisch. Zweite Arbeitsmappe: 5089 Inhaltszellen, 0,19 bzw. 0,21 Sekunden, ebenfalls identische Manifeste. Kein neuer Azure-Livetest dieser Promptversion.

## Vorheriger Stand der Bereinigung

Aktueller Ablauf: ausschließlich XLSX, ein Sol-Aufruf und lokale Excel-Validierung. **58 Offline-Tests bestanden in 7,79 Sekunden**. CLI-Hilfe geprüft. Der Abbruch mit 78793 Gesamttokens wurde nachgebildet: 46025 Eingabetokens plus genau 32768 Ausgabetokens bei entsprechendem Ausgabelimit, davon 9302 Reasoning-Tokens. Request-Limit und Abbruchgrund werden protokolliert. Aktuell wirksame Einstellungen: 128000 Ausgabetokens, Reasoning high, Timeout 900 Sekunden. Kein Live-Azure-Aufruf für diese Bereinigung.

## Historische Prüfungen – 16.09.2026

Die folgenden Angaben dokumentieren den damaligen Implementierungsstand. Die dort genannte Modellkette und PDF-/Word-Verarbeitung wurden inzwischen entfernt; die damaligen Wheel-Artefakte entsprechen nicht dem aktuellen Code.

Es wurden ausschließlich lokale und gemockte Prüfungen ausgeführt. Keine kostenpflichtigen Azure-Aufrufe, keine Cloudressourcen und keine fachliche Live-Extraktion.

| Prüfung | Tatsächliches Ergebnis |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q` | **61 passed in 7.93s**, keine übersprungenen Tests |
| Wheel-Build mit `pip wheel --no-deps --no-build-isolation . --wheel-dir outputs/wheel` | erfolgreich |
| Installation des Wheels mit `pip install --no-deps --target outputs/wheel_install ...` | erfolgreich |
| Promptladen aus dem installierten Wheel für alle vier Stufen und CLI `--help` | erfolgreich; Importpfad lag nachweislich in `outputs/wheel_install/` |
| SHA-256-Vergleich Function-App-Ausgangsstand | function_app.py, host.json, local.settings.json, .funcignore und requirements.txt unverändert |
| SHA-256-Vergleich kopiertes Excel/Datenmodell gegen Downloads | identisch |
| Wire-Schema | 61 Objektattribute, geschlossene Objekte, sämtliche Felder required, höchstens fünf Objektebenen |

Getestete Umgebung: Python 3.10.10, OpenAI 3.14.1, openpyxl 3.1.5, Pydantic 2.13.5, pypdf 6.19.0, azure-ai-documentintelligence 1.0.2, pytest 9.1.1.

Die Tests blockieren Socket-Verbindungen und prüfen tatsächliche HTTP-JSON-Bodies über einen MockTransport. Sie decken alle drei Eingabevarianten und Prüferzustände, vollständigen Sol-Ersatz, Sol-Ausfall ohne Rückfall, JSON-/Schemafehler und API-Ablehnung, Cachemodus explicit/off und Keyänderungen, Nutzungsfelder, Pfadgrenzen, Quellenprüfung, Dropdowns, Kontext, Mehrfachziele, Schlüssel und OCR/PDF/Word-Fälle ab. Die tatsächliche LibreOffice-Konvertierung wurde nicht ausgeführt; ihre Pipelineintegration ist gemockt. Reale PDF-/DOCX-Tender wurden nicht bereitgestellt.

## Reale Excel-Strukturanalyse, ohne Azure

`sample_inputs/Questions EVER Pharma Norwegian tender.xlsx` wurde mit `read_excel` vollständig lokal eingelesen. Die Originaldatei wurde nicht geändert. Analysezeit: 145,91 Sekunden. Eingabe: 28.038.921 Bytes; kompaktes JSON-Strukturmanifest: 310.540 Bytes vor lesbarer Einrückung. Der Aufwand entsteht durch tatsächlich in OOXML vorhandene millionenzeilige Formatierungsreste.

| Blatt | Inhaltstragende Zellen | Komprimierte Leerbereiche | Datenvalidierungen / aufgelöste Listen |
|---|---:|---:|---:|
| Bortezomib | 223 | 38 | 23 / 23 |
| Cabazitaxel | 211 | 29 | 22 / 22 |
| Eribulin | 170 | 22 | 17 / 17 |
| Fulvestrant | 170 | 22 | 17 / 17 |
| Pemetrexed | 212 | 29 | 22 / 22 |
| Gesamt | 986 | 140 | 101 / 101 |

Lokale Artefakte: `outputs/sample_analysis.json` und `outputs/sample_manifest.json`. Diese enthalten keine KI-Extraktion und keine geschätzte Zahl fachlicher Fragen. Eine empirische Fragenqualität wurde mangels menschlich geprüfter Soll-Liste und mangels beauftragtem Livetest nicht gemessen. Cache-Hits wurden nur simuliert; keine Aussagen über reale Einsparungen.

Die lokale `.env` fehlt. Azure-Zugänge wurden nicht aus der bestehenden Function-Konfiguration übernommen. Der in der README dokumentierte Einzeldatei-Start setzt eine vom Benutzer konfigurierte `.env` voraus und wurde hier nicht ausgelöst.
