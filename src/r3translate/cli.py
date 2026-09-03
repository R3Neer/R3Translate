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
from .markdown import rebuild_translation_fragments, split_translation_fragments
from .profile import load_profile
from .providers import available_providers, deepl_usage, translate_deepl


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
    budget = plan.add_mutually_exclusive_group()
    budget.add_argument("--quota", type=int, metavar="CHARACTERS")
    budget.add_argument("--live-usage", action="store_true")
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
            CommandHelp("plan", "TRANSLATION", "Analyse Markdown without writing", "Analyse one Markdown document or a directory tree and estimate the characters prepared for DeepL without creating files.", (f"{invocation} plan INPUT --profile PROFILE [--quota CHARACTERS | --live-usage]",), (
                HelpItem("INPUT", "Markdown source document, or directory to scan recursively for .md files. Hidden directories are ignored."),
                HelpItem("--profile PROFILE", "TOML translation contract that defines languages, terminology and protections."),
                HelpItem("--quota CHARACTERS", "Local character budget to compare with the actual DeepL request. Does not use the network."),
                HelpItem("--live-usage", "Query the current DeepL character quota through DEEPL_AUTH_KEY. Sends no Markdown content."),
            ), notes=("By default this command is fully offline. Prepared characters include local markers; DeepL request characters count only the linguistic fragments sent to the provider.",), examples=(f"{invocation} plan document.md --profile es-en.toml --quota 1000000", f"{invocation} plan notes --profile es-en.toml --live-usage")),
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
            ), notes=("Only linguistic fragments are sent to DeepL; protected Markdown, paths, links, identifiers and their markers remain local. DEEPL_AUTH_KEY is read only from the environment and is never written to a bundle or error message.",), examples=(f"{invocation} translate document.md --profile es-en.toml --provider deepl --output document.en.md",)),
            CommandHelp("check", "TRANSLATION", "Check terminology and protected structure", "Check a candidate for terminology, protected-marker and configured-language findings without changing files.", (f"{invocation} check INPUT --profile PROFILE [--source SOURCE]",), (
                HelpItem("INPUT", "Translated Markdown candidate to inspect."),
                HelpItem("--profile PROFILE", "TOML translation contract containing required terms and style checks."),
                HelpItem("--source SOURCE", "Optional original document used for additional source-language checks."),
            ), examples=(f"{invocation} check document.en.md --source document.md --profile es-en.toml",)),
            CommandHelp("providers", "PROVIDERS", "List provider availability", "List built-in translation providers and whether their local requirements are available.", (f"{invocation} providers",), notes=("Provider credentials are never displayed.",)),
        ),
        notes=(f"Run {invocation} <command> --help for detailed help.",),
    )


def _markdown_inputs(input_path: Path) -> tuple[Path, ...]:
    if input_path.is_file():
        if input_path.suffix.casefold() != ".md":
            raise CliError("The plan input must be a Markdown file or directory.", code="R3Translate.Plan.InvalidInput", hint="provide a .md file or a directory", exit_code=2)
        return (input_path,)
    if not input_path.is_dir():
        raise CliError("The plan input does not exist.", code="R3Translate.Plan.InputMissing", hint="provide an existing Markdown file or directory", exit_code=2)
    files = tuple(sorted((path for path in input_path.rglob("*.md") if not any(part.startswith(".") for part in path.relative_to(input_path).parts[:-1])), key=lambda path: path.as_posix().casefold()))
    if not files:
        raise CliError("The directory contains no Markdown files.", code="R3Translate.Plan.NoMarkdown", hint="provide a directory containing .md files", exit_code=2)
    return files


