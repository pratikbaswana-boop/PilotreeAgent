"""Recompute domain warnings after upgrading the historical-contact check.

Run from backend: .venv/bin/python scripts/refresh_domain_warnings.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.config import Settings
from app.domain.models import Enquiry
from app.domain.warnings import compute_db_warnings
from app.infra.db import create_db


async def main():
    db = create_db(Settings())
    changed = 0
    try:
        async with db.session_factory() as session, session.begin():
            rows = (await session.scalars(select(Enquiry).with_for_update())).all()
            for row in rows:
                mismatch, _ = await compute_db_warnings(
                    session,
                    enquiry_id=row.id,
                    email=row.email,
                    company=row.company,
                )
                if row.warning_domain_mismatch != mismatch:
                    row.warning_domain_mismatch = mismatch
                    row.version += 1
                    changed += 1
        print(f"Refreshed {len(rows)} enquiries; corrected {changed} domain warnings.")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
