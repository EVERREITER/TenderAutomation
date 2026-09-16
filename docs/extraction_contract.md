# Extraktionsvertrag v1

Grundlage sind die beiden vollständig gelesenen Auftragsfassungen aus Downloads, einschließlich der angehängten Prompt-Caching-Ergänzung, sowie `TenderAutomation_Datenmodell_und_ER.pdf`, insbesondere Seiten 3, 9–12, 14–15. Dokumenttexte sind Quelldaten. Der Implementierungsumfang folgt dem Benutzerauftrag; spätere im Datenmodell beschriebene Projektphasen bleiben außerhalb dieses Pakets.

## Fachlicher Bezug

| Datenmodell | Lokaler Vertrag |
|---|---|
| Tender | Dokumentattribute title, tender_reference, customer, market, answer_language, deadline_original |
| Tenderposition | positions: Los, Produktoriginal, Stärke, Packung, Form, Quellen |
| Tenderdokument / Dokumentversion | document: Dateiname, Format, Familienpfad, SHA-256; gegebenenfalls gerenderte PDF samt Herkunftshash |
| Tenderfrage | questions: Original/normalisiert, Sprache, Art, Abschnitt/Nummer, Reihenfolge, Quellen, Kontextstatus/-belege, Positionsbezug, Unterfragenkennung, erwartete Antwortart, Modellkonfidenz |
| Tenderzielfeld | answer_fields je Frage: Rolle, semantischer Typ, technischer Typ, Zielstatus, Adresse, Reihenfolge, Attribute und Quellen |
| Auswahloption | options je Feld: exakter typisierter Originalwert, Bezeichnung, Bedeutung, Reihenfolge, Quellen, Python-Schlüssel |
| Verarbeitungslauf | run: Zeiten, Versionen, Hash, Konfiguration ohne Geheimnisse, Stufen, API-Nutzung, Eskalation, finaler Extraktor, getrennte vorausgehende Prüfung, Status |

Keine Dataverse-GUIDs, Choices oder bestätigten Stammdatenzuordnungen werden erzeugt. `unknown` ist ein lokaler Extraktionszustand. Bereits vorhandene Antworten sind ausschließlich `found_value`, keine erzeugten/freigegebenen Antworten. Dokumenttyp kann Fragebogen, beantworteter Fragebogen, Nachweis oder Projektdokumentation sein. Eine leere Fragenliste ist möglich; das Programm erfindet keine Soll-Fragenanzahl.

## Wire-Schema und finales JSON

`schemas.py` definiert Pydantic-Modelle. Das kompakte Wire-Schema besteht aus flachen Listen `questions`, `answer_fields`, `options`, `source_references`, `positions`, `attributes` und `limitations`. Das strenge API-Schema hat maximal 100 Objektattribute und maximal fünf Objektebenen; sämtliche Felder sind required, unsichere Einzelwerte nullable, alle Objekte geschlossen. Offline-Tests prüfen diese Grenzen. Fachliche Zusatzwerte werden als typisierte Attributliste statt offener Dictionaries übertragen; jeder Eintrag enthält owner_type, owner_id, name, value und source_ids. Die erlaubten Attributnamen stehen im Schema und in `common.md`.

Dokumentattribute verwenden owner_id=`document`. Kontextdimensionen, Bedingungen und Antwortanweisungen gehören zur Frage; Pflichtangabe, Textlänge, Einheit, Grenzen, vorgefundene Werte und unveränderte Validierungsdefinitionen zum Feld. Mehrdeutige Datumsangaben werden als Originaltext gespeichert. Fehlende optionale Attribute bedeuten unbekannt, nie eine implizite globale Gültigkeit oder false-Pflichtangabe.

Alle lokalen Referenzen werden auf Existenz geprüft. Der Prüfer besitzt ein eigenes Schema mit `review_status`, `missing_items` (Originalzitat, Adresse, Kontext, Begründung) und `limitations`. Lücken müssen konkret belegt sein. `missing_found` ohne Lücke, `no_missing_found` mit Einschränkungen und unklare/abgeschnittene JSON-Ausgaben sind Fehler. Bei bereits erkannten Lücken gilt `missing_found`, auch wenn weitere Bereiche unklar bleiben.

