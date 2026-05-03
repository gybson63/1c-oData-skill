"""Тесты парсинга $metadata XML (lib.metadata_parser)."""

import pytest

from bot_lib.metadata_parser import (
    classify_entity_sets,
    find_schema,
    get_namespace,
    iter_entity_types,
    iter_nav_properties,
    iter_properties,
    parse_entity_fields,
    parse_entity_sets,
    search_entities,
)


# =========================================================================
# parse_entity_sets
# =========================================================================


class TestParseEntitySets:
    """Тесты для parse_entity_sets."""

    def test_returns_list_of_dicts(self, sample_metadata_xml: str):
        result = parse_entity_sets(sample_metadata_xml)
        assert isinstance(result, list)
        assert all(isinstance(e, dict) for e in result)

    def test_extracts_entity_names(self, sample_metadata_xml: str):
        result = parse_entity_sets(sample_metadata_xml)
        names = [e["name"] for e in result]
        assert "Catalog_Сотрудники" in names
        assert "Document_Увольнение" in names

    def test_description_label(self, sample_metadata_xml: str):
        result = parse_entity_sets(sample_metadata_xml)
        catalog = next(e for e in result if e["name"] == "Catalog_Сотрудники")
        assert "Description" in catalog["label"]

    def test_no_description_label(self, sample_metadata_xml: str):
        result = parse_entity_sets(sample_metadata_xml)
        doc = next(e for e in result if e["name"] == "Document_Увольнение")
        assert doc["label"] == ""

    def test_invalid_xml_returns_empty(self):
        result = parse_entity_sets("not xml at all")
        assert result == []

    def test_empty_xml_returns_empty(self):
        result = parse_entity_sets("")
        assert result == []


# =========================================================================
# parse_entity_fields
# =========================================================================


class TestParseEntityFields:
    """Тесты для parse_entity_fields."""

    def test_catalog_fields(self, sample_metadata_xml: str):
        fields = parse_entity_fields(sample_metadata_xml, "Catalog_Сотрудники")
        assert "Description" in fields
        assert "Code" in fields
        assert "Ref_Key" in fields
        assert "DataVersion" in fields
        assert "DeletionMark" in fields

    def test_document_fields_include_nav_property(self, sample_metadata_xml: str):
        fields = parse_entity_fields(sample_metadata_xml, "Document_Увольнение")
        # NavigationProperty тоже должен быть в списке
        assert "Сотрудник" in fields
        assert "Number" in fields
        assert "Date" in fields

    def test_nonexistent_entity_returns_empty(self, sample_metadata_xml: str):
        fields = parse_entity_fields(sample_metadata_xml, "Catalog_Несуществующий")
        assert fields == []

    def test_invalid_xml_returns_empty(self):
        fields = parse_entity_fields("bad xml", "Catalog_Сотрудники")
        assert fields == []


# =========================================================================
# search_entities
# =========================================================================


class TestSearchEntities:
    """Тесты для search_entities."""

    @pytest.fixture
    def entities(self, sample_metadata_xml: str) -> list[dict]:
        return parse_entity_sets(sample_metadata_xml)

    def test_search_by_russian_substring(self, entities: list[dict]):
        result = search_entities(entities, "Сотруд")
        assert "Catalog_Сотрудники" in result

    def test_search_by_prefix(self, entities: list[dict]):
        result = search_entities(entities, "Catalog_")
        assert "Catalog_Сотрудники" in result

    def test_search_by_document_type(self, entities: list[dict]):
        result = search_entities(entities, "Увольнен")
        assert "Document_Увольнение" in result

    def test_search_no_match(self, entities: list[dict]):
        result = search_entities(entities, "Несуществующее")
        assert len(result) == 0

    def test_search_empty_query_returns_all(self, entities: list[dict]):
        result = search_entities(entities, "")
        assert len(result) == len(entities)

    def test_search_respects_top(self, entities: list[dict]):
        result = search_entities(entities, "", top=1)
        assert len(result) == 1

    def test_search_empty_entities(self):
        result = search_entities([], "test")
        assert result == []

    def test_search_case_insensitive(self, entities: list[dict]):
        result_lower = search_entities(entities, "каталог")
        result_upper = search_entities(entities, "Каталог")
        # Оба должны вернуть одинаковый результат (если совпадает)
        assert result_lower == result_upper


