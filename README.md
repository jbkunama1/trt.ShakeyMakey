# trt.ShakeyMakey 🎲

Mobile-optimierte **PWA** zum zufälligen Auslosen von Teams (Klon von „Team Shake“).
Persistente Datenhaltung in **SQLite**, ausgeliefert als **ein eigenständiger
Docker-Container** (Nginx + Gunicorn/Flask + Telegram-Bot via Supervisor),
Build & Publish automatisch per **GitHub Actions** nach **GHCR** (GitHub
Container Registry).

## Funktionen

- **Shake-Startseite (mobile-first)**: Klasse wählen, Aufteilung per Chips,
  Teams/Je Team per Umschalter, Anzahl per Stepper (− / +) und ein am unteren
  Rand fixierter **SHAKE!**-Button. Beim Öffnen wird die letzte Auslosung der
  Klasse direkt angezeigt, dazu gibt es „Kopieren / Teilen“ und einen
  Aufklapper „Letzte Auslosungen“ (die letzten 5)
- **Admin-Menü** (⚙️ oben rechts): Klassen, Teilnehmer, Verlauf und App-Infos;
  zerstörerische Aktionen liegen abgesetzt in der „Gefahrenzone“
- Mehrere Klassen (Listen) anlegen, umbenennen, löschen
- Namen einzeln eingeben oder als Text-Import (Zeilenumbruch / Komma / Semikolon)
- **Vier Aufteilungsmethoden**: Zufällig, Stärke ausgleichen, Geschlecht mischen,
  Geschlecht trennen (siehe Abschnitt 6, angelehnt an Sportmethodik/Achtergarde)
- Jede Auslosung wird in SQLite gespeichert; pro Klasse bleiben automatisch nur
  die letzten **50 Auslosungen** erhalten (Round-Robin-Löschung der ältesten)
- **Installierbare PWA**: Manifest, SVG-Icons, Service Worker (App-Shell offline
  nutzbar, API-Daten bleiben immer live)
- **Telegram-Bot** (optional): Steuerung per Befehl **und per Inline-Buttons**
  (`/menu`), **Startmeldung** beim Containerstart, gesichert über eine
  Allowlist von Telegram-User-IDs
- Läuft in **einem** Container: Nginx liefert die statischen/PWA-Dateien aus,
  reicht `/api/*` an Gunicorn/Flask weiter, und der Telegram-Bot läuft als
  dritter Supervisor-Prozess daneben

## Bedienung der Web-App

| Bereich | Inhalt |
|---|---|
| **Start (Shake)** | Klasse, Aufteilung (Zufall / Stärke / Mix / Getrennt), Teams oder Spieler pro Team, Stepper, SHAKE!, Ergebnis, Kopieren/Teilen, Letzte Auslosungen |
| **Admin → Klassen** | Klasse auswählen, neu anlegen, umbenennen |
| **Admin → Teilnehmer** | Namen hinzufügen/importieren, Stärke (★1-★5) und Geschlecht je Name, einzelne Namen entfernen |
| **Admin → Verlauf** | Alle gespeicherten Auslosungen der Klasse (bis zu 50) |
| **Admin → App** | Installieren-Button (Android/Chrome) und Hinweise |
| **Gefahrenzone** | Alle Namen löschen, Verlauf löschen, Klasse löschen (jeweils mit Bestätigung) |

Der Admin-Bereich ist unter `/#admin` erreichbar; die Zurück-Taste des Geräts
führt zur Startseite. Die letzten Einstellungen (Klasse, Aufteilung, Modus,
Anzahl) merkt sich die App pro Gerät im `localStorage`.
Sind in einer Klasse weniger als 2 Namen hinterlegt, ist SHAKE! deaktiviert und
ein Hinweis führt direkt zu **Admin → Teilnehmer**.

## Repo-Struktur

```
trt.ShakeyMakey/
├── app.py                        # Flask-Backend (API + SQLite)
├── bot.py                        # Telegram-Bot (nutzt dieselbe API)
├── requirements.txt
├── Dockerfile                    # Ein Image: Nginx + Gunicorn + Bot via Supervisor
├── docker/
│   ├── nginx.conf
│   └── supervisord.conf
├── static/
│   ├── index.html                # Frontend (PWA-fähig): Shake-Start + Admin
│   ├── manifest.json
│   ├── service-worker.js
│   └── icons/
│       ├── icon-192.svg
│       └── icon-512.svg
├── docker-compose.yml            # Für Portainer: zieht Image von GHCR
├── docker-compose.dev.yml        # Für lokalen Build/Test
├── .dockerignore
└── .github/workflows/docker-publish.yml   # Build & Push nach GHCR
```

