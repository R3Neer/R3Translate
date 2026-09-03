# R3Translate

R3Translate translates Markdown without first rebuilding it. It identifies
translatable source intervals and protects code, links, identifiers and required
terminology. Direct provider translation sends only the linguistic fragments
between protections; protected values and their markers remain local. A
verified bundle changes only the identified source intervals.

The profile is the translation contract:

```toml
[language]
source = "ES"
target = "EN-GB"

[[terms]]
source = "regla"
target = "rule"
mode = "force"

[protected]
literals = ["always", "Num"]
patterns = ["\\b(?:D|Q)-\\d{3}\\b"]

[frontmatter]
translate = ["title", "aliases"]
preserve = ["status", "decisions", "questions"]

[style]
forbidden = ["behavior", "color"]

[checks]
probable-source = ["para", "pero", "también"]
```

## Offline exchange

```console
r3translate plan document.md --profile es-en.toml
r3translate extract document.md --profile es-en.toml --output exchange.json
# Fill each segment's `translation` field without changing its markers.
r3translate apply document.md exchange.json --profile es-en.toml --output document.en.md
r3translate check document.en.md --source document.md --profile es-en.toml
```

`extract` and `apply` are entirely offline. Bundles include SHA-256 hashes of
the original and profile and are rejected when either input has changed.

## Plan a translation budget

`plan` accepts either one Markdown file or a directory. Directory scans recurse
through `.md` files in a stable order and ignore hidden directories such as
`.git` and `.obsidian`.

```console
r3translate plan notas --profile es-en.toml --quota 1000000
```

It reports source, protected and prepared characters, plus the actual DeepL
request character count. Protection markers are included in `prepared_chars`
for diagnostic purposes, but excluded from the DeepL request because direct
translation keeps them local. This mode is fully offline and needs no API key.

To compare with the current DeepL account quota, request it explicitly:

```console
set DEEPL_AUTH_KEY=...
r3translate plan notas --profile es-en.toml --live-usage
```

`--live-usage` queries account usage but sends no Markdown content. A plan that
finds review terms or exceeds the chosen budget exits with code `1`.

## DeepL

```console
pipx install "r3translate[deepl] @ git+https://github.com/R3Neer/R3Translate.git@v0.2.1"
set DEEPL_AUTH_KEY=...
r3translate translate document.md --profile es-en.toml --provider deepl --output document.en.md
```

Only `translate --provider deepl` performs network traffic. Protected Markdown,
paths, links, identifiers and profile literals are never sent to DeepL; only
the surrounding translatable fragments are submitted and recomposed locally.
The key is read only from `DEEPL_AUTH_KEY`; R3Translate does not create remote
glossaries.

## Exit codes

- `0`: success
- `1`: candidate or check completed with findings
- `2`: usage or configuration error
- `3`: provider or network error
- `4`: unsafe reconstruction

## Licence

MIT. See [`LICENSE`](LICENSE).