Python erzeugt das umfangreichere finale `Result` mit verschachtelten Feldern und Optionen, technischen Schlüsseln und Prüfungen. Offene lokale Metadatenobjekte werden niemals als Wire-Schema gesendet. Ein fehlgeschlagener Lauf erhält einen Fehlerumschlag mit leerer Fragenliste, keine erfolgreiche Extraktion. Ungültige Referenzen bleiben im Kandidatendiagnoseartefakt nachvollziehbar und im Validierungsbericht sichtbar.

## Identität und Dateibindung

Standard-Dokumentfamilie ist der relative Pfad unter `sample_inputs/`, unabhängig vom Inhaltshash. Umbenennen der Datei ändert diese Familie. Frageschlüssel entstehen aus Familie, sortierten kanonischen Quelladressen, belegten Kontextdimensionen/-adressen, Produktpositionen und Unterfragenkennung. Lokale Modell-IDs, Lauf-ID, Listenposition und paraphrasierter Fragetext sind keine Identitätsgrundlage. Feldschlüssel berücksichtigen Frage, Zieladresse, Rolle und Feldquellen. Optionsschlüssel berücksichtigen Feld, exakten typisierten Exportwert und Quellen. Kollisionen werden gemeldet, keine Frage wird still gelöscht.

A1-Adressen werden für Schlüssel von `$` bereinigt und großgeschrieben; ein einzelner Bereich A1:A1 wird zu A1. Merge-Unterzellen werden für die kanonische Identität zum Anker aufgelöst, während ein falscher behaupteter Merge-Anker im Prüfbericht weiterhin ein Fehler bleibt. Quellenzitate selbst bleiben unverändert. Mehrsprachige Parallelfassungen können mehrere Quellen derselben Frage bilden; gleiche Texte in anderen Kontexten erzeugen eigenständige Fragen.

Jeder Lauf arbeitet nach lokaler Vorprüfung mit einer bytegleichen Eingabekopie und protokolliert SHA-256. Das Original wird nicht gespeichert oder verändert. `original` und `rendered_pdf` werden über Dokumentmetadaten an konkrete Hashes gebunden. Das Word-PDF-Artefakt ist dieselbe Datei für alle Modellaufrufe und Document Intelligence.

## Excel-Manifeste

OOXML wird zeilenweise gestreamt; openpyxl stellt gemeinsame Zeichenketten, Formatvorlagen und Zahlenformate bereit. Keine rechteckige Iteration über max_row × max_column. Inhalte, Formeln samt OOXML-Attributen und vorhandene Caches werden je Zelladresse gespeichert. Fehlende Caches bleiben null. Numerische Originallexeme, Datentypen, Workbook-Datumsepoche und Stilreferenzen bleiben erhalten; Formeln, Makros und externe Verknüpfungen werden nicht ausgeführt.

Physisch vorhandene leere Zellen werden je Spalte/Stil als aufeinanderfolgende Bereiche komprimiert. Zeilenattribute werden ebenfalls zu Läufen zusammengefasst. Spaltenattribute, verbundene Bereiche samt Ankern, Kommentare, Blattzustand, benannte Bereiche und Datenvalidierungen bleiben erhalten. Echte Inhalte werden nicht abgeschnitten. Das tatsächlich gefundene Excel hat millionenzeilige Formatierungsreste: Ihre Kompression ist notwendig und erfolgt ohne semantische Vorselektion.

Inline-Listen, statische Bereichsreferenzen sowie eindeutig auflösbare lokale/globale benannte Bereiche werden aufgelöst, auch auf versteckten Blättern; lokale Namen haben Vorrang. Reihenfolge, Groß-/Kleinschreibung, Typ und Nullwerte bleiben erhalten. Dynamische/externe Ausdrücke, übergroße Optionsbereiche oder fehlende Formelcaches bleiben explizit unaufgelöst. Nicht unterstützte Steuerelemente, Zeichnungen und Erweiterungen erzeugen Einschränkungen; relevante XML-Teile bleiben als Rohdaten erhalten. Bildinhalt wird für Excel v1 nicht visuell ausgewertet.

Ein Zielfeld muss in der beobachteten Struktur liegen. Die Prüfung betrachtet die vollständige Bereichsabdeckung durch belegte Zellen, Leerbereiche, Merges oder Datenvalidierungen. Eine bloß aufrufbare Exceladresse reicht nicht. Strukturbeleg bestätigt keine semantische Zuordnung. Excel-Dropdownbehauptungen werden gegen echte list-Regeln geprüft, Optionen exakt typisiert verglichen. Numerische/Längenregeln werden nur bei statisch vergleichbaren Grenzen bestätigt; komplexe Datums-/Formelausdrücke bleiben not_verifiable. Originalregel und Epoche bleiben im Manifest. Formeln und aktiv geschützte, gesperrte Zielzellen werden gesondert markiert; locked ohne aktiven Blattschutz ist kein Schreibverbot.