## 1. Automatischer Build & Push nach GHCR

Der Workflow `.github/workflows/docker-publish.yml` läuft automatisch bei:

- jedem Push auf `main` → Tag `latest` + Kurz-SHA
- jedem Git-Tag `v*.*.*` (z. B. `v1.0.0`) → zusätzlich Versions-Tag
- manuellem Start über „Run workflow“ in der Actions-Oberfläche

Er nutzt den eingebauten `GITHUB_TOKEN` (kein zusätzliches Secret nötig),
baut das Image für `linux/amd64` **und** `linux/arm64` und pusht es nach:

```
ghcr.io/jbkunama1/trt.shakeymakey:latest
```

> **Hinweis zu Groß-/Kleinschreibung:** Das Repo heißt `trt.ShakeyMakey`,
> aber Container-Images bei GHCR müssen komplett kleingeschrieben sein.
> Der Workflow verwendet deshalb fest `trt.shakeymakey` als Image-Namen —
> das ist so beabsichtigt und muss nicht angepasst werden.

Nach dem ersten erfolgreichen Lauf muss das Package ggf. einmalig in GitHub
unter *Packages* auf **public** gestellt werden (oder ein Registry-Login in
Portainer hinterlegt werden, falls es privat bleiben soll, siehe Schritt 3).

## 2. Image manuell testen (optional)

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

Danach unter `http://<server-ip>:8091` erreichbar.

## 3. Deployment per Portainer (GHCR-Image)

1. `docker-compose.yml` ist bereits mit deinem GitHub-Benutzernamen
   (`jbkunama1`) vorbelegt:
   ```yaml
   image: ghcr.io/jbkunama1/trt.shakeymakey:latest
   ```
2. Falls das GHCR-Package **privat** ist: In Portainer unter
   *Registries → Add registry → Custom* einen Registry-Eintrag für
   `ghcr.io` mit GitHub-Benutzername + einem Personal Access Token
   (Scope `read:packages`) anlegen.
3. In Portainer: **Stacks → Add stack → Repository** (auf dieses Repo
   verweisen) **oder** *Web editor* und den Inhalt von `docker-compose.yml`
   einfügen.
4. Unter **Environment variables** bei Bedarf die Telegram-Variablen setzen
   (siehe Abschnitt 5). Die Compose-Datei liest sie über `${VAR:-Default}`,
   ohne Wert gelten die Standardwerte.
5. Deploy klicken. Die App ist danach unter Port **8091** erreichbar
   (anpassbar in der Compose-Datei).
6. Für Updates: In Portainer beim Stack **„Re-pull image and redeploy“**
   nutzen, sobald ein neuer Actions-Lauf ein aktuelles `latest`-Image
   gepusht hat.

Die SQLite-Datenbank liegt im benannten Volume `shakeymakey_data` unter
`/data/teamshake.db` und übersteht Redeploys/Updates. Neben der Datenbank
legt die App dort eine Lock-Datei (`teamshake.db.lock`) und – wegen
WAL-Modus – die Dateien `teamshake.db-wal` und `teamshake.db-shm` an. Für
Backups das gesamte Volume sichern (oder alle zusammengehörigen Dateien
kopieren).

## 4. Als App installieren (PWA)

- **Android/Chrome:** Seite öffnen → ⚙️ **Admin → App → „📲 Installieren“**
  (oder Browser-Menü → „App installieren“).
- **iOS/Safari:** Seite öffnen → Teilen-Symbol → „Zum Home-Bildschirm“.
- Nach der Installation läuft ShakeyMakey wie eine native App (eigenes Icon,
  kein Browser-Rahmen), die App-Oberfläche wird vom Service Worker gecacht
  und funktioniert auch bei schwacher Verbindung – die eigentlichen Daten
  (Klassen, Namen, Auslosungen) kommen aber immer live vom Server, damit sie
  konsistent bleiben.

