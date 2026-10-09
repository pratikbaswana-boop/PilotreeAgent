"""Import an enquiry JSON array through the local development API.

Usage: .venv/bin/python scripts/import_enquiries.py /path/to/enquiries.json
Existing identical entries are skipped; differing entries are never overwritten.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.schemas.enquiries import EnquiryIn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.file.read_text())
    if not isinstance(raw, list):
        raise ValueError("Expected a JSON array of enquiries")
    rows = [EnquiryIn.model_validate(row) for row in raw]
    if len({row.id for row in rows}) != len(rows):
        raise ValueError("Duplicate enquiry IDs in input file")
    base = "http://127.0.0.1:8000"

    def request(path: str, method: str = "GET", payload=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(payload).encode() if payload is not None else None
        with urlopen(Request(base + path, data=body, headers=headers, method=method), timeout=30) as response:
            return json.load(response)

    token = request("/auth/local-session", "POST")["access_token"]
    imported = skipped = 0
    from urllib.parse import quote
    for row in rows:
        payload = row.model_dump(mode="json", exclude_none=True)
        path = "/enquiries/" + quote(row.id, safe="")
        try:
            existing = request(path, token=token)
        except HTTPError as error:
            if error.code != 404:
                raise
        else:
            if any(existing.get(key) != value for key, value in payload.items()):
                raise ValueError(f"Existing enquiry {row.id} differs; refusing to overwrite")
            skipped += 1
            continue
        request("/enquiries", "POST", payload, token)
        saved = request(path, token=token)
        if any(saved.get(key) != value for key, value in payload.items()):
            raise ValueError(f"Verification failed for {row.id}")
        imported += 1
    print(f"Imported {imported}; already present {skipped}; verified {len(rows)} enquiries.")


if __name__ == "__main__":
    main()
