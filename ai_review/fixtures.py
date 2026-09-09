"""Invented non-market evidence for offline acceptance; never real research results."""
import hashlib
import json
from .contracts import CHECKS, digest, now
from .providers import Route, FixtureTransport, OpenAIPrimaryAdapter, GeminiReviewerAdapter


def source(identifier='fixture-source', content='Invented fixture: observations precede decisions.'):
    return dict(id=identifier, content=content, sha256=hashlib.sha256(content.encode()).hexdigest(),
        provenance=dict(uri='fixture://' + identifier, retrieved_at='2026-09-07T00:00:00+00:00', publisher='offline test'))


def packet(checkpoint='new_hypothesis', artifact_id='fixture-hypothesis'):
    return dict(artifact_id=artifact_id, checkpoint=checkpoint, evidence=[source()],
        assumptions=['Synthetic contract exercise; no market inference'],
        specification='Only use observations received before the fixture decision.',
        deterministic_context={'data_admission': 'unknown'}, resolutions=[])


def validator(packet):
    return dict(checks={k: 'passed' for k in sorted(CHECKS)}, validator_version='fixture-validator-v1',
        packet_hash=digest(packet), scope='offline_review_only', verifications=[])


def response(output, version='fixture-1'):
    return dict(text=json.dumps(output), model_version=version, usage={'input_tokens': 30, 'output_tokens': 30})


def primary_response(request):
    return response(dict(proposal='Apply only observations preceding the fixture decision.',
                         assumptions=['Invented evidence'], uncertainties=['No historical-data certification']))


def review_response(request, classification='agree'):
    objections = []
    if classification != 'agree':
        objections.append(dict(severity=classification, primary_claim='Observations precede decisions',
            objection='Receipt availability is not demonstrated.', disputed_evidence=['fixture-source'],
            unresolved_question='When was the observation available?',
            required_resolution='Supply a linked timestamp receipt or a deterministic causal check.'))
    return response(dict(request_hash=request['request_hash'], classification=classification,
        summary='Independent synthetic evidence examination.', objections=objections))


def adapters(review=None, primary=None, **route_options):
    return (OpenAIPrimaryAdapter(Route('openai', 'gpt-fixture', 'fixture-1', mode='fixture', tier='fixture', **route_options),
                                FixtureTransport(primary or primary_response)),
            GeminiReviewerAdapter(Route('gemini', 'gemini-fixture', 'fixture-1', mode='fixture', tier='fixture', **route_options),
                                  FixtureTransport(review or review_response)))