> **Nach einem Update** kann der erste Aufruf noch die alte Oberfläche aus dem
> Cache zeigen (Cache-first). Seite einmal neu laden bzw. die installierte
> App schließen und neu öffnen. Bei größeren Frontend-Änderungen wird
> `CACHE_NAME` in `static/service-worker.js` hochgezählt.

## 5. Telegram-Bot einrichten (optional)

ShakeyMakey lässt sich zusätzlich per Telegram steuern. Der Bot läuft
als dritter Prozess **im selben Container** (via Supervisor) und spricht intern
mit der Flask-API (`http://127.0.0.1:5000/api`) - es ist also kein separater
Container nötig.

### Bot bei BotFather anlegen

1. In Telegram mit `@BotFather` chatten → `/newbot` → Namen vergeben.
2. Den erhaltenen Token kopieren (Format `123456789:AA...`).

### Variablen als Portainer-Stack-Variablen setzen

Im Stack unter **Environment variables** eintragen (der Token steht damit nicht
im Stack-Text):

| Variable | Beispiel | Wirkung |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `123456789:AA...` | Token von BotFather. Leer = der Bot startet nicht (der Container läuft trotzdem normal weiter). |
| `TELEGRAM_ALLOWED_IDS` | `111111111,222222222` | Kommagetrennte erlaubte Telegram-User-IDs. Leer = **niemand** autorisiert (Fail-Closed). Gruppen-IDs sind negativ. |
| `TELEGRAM_STARTUP_NOTIFY` | `1` | Startmeldung an alle erlaubten IDs. `0` schaltet sie ab (Standard `1`). |
| `TZ` | `Europe/Berlin` | Zeitzone für die Uhrzeit in der Startmeldung (Standard `Europe/Berlin`). |

### Erster Start: eigene Telegram-ID herausfinden

1. Stack zunächst nur mit `TELEGRAM_BOT_TOKEN` deployen, `TELEGRAM_ALLOWED_IDS`
   leer lassen.
2. Dem Bot in Telegram `/id` senden - das funktioniert auch **ohne**
   Autorisierung und zeigt genau die ID an, die eingetragen werden muss.
3. ID bei `TELEGRAM_ALLOWED_IDS` eintragen und den Stack neu deployen
   (*Update the stack*).

### Startmeldung und Inline-Menü

Beim Start wartet der Bot kurz, bis die API antwortet, und schickt dann an jede
erlaubte ID die Nachricht **„🟢 ShakeyMakey Bot gestartet“** mit Uhrzeit,
aktiver Klasse und dem Hauptmenü. Ist die API nicht erreichbar, kommt ein
Warnhinweis ohne Buttons. Eine ID, die dem Bot noch nie geschrieben hat, kann
Telegram nicht anschreiben (HTTP 403) - das wird nur im Container-Log vermerkt
(`Startmeldung an … fehlgeschlagen`) und stoppt den Bot nicht.
Startet supervisord den Bot nach einem Absturz neu, kommt die Meldung erneut.

Das Menü erreichst du über die Startmeldung, `/menu` oder `/start`:

| Button | Funktion |
|---|---|
| 🎲 Shake | Aufteilung wählen, dann 2-6 Teams oder 2-6 Spieler je Team; Ergebnis mit 🔁 Nochmal und 🏠 Menü |
| 👥 Namen | Namen der aktiven Klasse (inkl. Stärke/Geschlecht) |
| 📚 Klassen | Aktive Klasse per Klick wechseln (✅ markiert die aktive) |
| 📜 Verlauf | Die letzten 3 Auslosungen |
| ❓ Hilfe | Befehlsübersicht |

Die Buttons ändern die bestehende Nachricht, es entsteht kein Chat-Spam. Auch
unter den Ergebnissen von `/shake` und `/groesse` erscheinen die Buttons
„Nochmal“ und „Menü“. Button-Aktionen sind wie die Befehle auf die erlaubten IDs
beschränkt; alle anderen erhalten „Nicht autorisiert“.

### Verfügbare Befehle

