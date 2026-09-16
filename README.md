# Lokale Tenderfragenextraktion

Separates Python-Paket für genau eine `.xlsx`, `.pdf` oder `.docx` aus `sample_inputs/`. Die bestehende Azure Function App einschließlich `requirements.txt` bleibt unverändert. Enthalten sind ausschließlich Fragenextraktion, Prüfung auf übersehene Fragen und technische Quellenvalidierung. Kein Dataverse-Import, Matching, Antwortgenerieren oder Dokumentbefüllen.

## Installation

Python **3.10 oder neuer**. Implementierung und Offline-Tests wurden mit dem vorhandenen Python 3.10.10 geprüft. Eine eigene Umgebung hält die lokalen Zusatzabhängigkeiten von der Function-Laufzeit getrennt:

```powershell
python -m venv .venv-extraction
.\.venv-extraction\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Alternativ mit einer bereits aktivierten geeigneten Umgebung: `python -m pip install -e ".[test]"`. Für diese Implementierung wurde die vorhandene `.venv` verwendet; keine Function-Quelldatei oder Function-Abhängigkeitsdeklaration wurde geändert. Das offizielle OpenAI-SDK **3.14.1** wurde getestet; zulässiger Bereich `>=3.14.1,<4`. Promptdateien sind Paketdaten und werden auch aus einem installierten Wheel geladen.

In `.env` OpenAI-base_url, Schlüssel und tatsächliche Azure-Deploymentnamen für Luna/Terra/Sol eintragen. Beispiel-URL: `https://YOUR-RESOURCE.openai.azure.com/openai/v1/`; der Foundry-Projektendpunkt `/api/projects/...` ist ungeeignet. Deploymentnamen sind frei konfigurierbar, keine behauptete automatische Modellbereitstellung. `local.settings.json` wird nicht als Konfigurationsquelle gelesen.

PDF/Word benötigen zusätzlich Document-Intelligence-Endpunkt, Schlüssel und API-Version (`2024-11-30`). Excel benötigt weder diesen Dienst noch LibreOffice. Word benötigt lokal LibreOffice:

```powershell
winget install TheDocumentFoundation.LibreOffice
```

Danach in `.env` beispielsweise `LIBREOFFICE_PATH=C:/Program Files/LibreOffice/program/soffice.exe` setzen. Die Konvertierung nutzt eine Kopie, ein isoliertes temporäres Profil, Argumentlisten ohne Shell und einen Timeout. Die entstandene PDF wird geprüft und im Laufverzeichnis aufbewahrt.

## Einzeldatei starten

Aus dem Projektverzeichnis, mit konfigurierter `.env`:

```powershell
.\.venv\Scripts\python.exe -m tender_extraction --input "sample_inputs/Questions EVER Pharma Norwegian tender.xlsx" --output-dir "outputs"
```

Mit aktivierter Umgebung lautet derselbe Befehl:

```powershell
python -m tender_extraction --input "sample_inputs/Questions EVER Pharma Norwegian tender.xlsx" --output-dir "outputs"
```

**Dieser Startbefehl ruft kostenpflichtige Azure-Dienste auf. Er wurde während der Implementierung nicht ausgeführt.** Ohne `--input` erscheint ein Argumentfehler; es gibt keine automatische Stapelverarbeitung. Unterordner, Unicode und Leerzeichen sind zulässig; Wildcards, `..` und Symlink-Ausbrüche werden abgewiesen. Start aus dem Projektverzeichnis ist erforderlich, damit `sample_inputs/` und `.env` eindeutig sind.

Zu Beginn lagen weder `sample_inputs/` noch das Datenmodell im Repository. Das im Downloadordner vorhandene Excel-Beispiel wurde unverändert nach `sample_inputs/` kopiert, das Datenmodell nach `docs/`. Die Originaldateien bleiben erhalten. Nur dieses gefundene Excel-Beispiel wurde lokal strukturell analysiert. Es besitzt fünf Produktblätter mit sehr großen formatierten Leerbereichen. Es wurden keine echten `.xls`-/`.doc`-Samples bereitgestellt; diese Formate sind nicht unterstützt und benötigen eine gesonderte, geprüfte Konvertierungserweiterung. Synthetische Testdaten werden ausschließlich in temporären Testordnern erzeugt.

## Ablauf und Ergebnisse

Excel: Luna extrahiert, ein separater Terra-Aufruf prüft ausschließlich auf fehlende Fragen und Antwortkontexte. PDF/Word: Terra extrahiert und ein neuer Terra-Aufruf prüft. Jeder Aufruf erhält die vollständige Quelle erneut; PDF als tatsächliches `input_file` mit Base64-Daten, Word als exakt dieselbe erzeugte PDF plus Originalstrukturindex.