def _plan_value(input_path: Path, profile: Any, *, quota: int | None, live_usage: bool) -> tuple[dict[str, Any], int]:
    if quota is not None and quota < 0:
        raise CliError("The character quota cannot be negative.", code="R3Translate.Plan.InvalidQuota", hint="provide zero or a positive number", exit_code=2)
    files = _markdown_inputs(input_path)
    directory = input_path.is_dir()
    entries: list[dict[str, Any]] = []
    source_texts: list[str] = []
    for path in files:
        _, text, _ = read_utf8(path)
        bundle = create_bundle(path, profile)
        segments = bundle["segments"]
        protected = sum(len(protection["source"]) for segment in segments for protection in segment["protections"])
        prepared = sum(len(segment["prepared"]) for segment in segments)
        deepl_request = sum(
            len(parts[index])
            for segment in segments
            for parts, indexes in [split_translation_fragments(segment["prepared"], segment["protections"])]
            for index in indexes
        )
        entries.append({
            "path": path.relative_to(input_path).as_posix() if directory else path.name,
            "source_chars": len(text),
            "protected_chars": protected,
            "prepared_chars": prepared,
            "deepl_request_chars": deepl_request,
            "segments": len(segments),
        })
        source_texts.append(text)
    source = "\n".join(source_texts).casefold()
    review = sum(1 for term in profile.review_terms if term.source.casefold() in source)
    value: dict[str, Any] = {
        "markdown_files": len(entries),
        "source_chars": sum(entry["source_chars"] for entry in entries),
        "protected_chars": sum(entry["protected_chars"] for entry in entries),
        "prepared_chars": sum(entry["prepared_chars"] for entry in entries),
        "deepl_request_chars": sum(entry["deepl_request_chars"] for entry in entries),
        "segments": sum(entry["segments"] for entry in entries),
        "review_terms": review,
        "source": profile.source_language,
        "target": profile.target_language,
        "files": entries,
    }
    if quota is not None:
        usage = {"source": "configured", "used": 0, "limit": quota}
    elif live_usage:
        current = deepl_usage()
        usage = {"source": "live", **current}
    else:
        usage = None
    over_budget = False
    if usage is not None:
        remaining_before = usage["limit"] - usage["used"]
        remaining_after = remaining_before - value["deepl_request_chars"]
        usage["remaining_before_plan"] = remaining_before
        usage["remaining_after_plan"] = remaining_after
        value["quota"] = usage
        value["within_quota"] = remaining_after >= 0
        over_budget = remaining_after < 0
    else:
        value["within_quota"] = None
    return value, 1 if review or over_budget else 0


def _emit_plan(ui: ConsoleUI, fmt: str, value: dict[str, Any]) -> None:
    if fmt == "json":
        ui.json(value)
        return
    rows = (
        ("Markdown files", value["markdown_files"]),
        ("Source characters", value["source_chars"]),
        ("Characters protected", value["protected_chars"]),
        ("Prepared characters", value["prepared_chars"]),
        ("DeepL request chars", value["deepl_request_chars"]),
    )
    for label, number in rows:
        ui.key_value(label, f"{number:,}", width=24)
    quota = value.get("quota")
    if quota:
        prefix = "Configured quota" if quota["source"] == "configured" else "DeepL quota"
        ui.key_value(prefix, f"{quota['limit']:,}", width=24)
        if quota["source"] == "live":
            ui.key_value("DeepL used", f"{quota['used']:,}", width=24)
            ui.key_value("Remaining before plan", f"{quota['remaining_before_plan']:,}", width=24)
        ui.key_value("Remaining after plan", f"{quota['remaining_after_plan']:,}", width=24)
        if not value["within_quota"]:
            ui.warning("The planned DeepL request exceeds the available character budget.")
    if value["review_terms"]:
        ui.warning(f"{value['review_terms']} review term matches require attention.")


def run(arguments: argparse.Namespace, ui: ConsoleUI) -> int:
    if arguments.command == "providers":
        providers = available_providers()
        _emit(ui, arguments.format, {"providers": providers}, f"Providers: {', '.join(item['name'] for item in providers)}")
        return 0
    profile = load_profile(arguments.profile)
    if arguments.command in {"extract", "translate"}:
        bundle = create_bundle(arguments.input, profile)
    if arguments.command == "plan":
        value, exit_code = _plan_value(arguments.input, profile, quota=arguments.quota, live_usage=arguments.live_usage)
        _emit_plan(ui, arguments.format, value)
        return exit_code
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
        plans: list[tuple[list[str], list[int], int, int]] = []
        fragments: list[str] = []
        for item in bundle["segments"]:
            try:
                parts, translatable = split_translation_fragments(item["prepared"], item.get("protections", []))
            except (KeyError, TypeError, ValueError) as exc:
                raise CliError(f"Segment '{item.get('id', '?')}' has invalid protected structure.", code="R3Translate.Translate.Protection", details=str(exc), hint="extract the document again", exit_code=4) from exc
            start = len(fragments)
            fragments.extend(parts[index] for index in translatable)
            plans.append((parts, translatable, start, len(fragments)))
        translations = translate_deepl(fragments, source=profile.source_language, target=profile.target_language) if fragments else []
        for item, (parts, translatable, start, end) in zip(bundle["segments"], plans, strict=True):
            item["translation"] = rebuild_translation_fragments(parts, translatable, translations[start:end])
        candidate = apply_bundle(arguments.input, profile, bundle)
        _write_bytes(candidate, arguments.output, force=arguments.force)
        if arguments.output != "-":
            _emit(ui, arguments.format, {"output": arguments.output, "segments": len(bundle["segments"]), "fragments": len(translations)}, f"Translated {len(bundle['segments'])} segments ({len(translations)} fragments) to {arguments.output}.")
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

