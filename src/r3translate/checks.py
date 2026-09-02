from __future__ import annotations

import re
from dataclasses import dataclass

from .markdown import segment_document
from .profile import Profile


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    line: int | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"code": self.code, "message": self.message}
        if self.line is not None:
            value["line"] = self.line
        return value


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def check_document(text: str, profile: Profile, *, source_text: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for match in re.finditer(r"__R3P_\d{4}__", text):
        findings.append(Finding("R3Translate.Structure.Marker", f"Unresolved protection marker '{match.group(0)}'.", _line(text, match.start())))
    for spelling in profile.forbidden_spellings:
        for match in re.finditer(rf"(?<!\w){re.escape(spelling)}(?!\w)", text, re.IGNORECASE):
            findings.append(Finding("R3Translate.Style.Forbidden", f"Forbidden spelling '{match.group(0)}'.", _line(text, match.start())))
    for term in profile.review_terms:
        for match in re.finditer(rf"(?<!\w){re.escape(term.source)}(?!\w)", text, re.IGNORECASE):
            findings.append(Finding("R3Translate.Term.Review", f"Term '{match.group(0)}' requires review.", _line(text, match.start())))
    for residue in profile.probable_source:
        for match in re.finditer(rf"(?<!\w){re.escape(residue)}(?!\w)", text, re.IGNORECASE):
            findings.append(Finding("R3Translate.Language.SourceResidue", f"Probable {profile.source_language} residue '{match.group(0)}'.", _line(text, match.start())))
    if source_text is not None:
        source_segments = segment_document(source_text, profile)
        candidate_segments = segment_document(text, profile)
        source_protected = [(protection.source, protection.kind) for segment in source_segments for protection in segment.protections if protection.kind != "required-term"]
        candidate_protected = [(protection.source, protection.kind) for segment in candidate_segments for protection in segment.protections if protection.kind != "required-term"]
        if source_protected != candidate_protected:
            findings.append(Finding("R3Translate.Structure.Protected", "Protected Markdown, links, paths or identifiers changed."))
    return findings
