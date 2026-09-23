# trt.ShakeyMakey 🎲

Mobile-optimierte **PWA** zum zufälligen Auslosen von Teams (Klon von "Team Shake").
Persistente Datenhaltung in **SQLite**, ausgeliefert als **ein eigenständiger
Docker-Container** (Nginx + Gunicorn/Flask + Telegram-Bot via Supervisor),
Build & Publish automatisch per **GitHub Actions** nach **GHCR** (GitHub
Container Registry).

## Funktionen

- Mehrere Klassen (Listen) anlegen, umbenennen, löschen
- Namen einzeln eingeben oder als Text-Import (Zeilenumbruch / Komma / Semikolon)
- Teams per Zufall auslosen: Modus "Anzahl Teams" oder "Teilnehmer pro Team"
- Jede Auslosung wird in SQLite gespeichert; pro Klasse bleiben automatisch nur
  die letzten **50 Auslosungen** erhalten (Round-Robin-Löschung der ältesten)
- Verlaufs-Ansicht mit Zeitstempel je Auslosung
- **Installierbare PWA**: Manifest, SVG-Icons, Service Worker (App-Shell offline
  nutzbar, API-Daten bleiben immer live)
- **Telegram-Bot** (optional): komplette Steuerung per Chat-Befehl, gesichert
  über eine Allowlist von Telegram-User-IDs
- Läuft in **einem** Container: Nginx liefert die statischen/PWA-Dateien aus,
  reicht `/api/*` an Gunicorn/Flask weiter, und der Telegram-Bot läuft als
  dritter Supervisor-Prozess daneben

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
│   ├── index.html                # Frontend (PWA-fähig)
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
- manuellem Start über "Run workflow" in der Actions-Oberfläche

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
4. Deploy klicken. Die App ist danach unter Port **8091** erreichbar
   (anpassbar in der Compose-Datei).
5. Für Updates: In Portainer beim Stack **"Re-pull image and redeploy"**
   nutzen, sobald ein neuer Actions-Lauf ein aktuelles `latest`-Image
   gepusht hat.

Die SQLite-Datenbank liegt im benannten Volume `shakeymakey_data` unter
`/data/teamshake.db` und übersteht Redeploys/Updates. Für Backups reicht es,
dieses Volume zu sichern.

## 4. Als App installieren (PWA)

- **Android/Chrome:** Seite öffnen → oben rechts erscheint automatisch der
  Button **"📲 Installieren"** (oder Browser-Menü → "App installieren").
- **iOS/Safari:** Seite öffnen → Teilen-Symbol → "Zum Home-Bildschirm".
- Nach der Installation läuft ShakeyMakey wie eine native App (eigenes Icon,
  kein Browser-Rahmen), die App-Oberfläche wird vom Service Worker gecacht
  und funktioniert auch bei schwacher Verbindung – die eigentlichen Daten
  (Klassen, Namen, Auslosungen) kommen aber immer live vom Server, damit sie
  konsistent bleiben.

## 5. Telegram-Bot einrichten (optional)

ShakeyMakey lässt sich zusätzlich komplett per Telegram steuern. Der Bot läuft
als dritter Prozess **im selben Container** (via Supervisor) und spricht intern
mit der Flask-API (`http://127.0.0.1:5000/api`) - es ist also kein separater
Container nötig.

### Bot bei BotFather anlegen

1. In Telegram mit `@BotFather` chatten → `/newbot` → Namen vergeben.
2. Den erhaltenen Token kopieren (Format `123456789:AA...`).

### Umgebungsvariablen in Portainer setzen

In der `docker-compose.yml` bzw. im Portainer-Stack folgende Variablen
ausfüllen:

```yaml
environment:
  - TELEGRAM_BOT_TOKEN=123456789:AA...dein-token...
  - TELEGRAM_ALLOWED_IDS=111111111,222222222
```

- `TELEGRAM_BOT_TOKEN`: Token von BotFather. Bleibt er leer, startet der Bot
  gar nicht erst (der Container läuft trotzdem normal weiter).
- `TELEGRAM_ALLOWED_IDS`: kommagetrennte Liste erlaubter Telegram-User-IDs.
  Bleibt sie leer, antwortet der Bot **allen** Nutzern nur mit "nicht
  autorisiert" (Fail-Closed, kein Vollzugriff by default).

### Eigene Telegram-ID herausfinden

