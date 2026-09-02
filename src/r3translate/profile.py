from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from r3_cli import CliError


@dataclass(frozen=True)
class Term:
    source: str
    target: str | None
    mode: str = "force"
    note: str | None = None


@dataclass(frozen=True)
class Profile:
    path: Path
    sha256: str
    source_language: str
    target_language: str
    terms: tuple[Term, ...]
    protected_literals: tuple[str, ...]
    protected_patterns: tuple[str, ...]
    translate_frontmatter: tuple[str, ...]
    preserve_frontmatter: tuple[str, ...]
    forbidden_spellings: tuple[str, ...]
    extensions: dict[str, Any]

    @property
    def forced_terms(self) -> tuple[Term, ...]:
        return tuple(sorted((term for term in self.terms if term.mode == "force"), key=lambda item: len(item.source), reverse=True))

    @property
    def review_terms(self) -> tuple[Term, ...]:
        return tuple(term for term in self.terms if term.mode == "review")


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise CliError(f"Profile field '{name}' must be a list of non-empty strings.", code="R3Translate.Profile.Invalid", hint="repair the TOML profile")
    return tuple(value)


def load_profile(path: Path) -> Profile:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CliError(f"Profile '{path}' could not be read.", code="R3Translate.Profile.Unreadable", details=str(exc), hint="provide a readable profile", exit_code=2) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CliError(f"Profile '{path}' is not valid TOML.", code="R3Translate.Profile.InvalidToml", details=str(exc), hint="repair the profile", exit_code=2) from exc
    language = data.get("language")
    if not isinstance(language, dict) or not isinstance(language.get("source"), str) or not isinstance(language.get("target"), str):
        raise CliError("The profile must define [language] source and target.", code="R3Translate.Profile.Language", hint="add source and target language codes", exit_code=2)
    parsed_terms: list[Term] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(data.get("terms", [])):
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            raise CliError(f"Term {index + 1} has no source string.", code="R3Translate.Profile.Term", hint="repair [[terms]]", exit_code=2)
        mode = item.get("mode", "force")
        target = item.get("target")
        if mode not in {"force", "review"} or (mode == "force" and not isinstance(target, str)) or (target is not None and not isinstance(target, str)):
            raise CliError(f"Term '{item['source']}' has an invalid mode or target.", code="R3Translate.Profile.Term", hint="use mode force with target, or mode review", exit_code=2)
        key = (item["source"].casefold(), mode)
        if key in seen:
            raise CliError(f"Term '{item['source']}' is duplicated.", code="R3Translate.Profile.DuplicateTerm", hint="keep a single authoritative mapping", exit_code=2)
        seen.add(key)
        parsed_terms.append(Term(item["source"], target, mode, item.get("note")))
    protected = data.get("protected", {})
    frontmatter = data.get("frontmatter", {})
    style = data.get("style", {})
    patterns = _strings(protected.get("patterns"), "protected.patterns")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise CliError(f"Protected pattern '{pattern}' is invalid.", code="R3Translate.Profile.Pattern", details=str(exc), hint="repair the regular expression", exit_code=2) from exc
    return Profile(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        source_language=language["source"],
        target_language=language["target"],
        terms=tuple(parsed_terms),
        protected_literals=_strings(protected.get("literals"), "protected.literals"),
        protected_patterns=patterns,
        translate_frontmatter=_strings(frontmatter.get("translate"), "frontmatter.translate"),
        preserve_frontmatter=_strings(frontmatter.get("preserve"), "frontmatter.preserve"),
        forbidden_spellings=_strings(style.get("forbidden"), "style.forbidden"),
        extensions=dict(data.get("markdown", {})),
    )

