# Eingabe lesen

source_manifest enthält die gesamte Arbeitsmappe. Je Blatt steht cells für A1-Adresse → gespeicherter Wert. Die Adresse erhält Zeilen-/Spaltenbeziehungen; fasse Beschreibungen, Überschriften und Hinweise anhand dieser Beziehungen zusammen. Benutze den gesamten Kontext, auch wenn Fragen in Spalten statt Zeilen angeordnet sind oder Hinweise auf einem anderen Blatt stehen.

formulas enthält Formeln; bei ihnen ist cells der gespeicherte Cache oder null. cell_formats erhält Zahlen-/Datumsformate, date_epoch die Grundlage für Excel-Datumswerte. Kommentare, Merges, Sichtbarkeit, Namen und validations liefern zusätzlichen Kontext. Statische Dropdownwerte sind bereits aufgelöst. Werte weder Formeln noch externe Verknüpfungen aus.

Alle Inhaltszellen werden bereitgestellt. Nur rein leere Formatierungszellen fehlen in cells; das ist kein Beweis für ein fehlendes Antwortfeld. Leere Antwortadressen dürfen aus belegbarer Tabellenstruktur, Beschriftungen und Validierungsbereichen folgen. Farben oder Zellschutz allein beweisen kein Antwortziel.

# Formatbeispiel – keine Daten der tatsächlichen Quelle

Angenommen, ein Blatt „Angebot“ enthält A1="Produkt Beispiel", A3="Restlaufzeit angeben.", B3="In ganzen Monaten.", C2="Antwort" und eine zugehörige leere Antwortzelle C3. Der passende vollständige Datensatz lautet:

```json
{
  "questions": [{"id":"q1","original":"Restlaufzeit angeben.","normalized":"Restlaufzeit angeben.","notes":"In ganzen Monaten.","note_source_ids":["s2"],"language":"de","kind":"information","section":null,"number":null,"order":1,"source_ids":["s1"],"context_status":"extracted","context_source_ids":["s3"],"position_ids":["p1"],"subquestion":null,"expected_answer":"number","confidence":null}],
  "answer_fields": [{"id":"f1","question_id":"q1","label":"Antwort","role":"main_answer","semantic_type":"number","control_type":"excel_cell","target_status":"located","address":{"document":"original","sheet":"Angebot","cell_range":"C3"},"order":1,"source_ids":["s1","s4"]}],
  "options": [],
  "source_references": [
    {"id":"s1","address":{"document":"original","sheet":"Angebot","cell_range":"A3"},"quote":"Restlaufzeit angeben."},
    {"id":"s2","address":{"document":"original","sheet":"Angebot","cell_range":"B3"},"quote":"In ganzen Monaten."},
    {"id":"s3","address":{"document":"original","sheet":"Angebot","cell_range":"A1"},"quote":"Produkt Beispiel"},
    {"id":"s4","address":{"document":"original","sheet":"Angebot","cell_range":"C2"},"quote":"Antwort"}
  ],
  "positions": [{"id":"p1","lot":null,"product":"Produkt Beispiel","strength":null,"pack":null,"form":null,"source_ids":["s3"]}],
  "attributes": [],
  "limitations": []
}
```

Wende die Beziehungen des Beispiels auf die tatsächliche Quelle an. Übernimm weder seine Inhalte noch seine Adressen. Gib für die Arbeitsmappe ein einziges JSON-Objekt nach dem vorgegebenen Schema aus, ohne Markdown oder Begleittext.
