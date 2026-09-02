from __future__ import annotations

import re
from dataclasses import dataclass

from .profile import Profile


TOKEN_TEMPLATE = "__R3P_{:04d}__"
WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
FRONTMATTER_RE = re.compile(r"^([A-Za-z0-9_-]+)(\s*:\s*)(.*)$")


@dataclass(frozen=True)
class Protection:
    token: str
    source: str
    replacement: str
    kind: str

    def as_dict(self) -> dict[str, str]:
        return {"token": self.token, "source": self.source, "replacement": self.replacement, "kind": self.kind}


@dataclass(frozen=True)
class Segment:
    identifier: str
    start: int
    end: int
    source: str
    prepared: str
    line: int
    context: str
    protections: tuple[Protection, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.identifier,
            "start": self.start,
            "end": self.end,
            "source": self.source,
            "prepared": self.prepared,
            "translation": None,
            "context": {"line": self.line, "kind": self.context},
            "protections": [item.as_dict() for item in self.protections],
        }


def _add(spans: list[tuple[int, int, str, str]], start: int, end: int, kind: str, replacement: str | None = None) -> None:
    if end > start:
        spans.append((start, end, kind, replacement if replacement is not None else ""))


def _syntax_spans(text: str, profile: Profile) -> list[tuple[int, int, str, str]]:
    spans: list[tuple[int, int, str, str]] = []
    patterns = [
        (r"`+[^`]*?`+", "inline-code"),
        (r"\$\$.*?\$\$|(?<!\\)\$(?!\s).*?(?<!\s)\$", "math"),
        (r"</?[A-Za-z][^>]*>|<!--[\s\S]*?-->", "html"),
        (r"https?://[^\s>)\]}]+", "url"),
        (r"(?:[A-Za-z]:\\|\.\.?/|/)[^\s<>()\[\]{}]+", "path"),
        (r"!\[\[[^\]]+\]\]", "embed"),
    ]
    for pattern, kind in patterns:
        for match in re.finditer(pattern, text):
            _add(spans, match.start(), match.end(), kind)
    for match in re.finditer(r"\[([^\]\n]*)\]\(([^)\n]+)\)", text):
        _add(spans, match.start(), match.start() + 1, "markdown-link-delimiter")
        _add(spans, match.start(1) + len(match.group(1)), match.end(), "markdown-link-target")
    for match in re.finditer(r"\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]", text):
        if match.group(2) is None:
            _add(spans, match.start(), match.end(), "wikilink-target")
        else:
            _add(spans, match.start(), match.start(2), "wikilink-target")
            _add(spans, match.end(2), match.end(), "wikilink-delimiter")
    prefix = re.match(r"^(?:\s*(?:#{1,6}|>|[-+*]|\d+[.)])\s+)+", text)
    if prefix:
        _add(spans, prefix.start(), prefix.end(), "markdown-prefix")
    callout = re.match(r"^(\s*>?\s*\[![^\]]+\][+-]?\s*)", text)
    if callout:
        _add(spans, callout.start(), callout.end(), "callout")
    if re.fullmatch(r"\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*", text):
        _add(spans, 0, len(text), "table-separator")
    for match in re.finditer(r"[|]", text):
        _add(spans, match.start(), match.end(), "table-delimiter")
    for literal in sorted(profile.protected_literals, key=len, reverse=True):
        for match in re.finditer(re.escape(literal), text):
            _add(spans, match.start(), match.end(), "profile-literal")
    for pattern in profile.protected_patterns:
        for match in re.finditer(pattern, text):
            _add(spans, match.start(), match.end(), "profile-pattern")
    for term in profile.forced_terms:
        for match in re.finditer(rf"(?<!\w){re.escape(term.source)}(?!\w)", text, re.IGNORECASE):
            target = term.target or ""
            if match.group(0)[:1].isupper() and target:
                target = target[:1].upper() + target[1:]
            _add(spans, match.start(), match.end(), "required-term", target)
    for match in re.finditer(r"\\.|[*_~]+|[<>]", text):
        _add(spans, match.start(), match.end(), "markdown-delimiter")
    return spans


