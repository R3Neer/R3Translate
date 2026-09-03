from __future__ import annotations

import json
from pathlib import Path

import pytest

import r3translate.cli as cli_module
from r3translate.cli import main


def profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile.toml"
    path.write_text('[language]\nsource="ES"\ntarget="EN-GB"\n[style]\nforbidden=["color"]\n', encoding="utf-8")
    return path


def test_plan_json_has_no_ansi(tmp_path: Path, capsys) -> None:
    document = tmp_path / "a.md"
    document.write_text("Texto traducible.\n", encoding="utf-8")
    assert main(["--format", "json", "--colour", "always", "plan", str(document), "--profile", str(profile(tmp_path))]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["segments"] == 1
    assert "\x1b[" not in output


def test_help_is_separated_from_the_shell_prompt(capsys) -> None:
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("\n")
    assert "R3TRANSLATE 0.2.1" in output
    assert "GLOBAL OPTIONS" in output
    assert "--format text|json" in output
    assert output.endswith("\n\n")


def test_command_help_is_separated_from_the_shell_prompt(capsys) -> None:
    assert main(["plan", "--help"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("\n")
    assert "ARGUMENTS AND OPTIONS" in output
    assert "--profile PROFILE" in output
    assert "--quota CHARACTERS" in output
    assert output.endswith("\n\n")


@pytest.mark.parametrize(
    ("command", "expected"),
    (
        ("plan", "--profile PROFILE"),
        ("extract", "--output OUTPUT"),
        ("apply", "BUNDLE"),
        ("translate", "DEEPL_AUTH_KEY"),
        ("check", "--source SOURCE"),
        ("providers", "Provider credentials are never displayed."),
    ),
)
def test_command_help_pages_document_their_inputs(command: str, expected: str, capsys) -> None:
    assert main([command, "--help"]) == 0
    output = capsys.readouterr().out
    assert expected in output
    assert output.endswith("\n\n")


def test_version_remains_a_single_line(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert capsys.readouterr().out == "r3translate 0.2.1\n"


def test_plan_directory_reports_prepared_character_budget(tmp_path: Path, capsys) -> None:
    source = tmp_path / "notes"
    source.mkdir()
    (source / "a.md").write_text("Texto `code`.\n", encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    (nested / "b.md").write_text("Otro texto.\n", encoding="utf-8")
    ignored = source / ".obsidian"
    ignored.mkdir()
    (ignored / "ignored.md").write_text("No contar.\n", encoding="utf-8")
    assert main(["--format", "json", "plan", str(source), "--profile", str(profile(tmp_path)), "--quota", "100"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["markdown_files"] == 2
    assert [item["path"] for item in value["files"]] == ["a.md", "nested/b.md"]
    assert value["source_chars"] == sum(len(path.read_bytes().decode("utf-8")) for path in (source / "a.md", nested / "b.md"))
    assert value["protected_chars"] == len("`code`")
    assert value["prepared_chars"] > value["deepl_request_chars"]
    assert value["quota"]["remaining_after_plan"] == 100 - value["deepl_request_chars"]


def test_plan_over_budget_returns_findings(tmp_path: Path, capsys) -> None:
    document = tmp_path / "a.md"
    document.write_text("Texto traducible.\n", encoding="utf-8")
    assert main(["--format", "json", "plan", str(document), "--profile", str(profile(tmp_path)), "--quota", "1"]) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["within_quota"] is False
    assert value["quota"]["source"] == "configured"


def test_plan_is_offline_without_a_key(tmp_path: Path, monkeypatch, capsys) -> None:
    document = tmp_path / "a.md"
    document.write_text("Texto traducible.\n", encoding="utf-8")
    monkeypatch.delenv("DEEPL_AUTH_KEY", raising=False)
    assert main(["--format", "json", "plan", str(document), "--profile", str(profile(tmp_path))]) == 0
    assert "quota" not in json.loads(capsys.readouterr().out)


def test_extract_and_apply_offline(tmp_path: Path) -> None:
    document = tmp_path / "a.md"
    document.write_bytes(b"Texto.\n")
    config = profile(tmp_path)
    exchange = tmp_path / "exchange.json"
    candidate = tmp_path / "candidate.md"
    assert main(["extract", str(document), "--profile", str(config), "--output", str(exchange)]) == 0
    assert main(["apply", str(document), str(exchange), "--profile", str(config), "--output", str(candidate)]) == 0
    assert candidate.read_bytes() == document.read_bytes()


def test_provider_failure_uses_exit_3(tmp_path: Path, monkeypatch) -> None:
    document = tmp_path / "a.md"
    document.write_text("Texto.\n", encoding="utf-8")
    monkeypatch.delenv("DEEPL_AUTH_KEY", raising=False)
    assert main(["translate", str(document), "--profile", str(profile(tmp_path)), "--provider", "deepl", "--output", str(tmp_path / "out.md")]) == 3


def test_direct_translation_never_sends_protected_content(tmp_path: Path, monkeypatch) -> None:
    document = tmp_path / "a.md"
    document.write_text("Texto antes de `código` y https://example.com/a después.\n", encoding="utf-8")
    output = tmp_path / "out.md"
    seen: list[str] = []
    monkeypatch.setenv("DEEPL_AUTH_KEY", "secret-for-test")

    def translate(texts, *, source, target):
        seen.extend(texts)
        replacements = {
            "Texto antes de ": "Text before of ",
            " y ": " and ",
            " después.": " after.",
        }
        return [replacements.get(value, value) for value in texts]

    monkeypatch.setattr(cli_module, "translate_deepl", translate)
    assert main(["translate", str(document), "--profile", str(profile(tmp_path)), "--provider", "deepl", "--output", str(output)]) == 0
    assert all("[[R3P" not in value and "código" not in value and "https://example.com/a" not in value for value in seen)
    assert output.read_text(encoding="utf-8") == "Text before of `código` and https://example.com/a after.\n"

