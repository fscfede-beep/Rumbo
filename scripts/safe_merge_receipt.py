import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_payload(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def receipt_digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(data)).hexdigest()


def write_receipt(directory: Path, data: dict[str, Any]) -> Path:
    digest = receipt_digest(data)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    envelope = {"sha256": digest, "payload": data}
    encoded = canonical_payload(envelope)
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.write(b"\n")
    return path
