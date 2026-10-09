from types import SimpleNamespace as NS
from unittest.mock import Mock

from app.screeners import pii_presidio as pii


def test_public_route_places_preserved_but_personal_entities_masked(monkeypatch):
    artifacts = NS(
        tokens=NS(
            ents=[
                NS(start_char=0, end_char=10, label_="GPE", text="Birmingham"),
                NS(start_char=20, end_char=22, label_="GPE", text="UK"),
            ]
        )
    )
    results = [
        NS(start=0, end=10, entity_type="LOCATION"),
        NS(start=20, end=22, entity_type="LOCATION"),
        NS(start=30, end=45, entity_type="LOCATION"),
        NS(start=50, end=60, entity_type="PERSON"),
        NS(start=65, end=80, entity_type="EMAIL_ADDRESS"),
    ]
    analyzer = Mock()
    analyzer.nlp_engine.process_text.return_value = artifacts
    analyzer.analyze.return_value = results
    monkeypatch.setattr(pii, "_analyzer", analyzer)
    assert pii._run_presidio_analysis("route and contact information") == results[2:]
    anonymizer = Mock()
    anonymizer.anonymize.return_value = NS(text="masked")
    monkeypatch.setattr(pii, "_anonymizer", anonymizer)
    assert pii._run_presidio_anonymize("route and contact information") == "masked"
    assert anonymizer.anonymize.call_args.kwargs["analyzer_results"] == results[2:]
