from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from r3_cli import CliError
from r3translate.bundle import apply_bundle, atomic_write, create_bundle, read_bundle, write_bundle
from r3translate.checks import check_document
from r3translate.markdown import rebuild_translation_fragments, segment_document, split_translation_fragments
from r3translate.profile import load_profile


PROFILE = r'''
[language]
source = "ES"
target = "EN-GB"

[[terms]]
source = "regla"
target = "rule"
mode = "force"

[[terms]]
source = "ancla"
mode = "review"

[protected]
literals = ["always", "Num"]
patterns = ["\\b(?:D|Q)-\\d{3}\\b"]

[frontmatter]
translate = ["title", "aliases"]
preserve = ["status", "decisions"]

[style]
forbidden = ["behavior", "color"]

[checks]
probable-source = ["para"]
'''.lstrip()


@pytest.fixture
def fixture(tmp_path: Path):
    profile_path = tmp_path / "profile.toml"
    profile_path.write_text(PROFILE, encoding="utf-8", newline="")
    source_path = tmp_path / "source.md"
    source_path.write_text(
        "---\r\ntitle: Una regla clara\r\nstatus: borrador\r\n---\r\n# Una regla\r\n\r\nTexto con `código`, $x + 1$, [etiqueta](https://example.com/a), [[Destino|ancla]], D-123 y always.\r\n\r\n```mud\r\nalways regla\r\n```\r\n",
        encoding="utf-8",
        newline="",
    )
    return source_path, profile_path, load_profile(profile_path)


def test_empty_bundle_round_trip_is_byte_identical(fixture) -> None:
    source, _, profile = fixture
    bundle = create_bundle(source, profile)
    assert apply_bundle(source, profile, bundle) == source.read_bytes()


def test_apply_changes_only_segments_and_restores_protections(fixture) -> None:
    source, _, profile = fixture
    bundle = create_bundle(source, profile)
    for segment in bundle["segments"]:
        segment["translation"] = segment["prepared"].replace("Una", "A").replace("Texto con", "Text with").replace("etiqueta", "label")
    candidate = apply_bundle(source, profile, bundle).decode("utf-8")
    assert "A rule clara" in candidate
    assert "`código`" in candidate
    assert "$x + 1$" in candidate
    assert "[label](https://example.com/a)" in candidate
    assert "[[Destino|ancla]]" in candidate
    assert "D-123" in candidate and "always" in candidate
    assert "always regla" in candidate  # fenced code is untouched
    assert b"\r\n" in apply_bundle(source, profile, bundle)


def test_fragment_plan_keeps_protections_out_of_provider_text(fixture) -> None:
    source, _, profile = fixture
    segment = next(item for item in create_bundle(source, profile)["segments"] if "`código`" in item["source"])
    parts, indexes = split_translation_fragments(segment["prepared"], segment["protections"])
    provider_text = [parts[index] for index in indexes]
    assert all(item["source"] not in "".join(provider_text) for item in segment["protections"])
    assert all("[[R3P" not in value for value in provider_text)
    rebuilt = rebuild_translation_fragments(parts, indexes, [value.replace("Texto", "Text") for value in provider_text])
    assert all(item["token"] in rebuilt for item in segment["protections"])


def test_protected_only_line_does_not_create_a_segment(fixture) -> None:
    _, _, profile = fixture
    assert segment_document("`code only`\n", profile) == ()


def test_quoted_frontmatter_edges_are_protected(fixture) -> None:
    source, _, profile = fixture
    source.write_text('---\ntitle: "Un título"\n---\nTexto.\n', encoding="utf-8")
    segment = next(item for item in create_bundle(source, profile)["segments"] if item["protections"])
    assert [item["kind"] for item in segment["protections"]].count("frontmatter-quote") == 2


def test_lost_marker_is_unsafe(fixture) -> None:
    source, _, profile = fixture
    bundle = create_bundle(source, profile)
    segment = next(item for item in bundle["segments"] if item["protections"])
    segment["translation"] = segment["prepared"].replace(segment["protections"][0]["token"], "")
    with pytest.raises(CliError) as error:
        apply_bundle(source, profile, bundle)
    assert error.value.exit_code == 4


def test_changed_source_or_profile_is_rejected(fixture) -> None:
    source, profile_path, profile = fixture
    bundle = create_bundle(source, profile)
    source.write_text(source.read_text(encoding="utf-8") + "extra", encoding="utf-8")
    with pytest.raises(CliError, match="original document has changed"):
        apply_bundle(source, profile, bundle)
    assert bundle["profile"]["sha256"] == hashlib.sha256(profile_path.read_bytes()).hexdigest()


def test_bundle_json_round_trip(fixture, tmp_path: Path) -> None:
    source, _, profile = fixture
    output = tmp_path / "exchange.json"
    write_bundle(create_bundle(source, profile), output)
    assert read_bundle(output)["schema"] == "r3translate.bundle/v1"
    with pytest.raises(CliError):
        write_bundle({}, output)


def test_atomic_write_leaves_existing_destination_unchanged(tmp_path: Path) -> None:
    output = tmp_path / "candidate.md"
    output.write_bytes(b"old")
    with pytest.raises(CliError):
        atomic_write(output, b"new", force=False)
    assert output.read_bytes() == b"old"


def test_check_reports_british_spelling_and_review_term(fixture) -> None:
    _, _, profile = fixture
    findings = check_document("The behavior of this ancla uses color.", profile)
    assert [item.code for item in findings] == [
        "R3Translate.Style.Forbidden",
        "R3Translate.Style.Forbidden",
        "R3Translate.Term.Review",
    ]


def test_check_reports_source_residue_and_unresolved_marker(fixture) -> None:
    _, _, profile = fixture
    findings = check_document("Text para review [[R3P0001R]].", profile)
    assert {item.code for item in findings} == {
        "R3Translate.Language.SourceResidue",
        "R3Translate.Structure.Marker",
    }


def test_check_reports_unapplied_required_term(fixture) -> None:
    _, _, profile = fixture
    findings = check_document("Una regla pendiente.", profile)
    assert any(item.code == "R3Translate.Term.Required" for item in findings)


def test_check_ignores_protected_destinations_and_code(fixture) -> None:
    _, _, profile = fixture
    findings = check_document("[Label](docs/regla.md) and `regla behavior`.\n", profile)
    assert findings == []


def test_structural_check_detects_changed_link(fixture) -> None:
    source, _, profile = fixture
    original = source.read_text(encoding="utf-8")
    changed = original.replace("https://example.com/a", "https://example.com/b")
    findings = check_document(changed, profile, source_text=original)
    assert any(item.code == "R3Translate.Structure.Protected" for item in findings)


def test_frontmatter_lists_translate_but_math_and_indented_code_do_not(fixture) -> None:
    _, _, profile = fixture
    text = "---\naliases:\n  - Nombre visible\nstatus: activo\n---\n$$\nformula words\n$$\n\n    código indentado\n"
    bundle_source = [segment.source for segment in segment_document(text, profile)]
    assert bundle_source == ["Nombre visible"]
