# How to start SignalIQ locally

## 0. IMPORTANT: Fix Postgres port binding first (Kali+Docker bug)

    cd ~/Desktop/signaliq
    docker compose down postgres
    docker compose up -d postgres
    sleep 10
    docker port signaliq-postgres-1   # MUST show 0.0.0.0:5432->5432/tcp

## 1. Start API

    cd ~/Desktop/signaliq
    source .venv/bin/activate
    nohup uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 > /tmp/signaliq-api.log 2>&1 &

## 2. Start Celery worker

    cd ~/Desktop/signaliq
    source .venv/bin/activate
    nohup celery -A apps.worker.celery_app worker --loglevel=info -Q default,ingestion --hostname=worker1@%h > /tmp/signaliq-worker.log 2>&1 &

## 3. Start Celery Beat (scheduler)

    cd ~/Desktop/signaliq
    source .venv/bin/activate
    rm -f /tmp/celerybeat-schedule*
    nohup celery -A apps.worker.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule > /tmp/signaliq-beat.log 2>&1 &

## 4. Start Next.js

    cd ~/Desktop/signaliq/apps/web
    nohup npm run dev > /tmp/signaliq-web.log 2>&1 &

## 5. Verify

    curl -s http://localhost:8000/health
    curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/dashboard
    docker port signaliq-postgres-1
    tail -3 /tmp/signaliq-beat.log

## Scheduler behavior

The stock news fetcher runs:
- Every 30 min during US market hours (13:30-20:00 UTC Mon-Fri)
- Every 2 hours outside market hours
- Nightly cluster rebuild at 02:00 UTC

Each fetch auto-attaches new events to existing chains where relevant,
and creates new chains from unattached events.

## Stop everything

    pkill -9 -f "celery|uvicorn|next"
    cd ~/Desktop/signaliq && docker compose down postgres
