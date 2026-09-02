from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from r3_cli import CliError

from .markdown import restore_translation, segment_document
from .profile import Profile


SCHEMA = "r3translate.bundle/v1"


def read_utf8(path: Path) -> tuple[bytes, str, bool]:
    try:
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        return raw, raw.decode("utf-8-sig"), bom
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError(f"Document '{path}' is not readable UTF-8.", code="R3Translate.Document.Unreadable", details=str(exc), hint="provide an UTF-8 Markdown file", exit_code=2) from exc


def create_bundle(path: Path, profile: Profile) -> dict[str, Any]:
    raw, text, bom = read_utf8(path)
    return {
        "schema": SCHEMA,
        "original": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "utf8_bom": bom},
        "profile": {"path": str(profile.path), "sha256": profile.sha256},
        "language": {"source": profile.source_language, "target": profile.target_language},
        "segments": [segment.as_dict() for segment in segment_document(text, profile)],
    }


def atomic_write(path: Path, data: bytes, *, force: bool) -> None:
    if path.exists() and not force:
        raise CliError(f"Destination '{path}' already exists.", code="R3Translate.Output.Exists", hint="choose another path or add --force", exit_code=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_bundle(bundle: dict[str, Any], output: Path, *, force: bool = False) -> None:
    payload = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(output, payload, force=force)


def read_bundle(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"Bundle '{path}' is not valid JSON.", code="R3Translate.Bundle.Invalid", details=str(exc), hint="regenerate it with extract", exit_code=2) from exc
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or not isinstance(value.get("segments"), list):
        raise CliError(f"Bundle '{path}' does not use {SCHEMA}.", code="R3Translate.Bundle.Schema", hint="regenerate it with this R3Translate version", exit_code=2)
    return value


def apply_bundle(original: Path, profile: Profile, bundle: dict[str, Any]) -> bytes:
    raw, text, bom = read_utf8(original)
    if hashlib.sha256(raw).hexdigest() != bundle.get("original", {}).get("sha256"):
        raise CliError("The original document has changed since extraction.", code="R3Translate.Apply.OriginalChanged", hint="run extract again", exit_code=4)
    if profile.sha256 != bundle.get("profile", {}).get("sha256"):
        raise CliError("The translation profile has changed since extraction.", code="R3Translate.Apply.ProfileChanged", hint="run extract again", exit_code=4)
    if bom != bool(bundle.get("original", {}).get("utf8_bom")):
        raise CliError("The original document encoding marker has changed.", code="R3Translate.Apply.EncodingChanged", hint="run extract again", exit_code=4)
    edits: list[tuple[int, int, str]] = []
    previous_end = -1
    for item in bundle["segments"]:
        if not isinstance(item, dict):
            raise CliError("The bundle contains an invalid segment.", code="R3Translate.Apply.Bundle", hint="run extract again", exit_code=4)
        start, end = item.get("start"), item.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < previous_end or text[start:end] != item.get("source"):
            raise CliError(f"Segment '{item.get('id', '?')}' no longer matches the document.", code="R3Translate.Apply.SegmentMismatch", hint="run extract again", exit_code=4)
        previous_end = end
        translation = item.get("translation")
        if translation is None or translation == "":
            replacement = item["source"]
        elif not isinstance(translation, str):
            raise CliError(f"Segment '{item.get('id', '?')}' has no text translation.", code="R3Translate.Apply.Translation", hint="repair the bundle", exit_code=4)
        else:
            try:
                replacement = restore_translation(translation, item.get("protections", []))
            except (KeyError, TypeError, ValueError) as exc:
                raise CliError(f"Segment '{item.get('id', '?')}' lost a protected marker.", code="R3Translate.Apply.Marker", details=str(exc), hint="restore every marker exactly once", exit_code=4) from exc
        edits.append((start, end, replacement))
    candidate = text
    for start, end, replacement in reversed(edits):
        candidate = candidate[:start] + replacement + candidate[end:]
    return (b"\xef\xbb\xbf" if bom else b"") + candidate.encode("utf-8")

