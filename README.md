# trt.ShakeyMakey 🎲

Mobile-optimierte **PWA** zum zufälligen Auslosen von Teams (Klon von "Team Shake").
Persistente Datenhaltung in **SQLite**, ausgeliefert als **ein eigenständiger
Docker-Container** (Nginx + Gunicorn/Flask via Supervisor), Build & Publish
automatisch per **GitHub Actions** nach **GHCR** (GitHub Container Registry).

## Funktionen

- Mehrere Klassen (Listen) anlegen, umbenennen, löschen
- Namen einzeln eingeben oder als Text-Import (Zeilenumbruch / Komma / Semikolon)
- Teams per Zufall auslosen: Modus "Anzahl Teams" oder "Teilnehmer pro Team"
- Jede Auslosung wird in SQLite gespeichert; pro Klasse bleiben automatisch nur
  die letzten **50 Auslosungen** erhalten (Round-Robin-Löschung der ältesten)
- Verlaufs-Ansicht mit Zeitstempel je Auslosung
- **Installierbare PWA**: Manifest, SVG-Icons, Service Worker (App-Shell offline
  nutzbar, API-Daten bleiben immer live)
- Läuft in **einem** Container: Nginx liefert die statischen/PWA-Dateien aus
  und reicht `/api/*`-Aufrufe intern an Gunicorn/Flask weiter

## Repo-Struktur

```
trt.ShakeyMakey/
├── app.py                        # Flask-Backend (API + SQLite)
├── requirements.txt
├── Dockerfile                    # Ein Image: Nginx + Gunicorn via Supervisor
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

## Container-Architektur

Ein einziges Image, gesteuert von **Supervisor**, das zwei Prozesse startet:

- **Gunicorn** (Flask-App) lauscht intern auf `127.0.0.1:5000`
- **Nginx** lauscht extern auf Port `80`, liefert `static/` direkt aus
  (inkl. Cache-Header für Icons, kein Cache für `service-worker.js` und
  `manifest.json`) und reicht `/api/*` an Gunicorn weiter

Damit bleibt es bei **einem** Container/Image für Portainer, ganz wie bei
deinen anderen Stacks.
