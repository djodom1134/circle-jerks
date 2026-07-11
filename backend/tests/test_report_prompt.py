from __future__ import annotations

from app import services


def test_system_prompt_fingerprint_differs_past_120_chars():
    common = "x" * 130
    a = services.system_prompt_fingerprint(common + "AAA")
    b = services.system_prompt_fingerprint(common + "BBB")
    assert a != b


def test_system_prompt_fingerprint_stable_and_default():
    assert services.system_prompt_fingerprint(None) == services.system_prompt_fingerprint(None)
    assert services.system_prompt_fingerprint("") == services.system_prompt_fingerprint(None)
