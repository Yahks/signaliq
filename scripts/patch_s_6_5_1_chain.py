"""S.6.5.1 — chain embed → cluster into the ingestion pipeline.

Every new org that goes through ingestion was ending up with records
and enrichments, and nothing downstream. The chain fired only from
normalize → enrich. embed and cluster had to be triggered manually.

Two small patches add the missing links:

  enrich_org ends → embed_org.delay(org_id)
  embed_org ends  → recluster_org.delay(org_id)

Both enqueues are wrapped in try/except so a chaining failure never
breaks the primary task. The primary task succeeds; the chained task
is best-effort.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch_py(path: Path, old: str, new: str, label: str) -> None:
    """Patch a .py file with an explicit syntax check after the write."""
    src = path.read_text()
    if new in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(
            f"{path.name}: pattern not found for '{label}'\n"
            f"--- expected (first 400 chars) ---\n{old[:400]}"
        )
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: wrote but new text not found on re-read")
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"{path.name}: syntax error after '{label}': {e}")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. enrich.py — after enrich succeeds, enqueue embed
# ─────────────────────────────────────────────────────────────────────

def step1_enrich() -> None:
    print("\n[1/2] enrich.py")
    p = ROOT / "apps" / "worker" / "tasks" / "enrich.py"
    patch_py(
        p,
        '''    await engine.dispose()
    return {"processed": processed, "failed": failed, "total": len(rows)}


@celery_app.task(name="tasks.enrich_org", bind=True)''',
        '''    await engine.dispose()

    # S.6.5.1 — chain: after enrich completes, embed the records. Then
    # embed will chain to cluster. Best-effort: a chaining failure never
    # breaks the enrichment task itself.
    if processed > 0:
        try:
            from apps.worker.tasks.embed import embed_org
            embed_org.delay(str(org_id))
            log.info("enrich.chained_embed", org_id=org_id[:8], processed=processed)
        except Exception as exc:
            log.warning("enrich.chain_embed_failed", error=str(exc)[:200])

    return {"processed": processed, "failed": failed, "total": len(rows)}


@celery_app.task(name="tasks.enrich_org", bind=True)''',
        "chain embed after enrich",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. embed.py — after embed succeeds, enqueue cluster
# ─────────────────────────────────────────────────────────────────────

def step2_embed() -> None:
    print("\n[2/2] embed.py")
    p = ROOT / "apps" / "worker" / "tasks" / "embed.py"
    patch_py(
        p,
        '''    await engine.dispose()
    return {"processed": processed, "failed": failed, "total": len(rows)}


@celery_app.task(name="tasks.embed_org", bind=True)''',
        '''    await engine.dispose()

    # S.6.5.1 — chain: after embed completes, recluster the org's records
    # so clusters are rebuilt with the new vectors. Best-effort.
    if processed > 0:
        try:
            from apps.worker.tasks.cluster import recluster_org
            recluster_org.delay(str(org_id))
            log.info("embed.chained_cluster", org_id=org_id[:8], processed=processed)
        except Exception as exc:
            log.warning("embed.chain_cluster_failed", error=str(exc)[:200])

    return {"processed": processed, "failed": failed, "total": len(rows)}


@celery_app.task(name="tasks.embed_org", bind=True)''',
        "chain cluster after embed",
    )


def main() -> None:
    print("=" * 70)
    print("S.6.5.1 — chain embed → cluster into the ingestion pipeline")
    print("=" * 70)

    step1_enrich()
    step2_embed()

    print()
    print("=" * 70)
    print("Ingestion chain now:")
    print("  ingest → normalize → enrich → embed → cluster")
    print("Every new org that goes through ingestion will be clustered")
    print("automatically.")
    print("=" * 70)


if __name__ == "__main__":
    main()
