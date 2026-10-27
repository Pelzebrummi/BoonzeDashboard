# Energie Dashboard

Rechnungen, Abschläge und Zählerstände als PDF hochladen, die Werte prüfen und
mit dem Verbrauch vergleichen, den Home Assistant gemessen hat.

## Einrichtung

1. Im Tab *Info* „Watchdog“ und „In der Seitenleiste anzeigen“ einschalten,
   dann die App starten.
2. Im Dashboard die Seite **HA-Statistiken** öffnen. Dort stehen alle
   Zähler-Statistiken, die Home Assistant kennt.
3. Die passenden IDs hier unter **Konfiguration** bei Strom, Gas und Wasser
   eintragen und die App neu starten.

## Daten und Backups

PDFs und Datenbank liegen im Datenordner der App und sind in jedem
Home-Assistant-Backup enthalten. Während eines Backups wird die App kurz
angehalten, damit die Datenbank vollständig gesichert wird.

Wer die App deinstalliert, löscht damit auch alle hochgeladenen PDFs und Einträge.
