"""Semantyczna powierzchnia modelu: ścisły, ograniczony podzbiór czterech formatów.

Nie ufa temu, że builder sam wpisał hash do zbioru. Dekoduje narzędzia z żądania
wobec definicji zapamiętanych przy starcie. Nie aprobuje arbitralnych rozszerzeń
providera ani usuwania ograniczeń schematów. Brak wsparcia oznacza odmowę,
nie automatyczne poluzowanie lub próbę wysłania. To nie walidator całego SDK.
"""
from __future__ import annotations

import json
from typing import Any


class ModelBoundaryError(ValueError):
    """Nieznany format lub powierzchnia wykonawcza oznacza odmowę przed klientem."""


# Pola danych/parametrów generacji, nie kanały rozszerzeń wykonawczych. Nieznane
# top-level keys również odmawiają; extra_body nie może nadpisać pola po kontroli.
_COMMON = {'stream', 'timeout', 'extra_body'}
_FIELDS = {
    'chat_completions': _COMMON | {
        'model', 'messages', 'tools', 'tool_choice', 'parallel_tool_calls',
        'max_tokens', 'max_completion_tokens', 'temperature', 'top_p', 'seed',
        'stop', 'frequency_penalty', 'presence_penalty', 'logprobs', 'top_logprobs',
        'logit_bias', 'n', 'user', 'store', 'metadata', 'service_tier',
        'stream_options', 'response_format', 'reasoning_effort',
        'prompt_cache_key', 'prompt_cache_retention', 'safety_identifier',
    },
    'codex_responses': _COMMON | {
        'model', 'input', 'instructions', 'tools', 'tool_choice',
        'parallel_tool_calls', 'max_output_tokens', 'temperature', 'top_p',
        'store', 'metadata', 'service_tier', 'reasoning', 'text', 'include',
        'truncation', 'prompt_cache_key', 'prompt_cache_retention', 'user',
        'safety_identifier', 'extra_headers',
    },
    'anthropic_messages': _COMMON | {
        'model', 'messages', 'system', 'tools', 'tool_choice', 'max_tokens',
        'temperature', 'top_p', 'top_k', 'stop_sequences', 'metadata',
        'thinking', 'output_config', 'service_tier',
    },
    'bedrock_converse': _COMMON | {
        'modelId', 'messages', 'system', 'toolConfig', 'inferenceConfig',
        'guardrailConfig', 'requestMetadata', 'performanceConfig',
        '__bedrock_converse__', '__bedrock_region__',
    },
}


def is_moa_provider(provider: Any) -> bool:
    """MoA is a separate fan-out runtime, not an approved provider wrapper.

    Match the native initializer's normalization; never whitelist its private
    prepared-request payload as if it were a passive SDK data field.
    """
    return isinstance(provider, str) and provider.strip().lower() == "moa"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _refuse(reason: str) -> None:
    raise ModelBoundaryError(reason)


def _cache(value: Any) -> None:
    if (type(value) is not dict or set(value) - {'type', 'ttl'}
            or value.get('type') != 'ephemeral'
            or value.get('ttl', '5m') not in {'5m', '1h'}):
        _refuse('unrecognized tool cache annotation')


def _tool(agent: Any, mode: str, value: Any) -> dict:
    if type(value) is not dict:
        _refuse('tool is not an object')
    value = dict(value)
    if 'cache_control' in value:
        _cache(value.pop('cache_control'))
    if mode == 'chat_completions':
        if set(value) != {'type', 'function'} or value['type'] != 'function':
            _refuse('non-function or extended chat tool')
        fn = value['function']
        if type(fn) is not dict or set(fn) != {'name', 'description', 'parameters'}:
            _refuse('unrecognized chat function shape')
        return fn
    if mode == 'codex_responses':
        if (set(value) != {'type', 'name', 'description', 'parameters', 'strict'}
                or value['type'] != 'function' or value['strict'] is not False):
            _refuse('non-function or extended responses tool')
        return {k: value[k] for k in ('name', 'description', 'parameters')}
    if mode == 'anthropic_messages':
        if set(value) != {'name', 'description', 'input_schema'}:
            _refuse('non-function or extended messages tool')
        name = value['name']
        if getattr(agent, '_is_anthropic_oauth', False):
            if type(name) is not str or not name.startswith('mcp__'):
                _refuse('missing approved OAuth name prefix')
            name = name[len('mcp__'):]
        return {'name': name, 'description': value['description'], 'parameters': value['input_schema']}
    if (set(value) != {'toolSpec'} or type(value['toolSpec']) is not dict
            or set(value['toolSpec']) != {'name', 'description', 'inputSchema'}):
        _refuse('non-function or extended converse tool')
    spec = value['toolSpec']
    if type(spec['inputSchema']) is not dict or set(spec['inputSchema']) != {'json'}:
        _refuse('unrecognized converse input schema')
    return {'name': spec['name'], 'description': spec['description'], 'parameters': spec['inputSchema']['json']}