| Befehl | Wirkung |
|---|---|
| `/id` | Eigene Telegram-ID anzeigen (immer erlaubt) |
| `/start`, `/hilfe` | Befehlsübersicht (mit Menü-Buttons für erlaubte IDs) |
| `/menu` | Hauptmenü mit Inline-Buttons |
| `/klassen` | Alle Klassen auflisten |
| `/klasse <Name>` | Klasse aktivieren oder neu anlegen |
| `/namen` | Namen der aktiven Klasse anzeigen (inkl. Stärke/Geschlecht) |
| `/add <Name1, Name2, ...>` | Namen hinzufügen (kommagetrennt) |
| `/entfernen <Name>` | Einen Namen aus der aktiven Klasse entfernen |
| `/leeren` | Alle Namen der aktiven Klasse löschen |
| `/staerke <Name> <1-5>` | Spielstärke einer Person setzen |
| `/geschlecht <Name> <m/w/d>` | Geschlecht einer Person setzen |
| `/shake <Anzahl> [zufall/staerke/geschlecht/trennen]` | Teams nach Anzahl auslosen |
| `/groesse <ProTeam> [zufall/staerke/geschlecht/trennen]` | Teams nach Gruppengröße auslosen |
| `/verlauf [n]` | Letzte n Auslosungen anzeigen (Standard 5, max. 20) |

Beim Bot werden die Befehle beim Start über `set_my_commands` registriert und
erscheinen im Telegram-Befehlsmenü. Namen und Fehlertexte werden HTML-escaped,
Sonderzeichen wie `<` oder `&` in Namen sind also unkritisch.

Jede per Bot ausgelöste Auslosung landet in derselben SQLite-Datenbank und
demselben Round-Robin-Verlauf (max. `TEAMSHAKE_MAX_DRAWS`) wie die Web-App -
Web-Oberfläche und Telegram greifen auf identische Daten zu.

> Welche Klasse pro Chat gerade aktiv ist, merkt sich der Bot nur im
> Arbeitsspeicher. Nach einem Container-Neustart ist wieder die erste Klasse
> aktiv - über das Menü (📚 Klassen) oder `/klasse <Name>` erneut wählen.

## 6. Team-Aufteilungsmethoden (Sportmethodik / Achtergarde)

Zusätzlich zur reinen Zufallsauslosung unterstützt ShakeyMakey drei weitere,
in der Sportpädagogik dokumentierte Methoden zur fairen Teambildung (u. a.
beschrieben im Sportmethodenhandbuch von Frank Achtergarde sowie in der
Team-Bildungs-Theorie auf sportunterricht.ch) - jeweils digital nachgebildet,
um die klassischen Nachteile (z. B. Bloßstellung leistungsschwacher Schüler
beim „Wählen lassen“) zu vermeiden:

| Modus | Beschreibung | Voraussetzung |
|---|---|---|
| 🎲 Zufällig | Reine Zufallsverteilung (Standard) | - |
| 💪 Stärke ausgleichen | Schlangenverteilung nach hinterlegter Spielstärke (1-5), sodass jedes Team eine ähnliche Gesamtstärke hat | Stärke pro Name optional hinterlegt (Standard: 3) |
| ⚧ Geschlecht mischen | Verteilt Jungen/Mädchen/Divers gleichmäßig auf alle Teams (wie beim „Team Creator“) | Geschlecht bei mind. einem Namen hinterlegt |
| 🚹 Geschlecht trennen | Bildet eigene, geschlechtsreine Teams (z. B. reine Jungen- und reine Mädchenteams) | Geschlecht bei mind. einem Namen hinterlegt |

In der Web-Oberfläche lassen sich Stärke (★1-★5) und Geschlecht je Person unter
**Admin → Teilnehmer** per Dropdown setzen; im Telegram-Bot über `/staerke`
und `/geschlecht`. Beim Auslosen (Start-Seite, Bot-Menü bzw. `/shake`,
`/groesse`) wird die gewünschte Methode einfach mit ausgewählt/angehängt.

> **Geschlecht trennen:** Die gewählte Zahl gilt **pro Gruppe**. Bei „3 Teams“
> und Jungen + Mädchen entstehen also 6 Teams (3 Jungen-, 3 Mädchenteams);
> bei „Je Team“ gilt die Teamgröße je Gruppe.

> Wird „Geschlecht mischen“ oder „Geschlecht trennen“ gewählt, aber bei
> keiner Person ein Geschlecht hinterlegt, meldet die App/der Bot einen
> Hinweis statt eine sinnlose Verteilung durchzuführen.

## API-Übersicht

