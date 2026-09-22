"""Protect short-lived credential replies for the current Windows account."""

import hashlib
import json

import win32crypt


def _entropy(owner, reference, request_id):
    return hashlib.sha256(json.dumps([owner, reference, request_id]).encode()).digest()


def protect_response(response, owner, reference, request_id):
    payload = json.dumps(response, separators=(',', ':')).encode('utf-8')
    return win32crypt.CryptProtectData(payload, 'UnifiedPersonalMCP workflow resume',
        _entropy(owner, reference, request_id), None, None, 1)


def unprotect_response(payload, owner, reference, request_id):
    _, plaintext = win32crypt.CryptUnprotectData(bytes(payload),
        _entropy(owner, reference, request_id), None, None, 1)
    return json.loads(plaintext.decode('utf-8'))
