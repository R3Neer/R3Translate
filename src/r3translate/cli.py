from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from r3_cli import (
    CliError,
    CommandHelp,
    ConsoleUI,
    HelpCatalogue,
    HelpItem,
    R3ArgumentParser,
    add_output_arguments,
    resolve_help_request,
    validate_argparse_catalogue,
)

from . import __version__
from .bundle import apply_bundle, atomic_write, create_bundle, read_bundle, read_utf8, write_bundle
from .checks import check_document
from .profile import load_profile
from .providers import available_providers, translate_deepl


def _output_path(value: str) -> Path | None:
    return None if value == "-" else Path(value)


def _presentation(argv: Sequence[str]) -> tuple[str, bool]:
    colour = "auto"
    ascii_output = "--ascii" in argv
    for index, value in enumerate(argv):
        if value.startswith("--colour="):
            candidate = value.partition("=")[2]
            if candidate in {"auto", "always", "never"}:
                colour = candidate
        elif value == "--colour" and index + 1 < len(argv):
            candidate = argv[index + 1]
            if candidate in {"auto", "always", "never"}:
                colour = candidate
    if not ascii_output:
        encoding = sys.stdout.encoding or "utf-8"
        try:
            "═✓→•✗—".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            ascii_output = True
    return colour, ascii_output


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
    root = R3ArgumentParser(prog="r3translate", add_help=False)
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    root.add_argument("--format", choices=("text", "json"), default="text")
    add_output_arguments(root)
    commands = root.add_subparsers(dest="command", required=True, parser_class=R3ArgumentParser)
    plan = commands.add_parser("plan", help="Analyse a document without writing.", add_help=False)
    plan.add_argument("input", type=Path)
    plan.add_argument("--profile", required=True, type=Path)
    extract = commands.add_parser("extract", help="Create an offline JSON exchange bundle.", add_help=False)
    extract.add_argument("input", type=Path)
    extract.add_argument("--profile", required=True, type=Path)
    extract.add_argument("--output", required=True)
    extract.add_argument("--force", action="store_true")
    apply = commands.add_parser("apply", help="Apply a verified exchange bundle.", add_help=False)
    apply.add_argument("input", type=Path)
    apply.add_argument("bundle", type=Path)
    apply.add_argument("--profile", required=True, type=Path)
    apply.add_argument("--output", required=True)
    apply.add_argument("--force", action="store_true")
    translate = commands.add_parser("translate", help="Translate directly through an explicit provider.", add_help=False)
    translate.add_argument("input", type=Path)
    translate.add_argument("--profile", required=True, type=Path)
    translate.add_argument("--provider", choices=("deepl",), required=True)
    translate.add_argument("--output", required=True)
    translate.add_argument("--force", action="store_true")
    check = commands.add_parser("check", help="Check terminology and protected structure.", add_help=False)
    check.add_argument("input", type=Path)
    check.add_argument("--profile", required=True, type=Path)
    check.add_argument("--source", type=Path)
    commands.add_parser("providers", help="List provider availability.", add_help=False)
    return root


