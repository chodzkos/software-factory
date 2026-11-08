"""Wyłącznie typy wyjątków SDK dla prób callbacków z klientem Mock, nie klient API.

Gdy openai nie jest dostępne w lokalnym środowisku, nie udajemy instalacji SDK.
Dostarczamy tylko klasy używane przez niewykonywane w tym teście gałęzie HTTP.
Żaden klient/serializer/transport SDK nie jest implementowany przez tę fixture.
Pełna instalacja zależności z locka pozostaje odrębną bramką.
"""
from contextlib import contextmanager
import importlib
import sys
import types
from unittest.mock import patch


@contextmanager
def http_error_types():
    try:
        importlib.import_module('openai')
    except ModuleNotFoundError as exc:
        if exc.name != 'openai':
            raise  # Nie maskuj uszkodzonej instalacji lub brakującej zależności SDK.
        module = types.ModuleType('openai')
        class APIError(Exception):
            pass
        module.APIError = APIError
        module.APIConnectionError = type('APIConnectionError', (APIError,), {})
        module.APIStatusError = type('APIStatusError', (APIError,), {})
        module.APITimeoutError = type('APITimeoutError', (module.APIConnectionError,), {})
        module.RateLimitError = type('RateLimitError', (module.APIStatusError,), {})
        with patch.dict(sys.modules, {'openai': module}):
            yield 'EXCEPTION_TYPES_ONLY_NOT_SDK'
    else:
        yield 'SDK_IMPORT_AVAILABLE'
