from __future__ import annotations

import os
import time
from collections.abc import Sequence

from r3_cli import CliError


def available_providers() -> list[dict[str, object]]:
    try:
        import deepl  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    return [{"name": "deepl", "installed": installed, "requires": "DEEPL_AUTH_KEY"}]


def _deepl_translator():
    key = os.environ.get("DEEPL_AUTH_KEY")
    if not key:
        raise CliError("DeepL authentication is not configured.", code="R3Translate.Provider.Authentication", hint="set DEEPL_AUTH_KEY", exit_code=3)
    try:
        import deepl
    except ImportError as exc:
        raise CliError("The DeepL provider is not installed.", code="R3Translate.Provider.NotInstalled", hint="install r3translate[deepl]", exit_code=3) from exc
    return deepl.Translator(key)


def deepl_usage() -> dict[str, int]:
    """Return character usage without sending any Markdown content."""
    translator = _deepl_translator()
    try:
        usage = translator.get_usage()
        character = getattr(usage, "character", None)
        used = getattr(character, "count", None)
        limit = getattr(character, "limit", None)
        valid = getattr(character, "valid", True)
        if not valid or not isinstance(used, int) or not isinstance(limit, int):
            raise ValueError("character usage is unavailable")
        return {"used": used, "limit": limit}
    except CliError:
        raise
    except Exception as exc:  # SDK exception classes vary by version.
        raise CliError("DeepL usage could not be retrieved.", code="R3Translate.Provider.UsageFailed", details=type(exc).__name__, hint="check connectivity and the DeepL account, then retry", exit_code=3) from exc


def translate_deepl(texts: Sequence[str], *, source: str, target: str, batch_size: int = 40) -> list[str]:
    translator = _deepl_translator()
    translated: list[str] = []
    for offset in range(0, len(texts), batch_size):
        batch = list(texts[offset : offset + batch_size])
        for attempt in range(3):
            try:
                result = translator.translate_text(batch, source_lang=source.upper(), target_lang=target.upper(), preserve_formatting=True)
                values = result if isinstance(result, list) else [result]
                translated.extend(item.text for item in values)
                break
            except Exception as exc:  # SDK exception classes vary by version.
                if attempt == 2:
                    raise CliError("DeepL could not translate the requested batch.", code="R3Translate.Provider.Failed", details=type(exc).__name__, hint="check connectivity and the DeepL account, then retry", exit_code=3) from exc
                time.sleep(0.5 * (2**attempt))
    return translated

