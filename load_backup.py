"""
Load financial_news_backup.json into MongoDB.

The backup is a JSON array of mongoexport-style docs:
    {_id: {$oid}, published_dt: {$date}, fetched_at: {$date}, ...}

This script:
  1. Normalizes Mongo extended-JSON ({$oid}, {$date}) to native types
  2. Re-tags `companies` and `sector` via the new alias + sector detector
  3. Computes `title_key` for fuzzy dedup compatibility
  4. Drops Hindi articles
  5. Upserts on `dedup_key` so re-runs are idempotent

Usage:
  python load_backup.py                              # use defaults
  python load_backup.py --file financial_news_backup.json --batch 1000
  python load_backup.py --skip-tagging               # faster, skips re-extraction
  python load_backup.py --retag-only                 # only re-tag existing rows
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from company_aliases import detect_companies, detect_sector
from dedup import title_key, is_hindi

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backup-loader")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "financial_news")
COLLECTION = "articles"


def _convert_doc(doc: dict) -> dict:
    """Mongo extended JSON -> native Python types."""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            continue  # let MongoDB assign fresh _id on insert
        if isinstance(v, dict):
            if "$date" in v:
                # ISO string -> datetime
                try:
                    s = v["$date"]
                    if s.endswith("Z"):
                        s = s.replace("Z", "+00:00")
                    out[k] = datetime.fromisoformat(s)
                except Exception:
                    out[k] = None
            elif "$oid" in v:
                continue  # drop oid
            else:
                out[k] = v
        else:
            out[k] = v
    return out


async def _ensure_indexes(coll):
    try:
        await coll.create_index("dedup_key", unique=True, background=True)
    except Exception:
        pass
    for f in ("link", "fetched_at", "published_dt", "sentiment", "companies", "sector"):
        try:
            await coll.create_index(f, background=True)
        except Exception:
            pass


async def load_backup(path: Path, batch_size: int, skip_tagging: bool):
    log.info(f"Reading {path} ...")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        log.error("Backup is not a JSON array")
        sys.exit(1)
    log.info(f"Loaded {len(data):,} raw records")

    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]
    await _ensure_indexes(coll)

    total = len(data)
    hindi_skipped = 0
    inserted = 0
    updated = 0
    batch_ops = []

    for i, raw in enumerate(data, 1):
        doc = _convert_doc(raw)
        title = doc.get("title", "") or ""
        if is_hindi(title + " " + (doc.get("description") or "")):
            hindi_skipped += 1
            continue

        if not skip_tagging:
            text = f"{title}. {doc.get('description') or ''}"
            companies = detect_companies(text)
            sector = detect_sector(text, companies)
            doc["companies"] = companies
            doc["sector"] = sector
        else:
            doc.setdefault("companies", [])
            doc.setdefault("sector", None)

        doc["title_key"] = title_key(title)
        if not doc.get("dedup_key"):
            # Should be present from backup, but compute defensively
            import hashlib, re
            norm = re.sub(r"\s+", " ", title.lower().strip())
            doc["dedup_key"] = hashlib.md5(norm.encode()).hexdigest()

        batch_ops.append(UpdateOne(
            {"dedup_key": doc["dedup_key"]},
            {"$set": doc},
            upsert=True,
        ))

        if len(batch_ops) >= batch_size:
            result = await coll.bulk_write(batch_ops, ordered=False)
            inserted += result.upserted_count
            updated += result.modified_count
            batch_ops.clear()
            log.info(f"  {i:>7,}/{total:,}  upserted={inserted:,} updated={updated:,} hindi_skipped={hindi_skipped:,}")

    if batch_ops:
        result = await coll.bulk_write(batch_ops, ordered=False)
        inserted += result.upserted_count
        updated += result.modified_count

    log.info("─" * 60)
    log.info(f"DONE  upserted={inserted:,}  updated={updated:,}  hindi_skipped={hindi_skipped:,}")
    final_total = await coll.count_documents({})
    log.info(f"Collection {DB_NAME}.{COLLECTION} now has {final_total:,} docs")
    client.close()


async def retag_only(batch_size: int):
    """Walk every doc, recompute companies + sector + title_key."""
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]
    await _ensure_indexes(coll)

    total = await coll.count_documents({})
    log.info(f"Re-tagging {total:,} docs ...")
    cursor = coll.find({}, {"title": 1, "description": 1, "dedup_key": 1})
    batch_ops = []
    done = 0
    async for doc in cursor:
        text = f"{doc.get('title','')}. {doc.get('description') or ''}"
        if is_hindi(text):
            batch_ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"_drop": True}}))
            continue
        companies = detect_companies(text)
        sector = detect_sector(text, companies)
        batch_ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {
            "companies": companies,
            "sector": sector,
            "title_key": title_key(doc.get("title", "")),
        }}))
        if len(batch_ops) >= batch_size:
            await coll.bulk_write(batch_ops, ordered=False)
            done += len(batch_ops); batch_ops.clear()
            log.info(f"  re-tagged {done:,}/{total:,}")
    if batch_ops:
        await coll.bulk_write(batch_ops, ordered=False)
        done += len(batch_ops)
    # Purge Hindi
    dropped = await coll.delete_many({"_drop": True})
    log.info(f"Re-tagging done. Dropped {dropped.deleted_count:,} Hindi rows.")
    client.close()


def main():
    p = argparse.ArgumentParser(description="Load financial_news_backup.json into MongoDB")
    p.add_argument("--file", default="financial_news_backup.json", help="Backup JSON path")
    p.add_argument("--batch", type=int, default=1000, help="Bulk-write batch size")
    p.add_argument("--skip-tagging", action="store_true", help="Skip company/sector re-extraction (faster)")
    p.add_argument("--retag-only", action="store_true", help="Re-tag existing collection only — do not load file")
    args = p.parse_args()

    if args.retag_only:
        asyncio.run(retag_only(args.batch))
        return

    path = Path(args.file)
    if not path.exists():
        log.error(f"File not found: {path}")
        sys.exit(1)
    asyncio.run(load_backup(path, args.batch, args.skip_tagging))


if __name__ == "__main__":
    main()