def _select_non_overlapping(spans: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    ordered = sorted(spans, key=lambda item: (item[0], -(item[1] - item[0])))
    result: list[tuple[int, int, str, str]] = []
    end = -1
    for span in ordered:
        if span[0] >= end:
            result.append(span)
            end = span[1]
    return result


def _prepare(text: str, profile: Profile) -> tuple[str, tuple[Protection, ...]]:
    spans = _select_non_overlapping(_syntax_spans(text, profile))
    chunks: list[str] = []
    protections: list[Protection] = []
    cursor = 0
    for start, end, kind, replacement in spans:
        chunks.append(text[cursor:start])
        token = TOKEN_TEMPLATE.format(len(protections))
        original = text[start:end]
        protections.append(Protection(token, original, replacement or original, kind))
        chunks.append(token)
        cursor = end
    chunks.append(text[cursor:])
    return "".join(chunks), tuple(protections)


def segment_document(text: str, profile: Profile) -> tuple[Segment, ...]:
    segments: list[Segment] = []
    offset = 0
    in_fence = False
    fence_char = ""
    frontmatter = text.startswith("---\n") or text.startswith("---\r\n")
    in_frontmatter = frontmatter
    frontmatter_key: str | None = None
    in_math_block = False
    in_html_block: str | None = None
    for line_number, line_with_end in enumerate(text.splitlines(keepends=True), 1):
        line = line_with_end.rstrip("\r\n")
        stripped = line.strip()
        if in_math_block:
            if stripped in {"$$", r"\]"}:
                in_math_block = False
            offset += len(line_with_end)
            continue
        if stripped in {"$$", r"\["}:
            in_math_block = True
            offset += len(line_with_end)
            continue
        if in_html_block:
            if re.search(rf"</{re.escape(in_html_block)}\s*>", line, re.IGNORECASE) or (in_html_block == "!--" and "-->" in line):
                in_html_block = None
            offset += len(line_with_end)
            continue
        html_open = re.match(r"\s*<(script|style|pre|table)\b", line, re.IGNORECASE)
        if html_open and not re.search(rf"</{html_open.group(1)}\s*>", line, re.IGNORECASE):
            in_html_block = html_open.group(1)
            offset += len(line_with_end)
            continue
        if stripped.startswith("<!--") and "-->" not in stripped:
            in_html_block = "!--"
            offset += len(line_with_end)
            continue
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence, fence_char = True, marker
            elif marker == fence_char:
                in_fence = False
            offset += len(line_with_end)
            continue
        if in_fence:
            offset += len(line_with_end)
            continue
        if not in_frontmatter and (line.startswith("    ") or line.startswith("\t")):
            offset += len(line_with_end)
            continue
        context = "body"
        start_in_line = 0
        source = line
        if in_frontmatter:
            if line_number == 1:
                offset += len(line_with_end)
                continue
            if line == "---":
                in_frontmatter = False
                offset += len(line_with_end)
                continue
            match = FRONTMATTER_RE.match(line)
            if match:
                frontmatter_key = match.group(1)
                if frontmatter_key not in profile.translate_frontmatter:
                    offset += len(line_with_end)
                    continue
                start_in_line = match.start(3)
                source = match.group(3)
                context = f"frontmatter:{frontmatter_key}"
            else:
                list_item = re.match(r"^(\s*-\s+)(.*)$", line)
                if not list_item or frontmatter_key not in profile.translate_frontmatter:
                    offset += len(line_with_end)
                    continue
                start_in_line = list_item.start(2)
                source = list_item.group(2)
                context = f"frontmatter:{frontmatter_key}"
        if not source.strip() or not WORD_RE.search(source):
            offset += len(line_with_end)
            continue
        prepared, protections = _prepare(source, profile)
        visible = re.sub(r"__R3P_\d{4}__", "", prepared)
        if not WORD_RE.search(visible):
            offset += len(line_with_end)
            continue
        start = offset + start_in_line
        segments.append(Segment(f"s{len(segments) + 1:05d}", start, start + len(source), source, prepared, line_number, context, protections))
        offset += len(line_with_end)
    return tuple(segments)


def restore_translation(value: str, protections: list[dict[str, str]]) -> str:
    restored = value
    for item in protections:
        token = item["token"]
        if restored.count(token) != 1:
            raise ValueError(f"marker {token} is missing or duplicated")
        restored = restored.replace(token, item["replacement"])
    if re.search(r"__R3P_\d{4}__", restored):
        raise ValueError("unknown protection marker remains")
    return restored
