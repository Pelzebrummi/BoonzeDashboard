# Energie Dashboard

Eine Home-Assistant-App, die man über die HA-Seitenleiste öffnet. Man lädt
Rechnungen, Abschlagspläne und Zählerstände als PDF hoch, prüft die
vorgeschlagenen Werte und vergleicht sie mit dem Verbrauch, den Home Assistant
selbst gemessen hat.

Sie läuft auf dem Dell Wyse („BoonzeServer“) innerhalb der Home-Assistant-OS-VM,
ohne zusätzliche VM.

## So kommt ein Commit auf das Gerät

```
git push (main)
  └─ GitHub Actions: Tests → Image bauen und probeweise starten
       └─ Image nach ghcr.io/<owner>/<repo>:<version> → prüfen, dass es ohne Login abrufbar ist
            └─ Branch ha-app: config.yaml mit neuer Version
                 └─ Home Assistant (Automation alle 10 min): Store neu laden → Update installieren → App starten
```

- **Alles wird abgeholt:** Home Assistant holt sich neue Versionen selbst. Das
  funktioniert hinter doppeltem NAT ohne Portfreigabe.
- **Version:** Jeder Push auf `main` ergibt eine Version wie `2026.10.27.42`
  (Datum plus Laufnummer). Von der Bauzeit des Images an dauert es etwa 5–15
  Minuten, bis die App läuft.
- **Nichts manuell ändern:** Den Branch `ha-app` und die Versionsnummer schreibt
  nur der Workflow. Bitte dort nichts von Hand ändern.
- **Workflow-Datei nicht umbenennen:** Die Laufnummer gehört zu
  `.github/workflows/ci.yml`. Nach einer Umbenennung beginnt sie wieder bei 1,
  und jeder weitere Deploy würde als „älter“ übersprungen.
- **Erneut gestartete alte Läufe:** Ein erneut gestarteter alter Lauf
  veröffentlicht nichts. Im Lauf erscheint dann der Hinweis „Nicht
  veröffentlicht“.

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `app/` | FastAPI-App mit Jinja-Seiten, SQLite, PDF-Auslesen und HA-WebSocket-Client |
| `app/parsing.py` | Werte-Vorschläge aus dem PDF-Text und Vorlagen pro Versorger |
| `app/pdf/` | Text-Extraktion mit pypdf in einem eigenen Prozess mit Speicher- und Zeitlimit |
| `ha-app/` | Vorlage der App-Definition (`repository.yaml`, `config.yaml`, Übersetzungen) |
| `scripts/publish-ha-app.sh` | Füllt die Vorlage aus und schreibt sie auf den Branch `ha-app` |
| `.github/workflows/ci.yml` | Tests, Image-Build und Veröffentlichung |
| `homeassistant/auto_deploy_automation.yaml` | Automation, die neue Versionen installiert |
| `tests/` | pytest-Tests, unter anderem mit einer kleinen Fake-HA-WebSocket-API |

## Einmalige Einrichtung auf GitHub

1. **Repository anlegen und pushen:** Das Repository muss öffentlich sein, denn
   Home Assistant klont den Branch `ha-app` ohne Zugangsdaten. Danach den Branch
   `main` pushen.
2. **Paket öffentlich machen:** Der erste Lauf des Workflows **CI** bricht
   absichtlich beim Schritt „Check that the image can be pulled without login“
   ab, weil neue GHCR-Pakete privat sind. Dann unter „Packages“ das Paket
   `<repo>` öffnen → *Package settings* → *Change visibility* → **Public**.
   Ein privates Image ginge auch, bräuchte aber einen Token auf dem Gerät. Läuft
   der ab, scheitern alle Updates, und vor Ort merkt das niemand.
3. **Lauf neu starten:** Den abgebrochenen Lauf mit *Re-run failed jobs* neu
   starten. Danach gibt es den Branch `ha-app`.

## Einmalige Einrichtung in Home Assistant (vor Ort)

Repository-URL, Branch und `slug` dürfen sich danach nicht mehr ändern. Sonst
hält HA die App für eine neue App, und deren Datenordner ist leer.

1. **Repository eintragen:** Einstellungen → Apps → App Store → ⋮ →
   Repositories. URL: `https://github.com/<owner>/<repo>#ha-app`
2. **App installieren und starten:** „Energie Dashboard“ installieren. Im Tab
   *Info* **Watchdog** und *In der Seitenleiste anzeigen* einschalten, dann
   starten. Mit dem Watchdog startet der Supervisor die App neu, wenn sie
   abstürzt oder nicht mehr antwortet.
   Danach Home Assistant einmal neu starten (Einstellungen → System → ⏻ →
   Home Assistant neu starten). Erst dann legt HA die Update-Entität
   `update.energie_dashboard_update` an; vorher erscheinen keine Updates und
   die Automation greift nicht (beobachtet mit HA 2026.10.4).