Nach dem Deploy den Bot in Telegram anschreiben und `/id` senden - das
funktioniert auch **ohne** Autorisierung und zeigt genau die ID an, die in
`TELEGRAM_ALLOWED_IDS` eingetragen werden muss. Danach den Stack in Portainer
mit der ergänzten ID neu deployen (Environment-Wert ändern → Update the stack).

### Verfügbare Befehle

| Befehl | Wirkung |
|---|---|
| `/id` | Eigene Telegram-ID anzeigen (immer erlaubt) |
| `/hilfe` | Befehlsübersicht |
| `/klassen` | Alle Klassen auflisten |
| `/klasse <Name>` | Klasse aktivieren oder neu anlegen |
| `/namen` | Namen der aktiven Klasse anzeigen |
| `/add <Name1, Name2, ...>` | Namen hinzufügen (kommagetrennt) |
| `/entfernen <Name>` | Einen Namen aus der aktiven Klasse entfernen |
| `/leeren` | Alle Namen der aktiven Klasse löschen |
| `/shake <Anzahl Teams>` | Teams zufällig auslosen (Modus "Anzahl") |
| `/groesse <Spieler pro Team>` | Teams nach Gruppengröße auslosen |
| `/verlauf [n]` | Letzte n Auslosungen anzeigen (Standard 5, max. 20) |

Jede per Bot ausgelöste Auslosung landet in derselben SQLite-Datenbank und
demselben Round-Robin-Verlauf (max. `TEAMSHAKE_MAX_DRAWS`) wie die Web-App -
Web-Oberfläche und Telegram greifen auf identische Daten zu.

> Welche Klasse pro Chat gerade aktiv ist, merkt sich der Bot nur im
> Arbeitsspeicher. Nach einem Container-Neustart ist wieder die erste Klasse
> aktiv - mit `/klasse <Name>` einfach erneut wählen.

## API-Übersicht

| Methode | Pfad | Zweck |
|---|---|---|
| GET | /api/classes | Klassen auflisten |
| POST | /api/classes | Klasse anlegen `{name}` |
| PUT | /api/classes/<id> | Klasse umbenennen `{name}` |
| DELETE | /api/classes/<id> | Klasse löschen |
| GET | /api/classes/<id>/students | Namen einer Klasse |
| POST | /api/classes/<id>/students | Namen hinzufügen `{name}` oder `{names:[...]}` |
| DELETE | /api/students/<id> | Einzelnen Namen löschen |
| DELETE | /api/classes/<id>/students | Alle Namen einer Klasse löschen |
| POST | /api/classes/<id>/shake | Auslosung `{mode: "count"|"size", param: n}` |
| GET | /api/classes/<id>/history | Letzte Auslosungen (max. `TEAMSHAKE_MAX_DRAWS`) |
| DELETE | /api/classes/<id>/history | Verlauf einer Klasse löschen |
| GET | /healthz | Healthcheck (auch vom Docker-Healthcheck genutzt) |

## Umgebungsvariablen

| Variable | Default | Beschreibung |
|---|---|---|
| `TEAMSHAKE_DB` | `/data/teamshake.db` | Pfad zur SQLite-Datei |
| `TEAMSHAKE_MAX_DRAWS` | `50` | Wie viele Auslosungen pro Klasse behalten werden (Round-Robin) |
| `TELEGRAM_BOT_TOKEN` | *(leer)* | Token von @BotFather; leer = Bot deaktiviert |
| `TELEGRAM_ALLOWED_IDS` | *(leer)* | Kommagetrennte Telegram-User-IDs mit Zugriff; leer = niemand autorisiert |

## Container-Architektur

Ein einziges Image, gesteuert von **Supervisor**, das drei Prozesse startet:

- **Gunicorn** (Flask-App) lauscht intern auf `127.0.0.1:5000`
- **Nginx** lauscht extern auf Port `80`, liefert `static/` direkt aus
  (inkl. Cache-Header für Icons, kein Cache für `service-worker.js` und
  `manifest.json`) und reicht `/api/*` an Gunicorn weiter
- **Telegram-Bot** (`bot.py`) läuft nur, wenn `TELEGRAM_BOT_TOKEN` gesetzt
  ist, spricht intern mit derselben Flask-API und teilt sich damit
  automatisch die SQLite-Datenbank mit der Web-Oberfläche

Damit bleibt es bei **einem** Container/Image für Portainer, ganz wie bei
deinen anderen Stacks.
