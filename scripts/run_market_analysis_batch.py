"""Generate N market_analysis articles in a batch and print the
failure distribution from market_analysis_rejections.
"""
import sys
import os

# Ensure /app is on sys.path when running from /app/scripts/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import argparse
import asyncio
from collections import Counter

from sqlalchemy import text as sql_text

from packages.core.context import current_org_id, current_user_id
from packages.db.session import get_session_factory
from packages.db.tenant import set_current_org
from packages.trading.subblocks.base import generate_subblock


async def main(org_id: str, user_id: str, count: int) -> None:
    current_org_id.set(org_id)
    current_user_id.set(user_id)
    factory = get_session_factory()

    published = 0
    drafted = 0
    rejected = 0
    rejections: Counter = Counter()

    for i in range(count):
        async with factory() as s:
            await set_current_org(s, org_id)
            try:
                r = await generate_subblock(
                    s, org_id=org_id, slug="market_analysis",
                )
            except Exception as exc:
                print(f"[{i+1}/{count}] EXCEPTION: {str(exc)[:120]}")
                rejected += 1
                continue

            status = r.get("status") if r else "none"
            if status == "ok":
                post_id = r.get("post_id", "")
                row = (await s.execute(sql_text(
                    "SELECT status FROM trading_posts WHERE id = :pid"
                ), {"pid": post_id})).first()
                post_status = row[0] if row else "unknown"
                if post_status == "published":
                    published += 1
                else:
                    drafted += 1
                print(f"[{i+1}/{count}] OK  {r.get('symbol')}  "
                      f"{r.get('word_count')}w  {post_status}  {post_id[:8]}")
            elif status == "article_rejected":
                rejected += 1
                reason = (r.get("errors") or ["unknown"])[0]
                rejections[reason] += 1
                print(f"[{i+1}/{count}] REJ  {r.get('symbol')}  {reason}")
            elif status == "block_invalid":
                rejected += 1
                reason = (r.get("errors") or ["unknown"])[0]
                rejections[f"block_invalid:{reason}"] += 1
                print(f"[{i+1}/{count}] BLK  {r.get('symbol')}  {reason}")
            else:
                rejected += 1
                rejections[f"other:{status}"] += 1
                print(f"[{i+1}/{count}] {status.upper()}  {r.get('symbol', '?')}")

    print()
    print(f"Batch complete. {count} attempted.")
    print(f"  {published} published")
    print(f"  {drafted} drafted")
    print(f"  {rejected} rejected")
    print()
    print("Rejection reasons:")
    for reason, n in rejections.most_common():
        print(f"  {n:3d}  {reason}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--org", default=os.environ.get(
        "BATCH_ORG", "0d8a3d61-d8aa-4fbc-bd92-31b3a1bbd7d2"))
    parser.add_argument("--user", default=os.environ.get(
        "BATCH_USER", "f7bd5110-879f-45e0-adc9-0a81d31b0bba"))
    args = parser.parse_args()
    asyncio.run(main(args.org, args.user, args.count))