3. **Statistiken zuordnen:** Im Dashboard unter **HA-Statistiken** die IDs der
   Zähler nachsehen. Unter Konfiguration bei Strom, Gas und Wasser eintragen,
   dann die App neu starten.
4. **Automation anlegen:** Die Automation aus
   `homeassistant/auto_deploy_automation.yaml` anlegen. Prüfen, dass die Entity
   `update.energie_dashboard_update` heißt (Einstellungen → Geräte & Dienste →
   Entitäten). Die Automation installiert neue Versionen und startet die App,
   falls sie nicht läuft. Nach einem fehlerhaften Release startet der Supervisor
   die App nämlich auch mit dem korrigierten Release nicht von selbst. Nach dem
   ersten Lauf die Ablaufverfolgung (Trace) der Automation ansehen: Alle
   Schritte sollten ohne Fehler durchlaufen.
5. **Backup außer Haus:** Unter Einstellungen → System → Backups ein Ziel
   außerhalb des Wyse einrichten, z. B. Google Drive oder OneDrive. PDFs und
   Datenbank liegen im Datenordner der App und sind in jedem Backup enthalten.

## Lokal entwickeln

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
```

Nur die Parser-Tests ausführen (bei aktivierter Python-Umgebung):

```bash
pytest tests/test_parsing.py
```

Starten ohne Home Assistant (Daten landen in `./data`):

```bash
DEV_MODE=1 .venv/Scripts/python -m uvicorn app.main:create_app --factory --reload --port 8099
```

Mit echten Statistiken: zusätzlich `HA_WS_URL=ws://<ha-ip>:8123/api/websocket`
und `HA_TOKEN=<langlebiges Zugriffstoken>` setzen. Die Zuordnung der
Statistiken steht dann in `./data/options.json`, z. B.
`{"strom_statistic_id": "sensor.stromzaehler"}`.

## Design

- **Design System:** Farben, Schriften, Abstände, Komponenten und
  Sprachregeln stehen im Design System „Boonze Dashboard“:
  https://claude.ai/artifact/8PV9NmjEZAHoWUoowAVJUr. Neue Seiten bauen aus
  dessen Komponenten (Klassen `bz-…`).
- **Umsetzung in der App:** `app/static/boonze.css`. Die Token-Werte dort
  müssen mit `tokens.json` im Design System übereinstimmen. Ändert sich eine
  Farbe, an beiden Stellen ändern.
- **Schriften:** liegen in `app/static/fonts` (Atkinson Hyperlegible Next und
  Mono, Bricolage Grotesque, SIL Open Font License) und werden nicht von
  Google geladen. Das Dashboard sieht also auch ohne Internet gleich aus.
- **Hell und dunkel:** Das Dashboard folgt dem Dunkelmodus von Home
  Assistant, außerhalb von HA der Systemeinstellung.

## Worauf man beim Entwickeln achten muss

- **Pfade immer mit `base`:** Die App läuft unter
  `/api/hassio_ingress/<token>/`. Links, Formular-Ziele und Redirects deshalb
  immer mit `{{ base }}` bzw. `redirect()` bauen, nie mit einem nackten `/`.
- **Titel in der Seitenleiste ändern:** Ein geänderter `panel_title` in
  `ha-app/energie_dashboard/config.yaml` erscheint erst, wenn man nach dem
  Update in der App „In Seitenleiste anzeigen“ einmal aus- und wieder
  einschaltet.
- **Keine Pfade in Query-Parametern:** Der Supervisor blockt Anfragen mit
  Mustern wie `?next=/irgendwas` (HTTP 400). Pfade gehören in Formularfelder.
- **Datenbank:** Schemaänderungen nur als neue Migration am Ende von
  `MIGRATIONS` in `app/db.py`. Bestehende Migrationen nie ändern, denn der
  Datenordner der App bleibt über Updates erhalten.
- **Neue Versorger-Vorlagen:** In `PROVIDER_TEMPLATES` in `app/parsing.py`
  eintragen, mit einem Test, der echten (anonymisierten) Text verwendet.
- **PDF-Text:** Die App liest nur den Textinhalt einer PDF. Bei gescannten PDFs
  ist keiner vorhanden, dann trägt man die Werte in der Prüfmaske von Hand ein.
- **Bankdaten:** Abbuchungen lassen sich zuverlässiger aus dem CSV-Export des
  Online-Bankings übernehmen als aus Kontoauszug-PDFs. Ein CSV-Import wäre der
  nächste sinnvolle Ausbauschritt.
- **Uploads:** Uploads laufen per POST mit `ingress_stream: true`. Ohne diese
  Option puffert der Supervisor die Anfrage und bricht bei 16 MiB ab.