Nur `missing_found` startet einmal Sol mit der gesamten Quelle und ohne alten Fragenkatalog. Sol ersetzt den bisherigen Kandidaten vollständig. Danach erfolgt kein weiterer LLM-Aufruf. Für PDF/Word folgt genau eine Analyse derselben PDF mit Document Intelligence `prebuilt-layout`, deren Ergebnis in der Python-Validierung wiederverwendet wird. Normalfall zwei, bei Lücken drei fachliche OpenAI-Aufrufe; zusätzlich nur begrenzte SDK-Retries bei vorübergehenden Transportfehlern. Keine fachlichen Reparaturschleifen.

Jeder Lauf erhält ein neues Verzeichnis `outputs/<run_id>/`:

```text
result.json                         angereicherter finaler Kandidat mit Laufstatus
validation_report.json              strukturierte technische Einzelprüfungen
run_manifest.json                   Stufen, Konfiguration ohne Schlüssel, Nutzung
source.xlsx / source.pdf / source.docx   unveränderte Eingabekopie
source_manifest.json                vollständige strukturelle Quelldarstellung
initial_candidate.json              Diagnose: erste Extraktion
completeness_review.json            vorausgehende Terra-Prüfung
sol_candidate.json                  nur bei Eskalation
*_response_diagnostic.json          Antwortstatus, JSON/Fehlerdetails und Nutzung
rendered.pdf                        nur Word: festgehaltene Konvertierung
document_intelligence.json          nur PDF/Word: OCR/Layout-Ergebnis
```

`completed` (Exitcode 0) heißt: vereinbarte Schritte abgeschlossen, keine dokumentierten technischen Prüfprobleme. Es ist **keine fachliche Freigabe oder Vollständigkeitsgarantie**. `needs_review` (Exitcode 2) kennzeichnet unter anderem unklare Vollständigkeitsprüfung, fehlende Ziele, unbekannten Kontext, nicht verifizierbare oder widersprüchliche Quellenangaben. `failed` (Exitcode 1) kennzeichnet technische/API-/Schemafehler. Ein fehlgeschlagener Lauf hat keine finalen Fragen; erhaltene Kandidaten sind ausschließlich Diagnoseartefakte. Ein Sol-Fehler führt niemals zum Rückfall auf die Erstextraktion.

## Konfiguration und Limits

Alle Einstellungen stehen in `.env.example`. Startwerte: Luna-Erstextraktion `medium`, Terra-Erstextraktion und -Prüfung sowie Sol `high`; Timeout 300 Sekunden, SDK-Retries 2, Extraktions-Ausgabelimit 32768 Tokens, Prüferlimit 8192 Tokens. Reasoning kann das Ausgabelimit mitverbrauchen. Diese Startwerte sind keine empirisch optimierten Qualitätsgarantien.

`MAX_SOURCE_BYTES=100000000` begrenzt die lokale Eingabedatei, `MAX_INPUT_BYTES=20000000` den vollständig serialisierten Request einschließlich Schema und Prüfkandidat. Das ist eine zusätzliche lokale Schutzgrenze, keine Tokenzählung oder Zusicherung des deploymentabhängigen Kontextfensters. Weitere Azure-Limits werden als Fehler gemeldet. Kein Abschneiden von Blättern/Seiten, keine automatische Zerlegung, kein stiller Modellwechsel. Datenvalidierungs-Optionsbereiche über 100000 Zellen bleiben als Rohregel mit Einschränkung erhalten; echte Zellinhalte werden trotzdem vollständig aufgenommen.

Die Anwendung sendet `store=false`, keine Tools, Suchdienste, gespeicherten Chats oder `previous_response_id`. Dies ist keine Zusicherung vollständiger Nicht-Speicherung durch Azure. `.env`, `outputs/`, temporäre Umgebungen und lokale Samples sind von Git ausgeschlossen. Ausgaben enthalten sensible Quelldaten und bleiben lokal; normale Konsolenausgabe enthält nur Status, Verzeichnis und kurze Fehlerkategorie.

## Prompt Caching