def _choice(value: Any, names: set[str], mode: str, agent: Any) -> None:
    if value is None or (type(value) is str and value in {'auto', 'required', 'none'}):
        return
    if type(value) is not dict:
        _refuse('unrecognized tool choice')
    name = None
    if mode == 'chat_completions' and set(value) == {'type', 'function'} and value['type'] == 'function':
        if type(value['function']) is dict and set(value['function']) == {'name'}:
            name = value['function']['name']
    elif mode == 'codex_responses' and set(value) == {'type', 'name'} and value['type'] == 'function':
        name = value['name']
    elif mode == 'anthropic_messages':
        selection = {k: v for k, v in value.items() if k != 'disable_parallel_tool_use'}
        if 'disable_parallel_tool_use' in value and type(value['disable_parallel_tool_use']) is not bool:
            _refuse('invalid parallel choice')
        if selection in ({'type': 'auto'}, {'type': 'any'}, {'type': 'none'}):
            return
        if set(selection) == {'type', 'name'} and selection['type'] == 'tool':
            name = selection['name']
            if getattr(agent, '_is_anthropic_oauth', False) and type(name) is str and name.startswith('mcp__'):
                name = name[len('mcp__'):]
    elif mode == 'bedrock_converse':
        if value in ({'auto': {}}, {'any': {}}):
            return
        if set(value) == {'tool'} and type(value['tool']) is dict and set(value['tool']) == {'name'}:
            name = value['tool']['name']
    if type(name) is not str or name not in names:
        _refuse('choice is outside transmitted capability set')


def _codex_headers(agent: Any, payload: dict) -> dict[str, str]:
    """Only native Codex session/cache headers, never arbitrary SDK headers.

    Both values must match their existing request/session identities. Include
    these headers in the registered envelope so changing even both sides of a
    cache-key/header pair after the builder is refused by the final-call gate.
    No Authorization, Host, beta headers, aliases, routing override or extra_body.
    Other providers and custom Codex endpoints remain unsupported here.
    """
    if 'extra_headers' not in payload:
        return {}
    if (getattr(agent, 'provider', None) != 'openai-codex'
            or getattr(agent, 'base_url', None) not in {
                'https://chatgpt.com/backend-api/codex',
                'https://chatgpt.com/backend-api/codex/'}):
        _refuse('Codex headers require the approved provider and endpoint')
    headers = payload['extra_headers']
    if (type(headers) is not dict
            or any(type(k) is not str for k in headers)
            or set(headers) != {'session_id', 'x-client-request-id'}):
        _refuse('unapproved Codex header set')
    session = getattr(agent, 'session_id', None)
    cache_key = payload.get('prompt_cache_key')
    for value, limit in ((session, 256), (cache_key, 64)):
        if (type(value) is not str or not 0 < len(value) <= limit
                or any(not 33 <= ord(c) <= 126 for c in value)):
            _refuse('invalid Codex session or cache identity')
    expected = {'session_id': session, 'x-client-request-id': cache_key}
    if (any(type(v) is not str for v in headers.values()) or headers != expected):
        _refuse('Codex header identity mismatch')
    return dict(headers)


def capabilities(agent: Any, payload: Any, definitions: dict[str, str],
                 allowed_names: set[str] | None = None) -> dict:
    """Zwróć kanoniczny opis uprawnień, bez treści rozmowy i tokenów klienta."""
    if type(payload) is not dict or any(type(k) is not str for k in payload):
        _refuse('model payload is not a plain dictionary')
    mode = getattr(agent, 'api_mode', '')
    if mode not in _FIELDS or set(payload) - _FIELDS[mode]:
        _refuse('unreviewed request mode or top-level extension')
    # Rozszerzenia są kanałem nadpisywania SDK. Do zatwierdzenia ich semantyki
    # dopuszczamy tylko brak/pusty obiekt. Dotyczy też potencjalnie łagodnych opcji.
    if payload.get('extra_body') not in (None, {}):
        _refuse('unreviewed extra_body extension')
    if 'extra_body' in payload and payload['extra_body'] is not None and type(payload['extra_body']) is not dict:
        _refuse('invalid extra_body type')
    model = payload.get('modelId' if mode == 'bedrock_converse' else 'model')
    if type(model) is not str or model != getattr(agent, 'model', None):
        _refuse('request model differs from active route')
    headers = _codex_headers(agent, payload) if mode == 'codex_responses' else {}
    choice = payload.get('tool_choice')
    tools = payload.get('tools', [])
    if tools is None:
        tools = []
    if mode == 'bedrock_converse':
        config = payload.get('toolConfig')
        if config is not None:
            if type(config) is not dict or set(config) - {'tools', 'toolChoice'}:
                _refuse('extended converse tool configuration')
            tools = config.get('tools', [])
            if tools is None:
                tools = []
            choice = config.get('toolChoice')
    if type(tools) is not list:
        _refuse('invalid transported tool list')
    names: set[str] = set()
    canonical = {}
    cache_points = 0
    for value in tools:
        if mode == 'bedrock_converse' and value == {'cachePoint': {'type': 'default'}}:
            cache_points += 1
            if cache_points > 1:
                _refuse('duplicate converse tool cache annotation')
            continue
        fn = _tool(agent, mode, value)
        name = fn.get('name')
        if type(name) is not str or name in names or name not in definitions:
            _refuse('duplicate or unapproved transported tool')
        expected = json.loads(definitions[name])['function']
        if _json(fn) != _json(expected):
            _refuse('transport changed an approved function schema')
        if allowed_names is not None and name not in allowed_names:
            _refuse('transport widened the requested tool subset')
        canonical[name] = fn
        names.add(name)
    _choice(choice, names, mode, agent)
    if 'parallel_tool_calls' in payload and type(payload['parallel_tool_calls']) is not bool:
        _refuse('invalid parallel_tool_calls')
    return {'tools': canonical, 'choice': choice,
            'parallel': payload.get('parallel_tool_calls'),
            'extra_body': payload.get('extra_body'),
            'transport_headers': headers,
            '_route': [mode, getattr(agent, 'provider', ''), model]}
