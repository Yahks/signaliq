# SignalIQ — Reviewer Setup

## Prerequisites
- Docker Desktop, or Docker Engine + Compose v2
- ~10 GB free disk space

## Steps

### 1. Clone the repo
    git clone https://github.com/Yahks/signaliq.git
    cd signaliq

### 2. Environment
Receive `.env` from the sender (separate channel). Save it at the repo root:
    cp /path/to/received/.env .env

### 3. Build and start the stack
    docker compose build
    docker compose up -d
    sleep 30

### 4. Restore the demo database
Receive `signaliq_for_reviewer.sql` (separate channel). Restore it:
    docker compose exec -T postgres psql -U signaliq -d signaliq < signaliq_for_reviewer.sql

The dump contains:
- 944 trading posts (drafts + published)
- 18 subblocks configured
- 18 users, 1 active org (demo-4d519a)
- Full schema; `ai_credentials` is present but **empty** — add keys at `/settings/credentials`

### 5. Access
- Web UI: http://localhost:3000
- API docs: http://localhost:8000/docs

### 6. Log in
- Email: elearningyahaya@gmail.com
- Password: (sent separately)

### 7. Verify the system is running
- `/settings` → Article schedule shows 13 subblocks with intervals
- `/trading` → lists 944 posts
- `/trading/drafts` → shows drafts awaiting review
- `docker compose logs -f worker` → worker processes tasks
- `docker compose logs -f scheduler` → scheduler fires due subblocks
