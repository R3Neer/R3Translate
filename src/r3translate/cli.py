from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from r3_cli import CliError, ConsoleUI, add_output_arguments

from . import __version__
from .bundle import apply_bundle, atomic_write, create_bundle, read_bundle, read_utf8, write_bundle
from .checks import check_document
from .profile import load_profile
from .providers import available_providers, translate_deepl


def _output_path(value: str) -> Path | None:
    return None if value == "-" else Path(value)


def _write_bytes(value: bytes, output: str, *, force: bool) -> None:
    path = _output_path(output)
    if path is None:
        sys.stdout.buffer.write(value)
        sys.stdout.buffer.flush()
    else:
        atomic_write(path, value, force=force)


def _emit(ui: ConsoleUI, fmt: str, value: dict[str, Any], message: str) -> None:
    if fmt == "json":
        ui.json(value)
    else:
        ui.info(message)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="r3translate")
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    root.add_argument("--format", choices=("text", "json"), default="text")
    add_output_arguments(root)
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Analyse a document without writing.")
    plan.add_argument("input", type=Path)
    plan.add_argument("--profile", required=True, type=Path)
    extract = commands.add_parser("extract", help="Create an offline JSON exchange bundle.")
    extract.add_argument("input", type=Path)
    extract.add_argument("--profile", required=True, type=Path)
    extract.add_argument("--output", required=True)
    extract.add_argument("--force", action="store_true")
    apply = commands.add_parser("apply", help="Apply a verified exchange bundle.")
    apply.add_argument("input", type=Path)
    apply.add_argument("bundle", type=Path)
    apply.add_argument("--profile", required=True, type=Path)
    apply.add_argument("--output", required=True)
    apply.add_argument("--force", action="store_true")
    translate = commands.add_parser("translate", help="Translate directly through an explicit provider.")
    translate.add_argument("input", type=Path)
    translate.add_argument("--profile", required=True, type=Path)
    translate.add_argument("--provider", choices=("deepl",), required=True)
    translate.add_argument("--output", required=True)
    translate.add_argument("--force", action="store_true")
    check = commands.add_parser("check", help="Check terminology and protected structure.")
    check.add_argument("input", type=Path)
    check.add_argument("--profile", required=True, type=Path)
    check.add_argument("--source", type=Path)
    commands.add_parser("providers", help="List provider availability.")
    return root


def run(arguments: argparse.Namespace, ui: ConsoleUI) -> int:
    if arguments.command == "providers":
        providers = available_providers()
        _emit(ui, arguments.format, {"providers": providers}, f"Providers: {', '.join(item['name'] for item in providers)}")
        return 0
    profile = load_profile(arguments.profile)
    if arguments.command in {"plan", "extract", "translate"}:
        bundle = create_bundle(arguments.input, profile)
    if arguments.command == "plan":
        review = sum(1 for term in profile.review_terms if term.source.casefold() in read_utf8(arguments.input)[1].casefold())
        value = {"segments": len(bundle["segments"]), "review_terms": review, "source": profile.source_language, "target": profile.target_language}
        _emit(ui, arguments.format, value, f"{value['segments']} translatable segments; {review} review term matches.")
        return 1 if review else 0
    if arguments.command == "extract":
        if arguments.output == "-":
            sys.stdout.write(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
        else:
            write_bundle(bundle, Path(arguments.output), force=arguments.force)
            _emit(ui, arguments.format, {"output": arguments.output, "segments": len(bundle["segments"])}, f"Extracted {len(bundle['segments'])} segments to {arguments.output}.")
        return 0
    if arguments.command == "apply":
        bundle = read_bundle(arguments.bundle)
        candidate = apply_bundle(arguments.input, profile, bundle)
        _write_bytes(candidate, arguments.output, force=arguments.force)
        if arguments.output != "-":
            _emit(ui, arguments.format, {"output": arguments.output}, f"Wrote verified candidate to {arguments.output}.")
        return 0
    if arguments.command == "translate":
        ui.step(f"Translating {len(bundle['segments'])} segments with {arguments.provider}.")
        prepared = [item["prepared"] for item in bundle["segments"]]
        translations = translate_deepl(prepared, source=profile.source_language, target=profile.target_language)
        for item, translation in zip(bundle["segments"], translations, strict=True):
            item["translation"] = translation
        candidate = apply_bundle(arguments.input, profile, bundle)
        _write_bytes(candidate, arguments.output, force=arguments.force)
        if arguments.output != "-":
            _emit(ui, arguments.format, {"output": arguments.output, "segments": len(translations)}, f"Translated {len(translations)} segments to {arguments.output}.")
        return 0
    raw, text, _ = read_utf8(arguments.input)
    del raw
    source_text = read_utf8(arguments.source)[1] if arguments.source else None
    findings = check_document(text, profile, source_text=source_text)
    if arguments.format == "json":
        ui.json({"findings": [item.as_dict() for item in findings], "ok": not findings})
    elif findings:
        for finding in findings:
            location = f"line {finding.line}: " if finding.line else ""
            ui.warning(f"{location}{finding.message} [{finding.code}]")
    else:
        ui.success("No translation findings.")
    return 1 if findings else 0


def main(argv: list[str] | None = None) -> int:
    root = parser()
    try:
        arguments = root.parse_args(argv)
        human_stream = sys.stderr if arguments.format == "text" else sys.stdout
        ui = ConsoleUI(colour=arguments.colour, ascii=arguments.ascii, stdout=human_stream, stderr=sys.stderr)
        return run(arguments, ui)
    except CliError as exc:
        fmt = getattr(locals().get("arguments", None), "format", "text")
        if fmt == "json":
            sys.stdout.write(json.dumps({"error": exc.as_diagnostic().as_dict()}, ensure_ascii=False) + "\n")
        else:
            ConsoleUI(colour="never", stderr=sys.stderr).error(exc)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

