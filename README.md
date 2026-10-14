# SignalIQ — self-hosted

A self-hosted trading-psychology article generator. Runs entirely
on your machine. No data leaves your computer except the LLM calls
you make with your own API key.

## Requirements

- Docker Desktop (Windows/macOS) or Docker + Docker Compose (Linux)
- 4 GB of RAM, 10 GB of free disk space

## Setup

1. Copy `.env.example` to `.env`:

       cp .env.example .env

2. Edit `.env`:
   - Set `SECRET_KEY` to a random 32-character string.
   - Add at least one LLM key: `GROQ_API_KEY` (recommended, free
     tier available at https://console.groq.com/keys).

3. Start the stack:

       docker compose up -d

   The first run builds images and runs database migrations. It
   takes 2–3 minutes.

4. Open http://localhost:3000

5. Sign up. Then go to Settings → API Credentials and paste your
   LLM key (or leave it if you set `GROQ_API_KEY` in `.env`).

6. Go to Settings → Article schedule. Enable it and set the
   interval.

## Updating

       docker compose pull
       docker compose up -d

Data lives in the `pg_data` Docker volume. It survives updates.

## Stopping

       docker compose down

## Backing up

       docker compose exec postgres pg_dump -U signaliq signaliq > backup.sql

## Logs

       docker compose logs -f api
       docker compose logs -f worker
       docker compose logs -f scheduler

## Troubleshooting

- **Port already in use**: something else is on 3000, 8000, 5432,
  or 6379. Change the port mapping in `docker-compose.yml`.
- **Generation fails**: no LLM key configured. Set one in `.env`
  or in Settings → API Credentials.
- **No articles**: enable the schedule in Settings → Article
  schedule, or click `Generate now` on a subblock page.
