# R3Translate

R3Translate translates Markdown without first rebuilding it. It identifies
translatable source intervals, replaces code, links, identifiers and required
terminology with opaque markers, and changes only those intervals when a
verified bundle is applied.

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

## DeepL

```console
pipx install "r3translate[deepl] @ git+https://github.com/R3Neer/R3Translate.git@v0.1.0"
set DEEPL_AUTH_KEY=...
r3translate translate document.md --profile es-en.toml --provider deepl --output document.en.md
```

Only `translate --provider deepl` performs network traffic. The key is read
only from `DEEPL_AUTH_KEY`; R3Translate does not create remote glossaries.

## Exit codes

- `0`: success
- `1`: candidate or check completed with findings
- `2`: usage or configuration error
- `3`: provider or network error
- `4`: unsafe reconstruction

## Licence

MIT. See [`LICENSE`](LICENSE).

