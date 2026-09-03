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
    for match in re.finditer(r"(?:__R3P_\d{4}__|\[\[R3P\d{4}R\]\])", text):
        findings.append(Finding("R3Translate.Structure.Marker", f"Unresolved protection marker '{match.group(0)}'.", _line(text, match.start())))
    candidate_segments = segment_document(text, profile)
    for segment in candidate_segments:
        visible = re.sub(r"(?:__R3P_\d{4}__|\[\[R3P\d{4}R\]\])", " ", segment.prepared)
        for spelling in profile.forbidden_spellings:
            for match in re.finditer(rf"(?<!\w){re.escape(spelling)}(?!\w)", visible, re.IGNORECASE):
                findings.append(Finding("R3Translate.Style.Forbidden", f"Forbidden spelling '{match.group(0)}'.", segment.line))
        for term in profile.review_terms:
            for match in re.finditer(rf"(?<!\w){re.escape(term.source)}(?!\w)", visible, re.IGNORECASE):
                findings.append(Finding("R3Translate.Term.Review", f"Term '{match.group(0)}' requires review.", segment.line))
        for protection in segment.protections:
            if protection.kind == "required-term" and protection.source.casefold() != protection.replacement.casefold():
                findings.append(Finding("R3Translate.Term.Required", f"Required mapping '{protection.source}' -> '{protection.replacement}' was not applied.", segment.line))
        for residue in profile.probable_source:
            for match in re.finditer(rf"(?<!\w){re.escape(residue)}(?!\w)", visible, re.IGNORECASE):
                findings.append(Finding("R3Translate.Language.SourceResidue", f"Probable {profile.source_language} residue '{match.group(0)}'.", segment.line))
    if source_text is not None:
        source_segments = segment_document(source_text, profile)
        structural_kinds = {
            "inline-code", "math", "html", "url", "path", "embed", "markdown-link-delimiter",
            "markdown-link-target", "wikilink-target", "wikilink-delimiter", "markdown-prefix", "callout",
            "table-separator", "table-delimiter", "profile-pattern", "markdown-delimiter", "frontmatter-quote",
        }

        def structural_signature(segments):
            signature = []
            for segment in segments:
                for protection in segment.protections:
                    if protection.kind not in structural_kinds:
                        continue
                    value = protection.source
                    if protection.kind == "markdown-prefix":
                        value = re.sub(r"\s+", " ", value)
                    signature.append((value, protection.kind))
            return signature

        source_protected = structural_signature(source_segments)
        candidate_protected = structural_signature(candidate_segments)
        if source_protected != candidate_protected:
            findings.append(Finding("R3Translate.Structure.Protected", "Protected Markdown, links, paths or identifiers changed."))
    return findings