# =========================================================================
# iter_entity_types / iter_properties / iter_nav_properties
# =========================================================================


class TestIterHelpers:
    """Тесты для итераторов по XML-элементам."""

    def test_iter_entity_types(self, sample_metadata_xml: str):
        from xml.etree import ElementTree as ET

        root = ET.fromstring(sample_metadata_xml)
        types = list(iter_entity_types(root))
        names = [et.get("Name") for et in types]
        assert "Catalog_Сотрудники" in names
        assert "Document_Увольнение" in names

    def test_iter_properties(self, sample_metadata_xml: str):
        from xml.etree import ElementTree as ET

        root = ET.fromstring(sample_metadata_xml)
        catalog_type = next(
            et for et in iter_entity_types(root) if et.get("Name") == "Catalog_Сотрудники"
        )
        props = list(iter_properties(catalog_type))
        prop_names = [p.get("Name") for p in props]
        assert "Description" in prop_names
        assert "Code" in prop_names

    def test_iter_nav_properties(self, sample_metadata_xml: str):
        from xml.etree import ElementTree as ET

        root = ET.fromstring(sample_metadata_xml)
        doc_type = next(
            et for et in iter_entity_types(root) if et.get("Name") == "Document_Увольнение"
        )
        nav_props = list(iter_nav_properties(doc_type))
        nav_names = [p.get("Name") for p in nav_props]
        assert "Сотрудник" in nav_names

    def test_iter_nav_properties_empty_for_catalog(self, sample_metadata_xml: str):
        from xml.etree import ElementTree as ET

        root = ET.fromstring(sample_metadata_xml)
        catalog_type = next(
            et for et in iter_entity_types(root) if et.get("Name") == "Catalog_Сотрудники"
        )
        nav_props = list(iter_nav_properties(catalog_type))
        assert len(nav_props) == 0


# =========================================================================
# classify_entity_sets
# =========================================================================


class TestClassifyEntitySets:
    """Тесты для classify_entity_sets."""

    def test_classifies_catalog_and_document(self, sample_metadata_xml: str):
        type_counts, type_names, entity_sets, namespace = classify_entity_sets(
            sample_metadata_xml
        )
        assert namespace == "TestConfig"
        assert type_counts.get("Catalog") == 1
        assert type_counts.get("Document") == 1

    def test_type_names_contain_object_names(self, sample_metadata_xml: str):
        _, type_names, _, _ = classify_entity_sets(sample_metadata_xml)
        assert "Сотрудники" in type_names.get("Catalog", [])
        assert "Увольнение" in type_names.get("Document", [])

    def test_entity_sets_elements(self, sample_metadata_xml: str):
        _, _, entity_sets, _ = classify_entity_sets(sample_metadata_xml)
        assert len(entity_sets) == 2
        names = [es.get("Name") for es in entity_sets]
        assert "Catalog_Сотрудники" in names
        assert "Document_Увольнение" in names

    def test_invalid_xml_returns_empty(self):
        type_counts, type_names, entity_sets, namespace = classify_entity_sets("bad")
        assert type_counts == {}
        assert type_names == {}
        assert entity_sets == []
        assert namespace == ""


# =========================================================================
# find_schema / get_namespace
# =========================================================================


class TestSchemaHelpers:
    """Тесты для find_schema и get_namespace."""

    def test_find_schema(self, sample_metadata_xml: str):
        from xml.etree import ElementTree as ET

        root = ET.fromstring(sample_metadata_xml)
        schema = find_schema(root)
        assert schema is not None
        assert schema.get("Namespace") == "TestConfig"

    def test_get_namespace(self, sample_metadata_xml: str):
        from xml.etree import ElementTree as ET

        root = ET.fromstring(sample_metadata_xml)
        schema = find_schema(root)
        assert get_namespace(schema) == "TestConfig"