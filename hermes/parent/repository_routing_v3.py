"""Literal route authority for the separate routed-card v3 increment.

The trusted parent must put repository_routing in its request BEFORE sealing
request/descriptor/finalization_binding. No defaults, inference, model argument
or change to owner task inputs is allowed. This module does not approve a job,
choose providers/models, claim a card, or replace the native routing policy.
"""
from __future__ import annotations
from dataclasses import dataclass
import repository_task_contract as c

VERSION = '3.0.0'
SCHEMA = 'sf-native-repository-route/v3'
IMPLEMENTER = 'coder-claude'
REVIEWERS = ('reviewer-gpt',)
KEYS = ('schema', 'implementer', 'required_reviewers', 'security_sensitive',
        'admission_sha256')

class RoutingRefused(ValueError):
    """Fail-closed literal contract refusal; no source or credentials in error."""

def need(ok: object, code: str) -> None:
    if not ok:
        raise RoutingRefused(code)

@dataclass(frozen=True)
class Route:
    admission_sha256: str
    security_sensitive: str

    def fields(self) -> dict:
        c.hex_value(self.admission_sha256)
        need(type(self.security_sensitive) is str and self.security_sensitive in ('yes', 'no'),
             'routing_security_literal_required')
        return dict(schema=SCHEMA, implementer=IMPLEMENTER,
                    required_reviewers=list(REVIEWERS), security_sensitive=self.security_sensitive,
                    admission_sha256=self.admission_sha256)

    def digest(self) -> str:
        return c.sha(c.canonical(self.fields()))

    def declarations(self) -> str:
        d = self.fields()
        return ('IMPLEMENTER: ' + d['implementer'] + '\n'
                'REQUIRED_REVIEWERS: ' + ','.join(d['required_reviewers']) + '\n'
                'SECURITY_SENSITIVE: ' + d['security_sensitive'] + '\n')

def from_spec(spec: dict, admission: c.Admission) -> Route:
    """Use only the caller's hash-bound request, never the native card body.

    The adapter calls this after original fj_validate_binding verified the whole
    request against the descriptor and DB binding. This pure parser additionally
    binds the literal route to the exact owner admission. Same-interpreter hostile
    callers remain outside the accepted runtime trust boundary.
    """
    need(type(spec) is dict, 'routing_request_type')
    d = spec.get('repository_routing')
    need(type(d) is dict and set(d) == set(KEYS), 'routing_fields_required')
    need(d['schema'] == SCHEMA and type(d['schema']) is str, 'routing_schema')
    need(type(d['implementer']) is str and d['implementer'] == IMPLEMENTER and
         type(d['required_reviewers']) is list and d['required_reviewers'] == list(REVIEWERS),
         'routing_fixed_profile_pair')
    digest = c.sha(c.canonical(admission.receipt()))
    need(type(d['admission_sha256']) is str and d['admission_sha256'] == digest,
         'routing_owner_admission_changed')
    route = Route(digest, d['security_sensitive'])
    need(route.fields() == d, 'routing_noncanonical_fields')
    return route