`OPENAI_PROMPT_CACHE_MODE=explicit`, `OPENAI_PROMPT_CACHE_NAMESPACE=tender-extraction-v1`, `OPENAI_PROMPT_CACHE_TTL=30m` sind die Standardwerte. Ein stabiler developer-Block enthält `common.md` und den Aufgabenprompt. Genau ein Breakpoint steht an seinem Ende. Erst danach folgen variable user-Daten. Das strenge Schema wird separat mit `text.format` gesendet. Cachefelder werden über das dokumentierte SDK-`extra_body` übertragen; HTTP-Mocktests prüfen den serialisierten Request.

Der kurze Schlüssel `ta:<48 Hexzeichen>` beruht auf Namespace, Ressource/Deployment, Stufe, Excel/PDF/Word-PDF-Variante, Versionen, tatsächlichem Prompttext, deterministischem Schema und Reasoning. Keine Dateinamen, Dokumentinhalte, Datei-Hashes, Lauf-IDs, Zeiten oder Geheimnisse. Prompt-/Schemaänderungen ändern den Fingerprint auch ohne Versionsanpassung. Jede Stufe hat ihre eigene Cachegruppe.

`off` sendet weiterhin `prompt_cache_options.mode="explicit"`, aber keinen Breakpoint und keinen Key. Bei abgelehnten Cacheparametern folgt `cache_configuration_unsupported` mit konkretem Dienstfehler im lokalen Diagnoseartefakt; kein automatischer Fallback oder Wiederholungsaufruf. Keine Warm-ups, keine Zusatzaufrufe wegen Cache-Miss, kein lokaler KI-Ergebniscache und keine 24h-Retention. TTL ist keine Datenschutzrichtlinie.

Pro tatsächlichem fachlichem Request speichert das Manifest Stufe, Deployment, zurückgemeldetes Modell, Cachemodus/Key/Fingerprint/TTL, Laufzeit und die vollständige zurückgelieferte `usage`. Ausgewertet werden `input_tokens`, `output_tokens`, `input_tokens_details.cached_tokens` und gegebenenfalls `cache_write_tokens`. Fehlendes bleibt null; `cache_hit` ist true bei >0, false bei explizit 0, sonst null. Cache-Writes sind keine Hits; gecachte Tokens sind schon in den Eingabetokens enthalten. SDK-interne Transportversuche werden nicht als zusätzliche fachliche Stufen ausgegeben.

Konfiguriertes Caching beweist keinen Treffer. Es gibt keine Einsparungsbehauptung und keinen Kostenrechner. Die Mindestpräfixlänge wird nicht durch Fülltext erzwungen. Die Implementierung folgt den dokumentierten [Azure-Cacheparametern](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching) und [OpenAI-Cachemechanismen](https://developers.openai.com/api/docs/guides/prompt-caching); Schemaaufbau nach [Azure Structured Outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs). Geprüft am 16.09.2026; tatsächliche Deploymentunterstützung wurde nicht live getestet.

## Offline prüfen

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests blockieren echte Socket-Verbindungen. Sie verwenden synthetische Excel-, PDF- und Wordstrukturen sowie Antworten über einen HTTP-Mock des offiziellen OpenAI-SDK. Geprüft werden Modellreihenfolge, Ersatz statt Zusammenführung, Sol-Fehler, unklare Prüferzustände, Quellen/Optionen/Kontext, Originalschutz, Pfadgrenzen und Cache-Requestdaten. Simulierte Treffer sind keine realen Azuremesswerte. Die separate lokale Analyse des tatsächlichen Excel-Beispiels benötigt ebenfalls keinen Azurezugang.

Kein Livetest wurde beauftragt oder durchgeführt. Eine eigene `.env` mit den erforderlichen Zugängen fehlt derzeit; vorhandene Function-Geheimnisse werden nicht übernommen. Details des Vertrags, Datenmodellbezug und Validierungsgrenzen: [docs/extraction_contract.md](docs/extraction_contract.md).

Tatsächlicher Abschlussstand: **61 Offline-Tests bestanden**, Wheel gebaut und isoliert installiert, Promptdateien und CLI aus dem installierten Paket geprüft. Zahlen und Prüfgrenzen stehen in [docs/verification.md](docs/verification.md).

```text
src/tender_extraction/  CLI, Konfiguration, Schema, Pipeline, Validierung
  readers/             Excel-OOXML, Wordstruktur, PDF, lokale Konvertierung
  services/            Azure Responses und Document Intelligence
  prompts/             vier versionierte, installierbare Promptdateien
tests/                 standardmäßig ausschließlich offline
docs/                  Vertrag und Datenmodell
sample_inputs/         lokale unveränderte Eingabekopien
outputs/               lokale Laufartefakte und Analyseberichte
```
#   T e n d e r A u t o m a t i o n  
 