| Methode | Pfad | Zweck |
|---|---|---|
| GET | /api/classes | Klassen auflisten |
| POST | /api/classes | Klasse anlegen `{name}` |
| PUT | /api/classes/<id> | Klasse umbenennen `{name}` (404 bei unbekannter ID) |
| DELETE | /api/classes/<id> | Klasse löschen |
| GET | /api/classes/<id>/students | Namen einer Klasse (inkl. strength, gender) |
| POST | /api/classes/<id>/students | Namen hinzufügen `{name}` oder `{names:[...]}` (Strings oder `{name, strength, gender}`-Objekte); 404 bei unbekannter Klasse |
| PUT | /api/students/<id> | Name/Stärke/Geschlecht einer Person ändern (404 bei unbekannter ID) |
| DELETE | /api/students/<id> | Einzelnen Namen löschen |
| DELETE | /api/classes/<id>/students | Alle Namen einer Klasse löschen |
| POST | /api/classes/<id>/shake | Auslosung `{mode: count oder size, param: n, balance: random, strength, gender_mixed oder gender_separate}`; ungültiger `mode`/`param` ergibt 400 |
| GET | /api/classes/<id>/history | Letzte Auslosungen (max. `TEAMSHAKE_MAX_DRAWS`) |
| DELETE | /api/classes/<id>/history | Verlauf einer Klasse löschen |
| GET | /healthz | Healthcheck (auch vom Docker-Healthcheck genutzt) |

Ungültiges oder fehlendes JSON im Request-Body wird wie ein leerer Body
behandelt und führt zu einer 400-Antwort statt zu einem Serverfehler.

## Umgebungsvariablen

| Variable | Default | Beschreibung |
|---|---|---|
| `TEAMSHAKE_DB` | `/data/teamshake.db` | Pfad zur SQLite-Datei |
| `TEAMSHAKE_MAX_DRAWS` | `50` | Wie viele Auslosungen pro Klasse behalten werden (Round-Robin) |
| `TELEGRAM_BOT_TOKEN` | *(leer)* | Token von @BotFather; leer = Bot deaktiviert |
| `TELEGRAM_ALLOWED_IDS` | *(leer)* | Kommagetrennte Telegram-User-IDs mit Zugriff; leer = niemand autorisiert |
| `TELEGRAM_STARTUP_NOTIFY` | `1` | `0`/`false`/`no`/`off` schaltet die Startmeldung des Bots ab |
| `TZ` | `Europe/Berlin` (in der Compose-Datei) | Zeitzone des Containers (Uhrzeit der Startmeldung) |
| `TEAMSHAKE_INTERNAL_API` | `http://127.0.0.1:5000/api` | Interne API-Adresse des Bots, normalerweise nicht ändern |

## Container-Architektur

Ein einziges Image, gesteuert von **Supervisor**, das drei Prozesse startet:

- **Gunicorn** (Flask-App, 2 Worker) lauscht intern auf `127.0.0.1:5000`. Die
  Datenbank-Initialisierung ist per Dateilock abgesichert, SQLite läuft im
  WAL-Modus
- **Nginx** lauscht extern auf Port `80`, liefert `static/` direkt aus
  (inkl. gzip und Cache-Header für Icons, kein Cache für `service-worker.js`
  und `manifest.json`) und reicht `/api/*` an Gunicorn weiter
- **Telegram-Bot** (`bot.py`) läuft nur, wenn `TELEGRAM_BOT_TOKEN` gesetzt
  ist, spricht intern mit derselben Flask-API und teilt sich damit
  automatisch die SQLite-Datenbank mit der Web-Oberfläche

Supervisor startet alle Prozesse bei Absturz automatisch neu (mit
`startsecs`/`startretries`).

Damit bleibt es bei **einem** Container/Image für Portainer, ganz wie bei
deinen anderen Stacks.

## Sicherheitshinweise

- Die Web-App und die API haben **keine eigene Authentifizierung**. Klassen und
  Schülernamen sind personenbezogene Daten - den Dienst daher nicht ungeschützt
  ins Internet stellen, sondern z. B. hinter einem Reverse-Proxy mit Login
  (etwa Cloudflare Access oder Basic Auth) oder nur im lokalen Netz betreiben.
- Der Telegram-Bot ist über `TELEGRAM_ALLOWED_IDS` abgesichert (Fail-Closed).
  Den Bot-Token nicht ins Repo schreiben, sondern als Portainer-Variable setzen.
