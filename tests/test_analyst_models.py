#!/usr/bin/env python3
"""Тесты MetadataBrief."""

import json

from bot.agents.analyst.models import MetadataBrief, MetadataObject


def test_metadata_object_from_dict():
    obj = MetadataObject.from_dict(
        {
            "meta_type": "Document",
            "name": "Отпуск",
            "odata_entity": "Document_Отпуск",
            "role": "primary",
            "reason": "оформление отпуска",
        }
    )
    assert obj.name == "Отпуск"
    assert obj.role == "primary"


def test_metadata_brief_roundtrip():
    data = {
        "intent": "vacation_balance",
        "primary_objects": [
            {
                "meta_type": "InformationRegister",
                "name": "АналитикаОстатковОтпусков",
                "odata_entity": "InformationRegister_АналитикаОстатковОтпусков",
                "role": "primary",
                "reason": "остаток дней",
            }
        ],
        "avoid": ["InformationRegister_ОстаткиОтпусков"],
        "notes": "test",
    }
    brief = MetadataBrief.from_dict(data)
    assert brief.intent == "vacation_balance"
    assert len(brief.primary_objects) == 1
    assert brief.avoid[0].endswith("ОстаткиОтпусков")
    restored = MetadataBrief.from_dict(json.loads(brief.to_json()))
    assert restored.intent == brief.intent


def test_to_prompt_block():
    brief = MetadataBrief(
        intent="employees",
        primary_objects=[
            MetadataObject(
                meta_type="InformationRegister",
                name="КадроваяИсторияСотрудников",
                odata_entity="InformationRegister_КадроваяИсторияСотрудников",
                role="primary",
                reason="актуальные данные",
            )
        ],
    )
    block = brief.to_prompt_block()
    assert "Intent: employees" in block
    assert "КадроваяИсторияСотрудников" in block
