from __future__ import annotations

import sys
from types import SimpleNamespace

from r3translate.providers import translate_deepl


def test_deepl_adapter_batches_without_exposing_key(monkeypatch) -> None:
    calls: list[tuple[list[str], str, str]] = []

    class Translator:
        def __init__(self, key: str) -> None:
            assert key == "secret-for-test"

        def translate_text(self, texts, *, source_lang, target_lang, preserve_formatting):
            assert preserve_formatting is True
            calls.append((texts, source_lang, target_lang))
            return [SimpleNamespace(text=f"EN:{text}") for text in texts]

    monkeypatch.setenv("DEEPL_AUTH_KEY", "secret-for-test")
    monkeypatch.setitem(sys.modules, "deepl", SimpleNamespace(Translator=Translator))
    assert translate_deepl(["uno", "dos", "tres"], source="ES", target="EN-GB", batch_size=2) == ["EN:uno", "EN:dos", "EN:tres"]
    assert [len(call[0]) for call in calls] == [2, 1]
    assert all(call[1:] == ("ES", "EN-GB") for call in calls)