def help_catalogue() -> HelpCatalogue:
    invocation = "r3translate"
    return HelpCatalogue(
        product="R3TRANSLATE",
        version=__version__,
        description="Translate Markdown conservatively without rebuilding its protected structure.",
        invocation=invocation,
        groups=("TRANSLATION", "PROVIDERS"),
        usage=(f"{invocation} <command> [arguments] [options]", f"{invocation} <command> --help", f"{invocation} --version"),
        global_items=(
            HelpItem("--version", "Print the installed version and exit."),
            HelpItem("--format text|json", "Choose human-readable text or machine-readable JSON. Default: text."),
            HelpItem("--colour auto|always|never", "Control colour for human output. Default: auto; NO_COLOR disables it."),
            HelpItem("--ascii", "Use ASCII status symbols when Unicode is unsuitable."),
        ),
        commands=(
            CommandHelp("plan", "TRANSLATION", "Analyse a document without writing", "Analyse a Markdown document and report translatable segments and review terms without creating files.", (f"{invocation} plan INPUT --profile PROFILE",), (
                HelpItem("INPUT", "Markdown source document to analyse."),
                HelpItem("--profile PROFILE", "TOML translation contract that defines languages, terminology and protections."),
            ), examples=(f"{invocation} plan document.md --profile es-en.toml",)),
            CommandHelp("extract", "TRANSLATION", "Create an offline JSON exchange bundle", "Extract verified translation segments into a JSON bundle for an external translator.", (f"{invocation} extract INPUT --profile PROFILE --output OUTPUT [--force]",), (
                HelpItem("INPUT", "Markdown source document to extract."),
                HelpItem("--profile PROFILE", "TOML translation contract used to protect structure and terminology."),
                HelpItem("--output OUTPUT", "New JSON bundle path, or - to write the bundle to standard output."),
                HelpItem("--force", "Replace an existing output file. Without it, existing files are preserved."),
            ), notes=("This command is offline. Do not alter opaque markers when filling translations.",), examples=(f"{invocation} extract document.md --profile es-en.toml --output exchange.json",)),
            CommandHelp("apply", "TRANSLATION", "Apply a verified exchange bundle", "Reconstruct a translated Markdown candidate only when the source document and profile still match the bundle hashes.", (f"{invocation} apply INPUT BUNDLE --profile PROFILE --output OUTPUT [--force]",), (
                HelpItem("INPUT", "Original Markdown document used to create the bundle."),
                HelpItem("BUNDLE", "JSON exchange bundle containing translations and verification hashes."),
                HelpItem("--profile PROFILE", "The unchanged TOML translation contract used for extraction."),
                HelpItem("--output OUTPUT", "New candidate path, or - to write candidate bytes to standard output."),
                HelpItem("--force", "Replace an existing output file. Without it, existing files are preserved."),
            ), notes=("Hash mismatches are rejected to prevent unsafe reconstruction.",), examples=(f"{invocation} apply document.md exchange.json --profile es-en.toml --output document.en.md",)),
            CommandHelp("translate", "TRANSLATION", "Translate through an explicit provider", "Translate protected segments through the requested provider and write a verified Markdown candidate.", (f"{invocation} translate INPUT --profile PROFILE --provider deepl --output OUTPUT [--force]",), (
                HelpItem("INPUT", "Markdown source document to translate."),
                HelpItem("--profile PROFILE", "TOML translation contract used to protect structure and enforce terminology."),
                HelpItem("--provider deepl", "Use DeepL. Requires DEEPL_AUTH_KEY and is the only command that uses the network."),
                HelpItem("--output OUTPUT", "New candidate path, or - to write candidate bytes to standard output."),
                HelpItem("--force", "Replace an existing output file. Without it, existing files are preserved."),
            ), notes=("DEEPL_AUTH_KEY is read only from the environment and is never written to a bundle or error message.",), examples=(f"{invocation} translate document.md --profile es-en.toml --provider deepl --output document.en.md",)),
            CommandHelp("check", "TRANSLATION", "Check terminology and protected structure", "Check a candidate for terminology, protected-marker and configured-language findings without changing files.", (f"{invocation} check INPUT --profile PROFILE [--source SOURCE]",), (
                HelpItem("INPUT", "Translated Markdown candidate to inspect."),
                HelpItem("--profile PROFILE", "TOML translation contract containing required terms and style checks."),
                HelpItem("--source SOURCE", "Optional original document used for additional source-language checks."),
            ), examples=(f"{invocation} check document.en.md --source document.md --profile es-en.toml",)),
            CommandHelp("providers", "PROVIDERS", "List provider availability", "List built-in translation providers and whether their local requirements are available.", (f"{invocation} providers",), notes=("Provider credentials are never displayed.",)),
        ),
        notes=(f"Run {invocation} <command> --help for detailed help.",),
    )


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
    catalogue = help_catalogue()
    values = sys.argv[1:] if argv is None else argv
    colour, ascii_output = _presentation(values)
    try:
        validate_argparse_catalogue(root, catalogue)
        request = resolve_help_request(values, catalogue)
        if request is not None:
            ConsoleUI(colour=colour, ascii=ascii_output).help(catalogue, request.command)
            return 0
        arguments = root.parse_args(values)
        human_stream = sys.stderr if arguments.format == "text" else sys.stdout
        ui = ConsoleUI(colour=arguments.colour, ascii=arguments.ascii or ascii_output, stdout=human_stream, stderr=sys.stderr)
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