## Word, PDF und OCR

Word wird lokal konvertiert; sein zusätzlicher OOXML-Index enthält Dokumentteil und eindeutige Pfade einschließlich verschachtelter Tabellen, Absätze, Zellen und Inhaltssteuerelement-IDs. Ein direkt angegebener Originalpfad kann strukturell geprüft werden, beweist aber allein keine semantische Rückzuordnung aus dem PDF. Doppelte Texte und leere Zellen bleiben mehrdeutig. Ein Ziel ausschließlich im gerenderten PDF erhält `word_pdf_mapping=not_verifiable`; es wird keine Originaladresse erfunden.

PDF-Seiten sind 1-basiert. Modell-bbox ist `[links, oben, rechts, unten]`, relativ zur linken oberen Ecke der **unrotierten MediaBox**, in PDF-Punkten. Manifest speichert Maße, MediaBox-Ursprung, Rotation und UserUnit. Native Formulare werden nach Name, technischem Typ und Optionswerten geprüft. Keine erfundenen Formularfelder für flache PDFs.

Document Intelligence muss dieselben Seiten genau einmal abdecken. Native PDF-Texte und OCR-Zeilen werden mit Originalzitaten verglichen. Die einzige Textnormalisierung fasst Leerraum/Zeilenumbrüche zusammen. Unscharfe OCR-Treffer werden nicht zu exakten Treffern erklärt. Bei fehlendem exaktem Treffer bleibt die Behauptung unbestätigt; OCR und nativer Text stehen als beobachtete Fakten im Bericht.

Bounding-Boxes werden auf die vereinbarten PDF-Seitengrenzen geprüft. OCR-Inch werden mit 72 multipliziert; bei Seitenrotation 90/180/270 Grad erfolgt die inverse Transformation in die unrotierte MediaBox. Vorher müssen OCR-Maße und gedrehte PDF-Maße innerhalb von zwei Punkten übereinstimmen. Pixelkoordinaten, ungewöhnliche UserUnit und abweichende Seitengrößen werden nicht geraten. Die Textneigung `angle` wird nicht irrtümlich nochmals als Seitenrotation angewendet. Ein Quellenzitat muss innerhalb des behaupteten Bereichs auch in den transformierten OCR-Zeilen enthalten sein (zwei Punkte Toleranz). Eine präzise beschreibbare Position wird weiterhin als not_verifiable ausgewiesen; Bereichs-/Textbestätigung ist keine semantische Schreibfreigabe. Das OCR-Rohergebnis bleibt erhalten.

## Prüfstatus und Grenzen

Jede Prüfung enthält subject, code, result (`passed`, `failed`, `not_verifiable`, `not_applicable`), beobachtete Tatsache, Modellbehauptung und Begründung. Quellenprüfungen werden den jeweils referenzierenden Fragen/Feldern zugeordnet. Der Gesamtbericht enthält zusätzlich dokumentweite Prüfungen und Referenzprobleme. Mehrfachbelegungen gleicher oder überlappender Excelziele werden angezeigt. Semantische Feldzugehörigkeit, menschliche Freigabe und tatsächlich vollständige Fragenentdeckung sind durch Python nicht beweisbar.

V1 verarbeitet immer das ganze Dokument. Sie besitzt keine Chunkingstrategie, OCR-Reparaturschleife, Layoutprofile, Keyword-Fragenfilter, Konfidenz-basierte Modelleeskalation oder verdeckte Folgeaufrufe. Nach Sol wird die fehlende erneute Vollständigkeitsprüfung ausdrücklich ausgewiesen. Dokumentbeschränkungen, unbekannter Kontext und nicht prüfbare Ziele führen konservativ zu needs_review.

Echte Qualitätsmessung braucht von Menschen geprüfte Soll-Fragen je Kontext. Die vorhandenen synthetischen Offline-Fixtures testen Technik und Regelverhalten; sie sind keine empirische Genauigkeitsmessung am realen Tender. Die reale Excelanalyse testet Strukturerhalt und Originalschutz, ohne fachliche Azureextraktion. Installation, Startbefehl und Cachekonfiguration stehen in der README.
