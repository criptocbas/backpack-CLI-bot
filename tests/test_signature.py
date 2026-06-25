"""Tests for ED25519 request signing.

The highest-value guard here is the boolean-lowercasing rule: Python's
``str(True)`` is ``"True"`` but the JSON wire format (and therefore the
server-side signature check) uses ``"true"``. If the signer ever stops
lowercasing, ``test_capitalized_bool_does_not_verify`` fails.
"""

import base64

import pytest
from nacl.exceptions import BadSignatureError

from api.backpack import BackpackClient


def _verify(client: BackpackClient, message: str, signature: str) -> None:
    """Raise BadSignatureError if ``signature`` is not valid for ``message``."""
    client.signing_key.verify_key.verify(message.encode(), base64.b64decode(signature))


def test_booleans_are_lowercased_in_signed_message(client: BackpackClient):
    sig, ts, win = client._generate_signature(
        "orderExecute", {"reduceOnly": True, "quantity": "1.5"}
    )
    expected = (
        f"instruction=orderExecute&quantity=1.5&reduceOnly=true"
        f"&timestamp={ts}&window={win}"
    )
    _verify(client, expected, sig)  # must not raise


def test_capitalized_bool_does_not_verify(client: BackpackClient):
    # Proves the signer did NOT sign "reduceOnly=True".
    sig, ts, win = client._generate_signature("orderExecute", {"reduceOnly": True})
    wrong = f"instruction=orderExecute&reduceOnly=True&timestamp={ts}&window={win}"
    with pytest.raises(BadSignatureError):
        _verify(client, wrong, sig)


def test_false_bool_is_lowercased(client: BackpackClient):
    sig, ts, win = client._generate_signature("orderExecute", {"postOnly": False})
    expected = f"instruction=orderExecute&postOnly=false&timestamp={ts}&window={win}"
    _verify(client, expected, sig)


def test_params_are_sorted_alphabetically(client: BackpackClient):
    sig, ts, win = client._generate_signature("balanceQuery", {"b": "2", "a": "1"})
    expected = f"instruction=balanceQuery&a=1&b=2&timestamp={ts}&window={win}"
    _verify(client, expected, sig)


def test_no_params_signs_instruction_only(client: BackpackClient):
    sig, ts, win = client._generate_signature("balanceQuery")
    expected = f"instruction=balanceQuery&timestamp={ts}&window={win}"
    _verify(client, expected, sig)


def test_window_is_returned_as_string(client: BackpackClient):
    _sig, _ts, win = client._generate_signature("balanceQuery")
    assert win == "5000"
