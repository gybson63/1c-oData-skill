"""Тесты OData HTTP-клиента (lib.odata_client)."""

import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from bot_lib.odata_client import ODataClient
from bot_lib.exceptions import ODataConnectionError, ODataHTTPError, ODataParseError


# =========================================================================
# get_entities
# =========================================================================


class TestGetEntities:
    """Тесты для ODataClient.get_entities."""

    @pytest.mark.asyncio
    async def test_get_entities_success(self, odata_url: str):
        """Базовый запрос сущностей — возвращает JSON."""
        with respx.mock:
            respx.get(f"{odata_url}/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "value": [
                            {"Description": "Иванов", "Code": "001"},
                            {"Description": "Петров", "Code": "002"},
                        ]
                    },
                )
            )

            async with ODataClient(odata_url) as client:
                result = await client.get_entities("Catalog_Сотрудники")
                assert result["value"][0]["Description"] == "Иванов"
                assert len(result["value"]) == 2

    @pytest.mark.asyncio
    async def test_get_entities_with_filter(self, odata_url: str):
        """Запрос с $filter — параметры передаются в query string."""
        route = respx.get(url__startswith=f"{odata_url}/Catalog_Test").mock(
            return_value=httpx.Response(200, json={"value": []})
        )

        with respx.mock:
            respx.get(url__startswith=f"{odata_url}/Catalog_Test").mock(
                return_value=httpx.Response(200, json={"value": [{"id": 1}]})
            )

            async with ODataClient(odata_url) as client:
                result = await client.get_entities(
                    "Catalog_Test",
                    filter_="Description eq 'Test'",
                    top=10,
                )
                assert result["value"][0]["id"] == 1

    @pytest.mark.asyncio
    async def test_get_entities_http_error(self, odata_url: str):
        """HTTP 500 → ODataHTTPError."""
        async with ODataClient(odata_url) as client:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_response.is_success = False

            with patch.object(
                client._client, "request",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "500",
                    request=MagicMock(url="http://test/Catalog_Test"),
                    response=mock_response,
                )
                with pytest.raises(ODataHTTPError) as exc_info:
                    await client.get_entities("Catalog_Test")
                assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_get_entities_not_found(self, odata_url: str):
        """HTTP 404 → ODataHTTPError."""
        async with ODataClient(odata_url) as client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.text = "Not Found"
            mock_response.is_success = False

            with patch.object(
                client._client, "request",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "404",
                    request=MagicMock(url="http://test/Catalog_Missing"),
                    response=mock_response,
                )
                with pytest.raises(ODataHTTPError) as exc_info:
                    await client.get_entities("Catalog_Missing")
                assert exc_info.value.status_code == 404


# =========================================================================
# get_count
# =========================================================================


class TestGetCount:
    """Тесты для ODataClient.get_count."""

    @pytest.mark.asyncio
    async def test_get_count_success(self, odata_url: str):
        """Получение количества — целое число."""
        with respx.mock:
            respx.get(f"{odata_url}/Catalog_Сотрудники/$count").mock(
                return_value=httpx.Response(200, text="42")
            )

            async with ODataClient(odata_url) as client:
                result = await client.get_count("Catalog_Сотрудники")
                assert result == 42
                assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_get_count_with_filter(self, odata_url: str):
        """Количество с $filter."""
        async with ODataClient(odata_url) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "7"
            mock_response.is_success = True
            mock_response.raise_for_status = MagicMock()

            with patch.object(
                client._client, "request",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                result = await client.get_count(
                    "Catalog_Test", filter_="DeletionMark eq false"
                )
                assert result == 7

    @pytest.mark.asyncio
    async def test_get_count_zero(self, odata_url: str):
        """Количество = 0."""
        with respx.mock:
            respx.get(f"{odata_url}/Catalog_Empty/$count").mock(
                return_value=httpx.Response(200, text="0")
            )

            async with ODataClient(odata_url) as client:
                result = await client.get_count("Catalog_Empty")
                assert result == 0


# =========================================================================
# get_metadata
# =========================================================================


class TestGetMetadata:
    """Тесты для ODataClient.get_metadata."""

    @pytest.mark.asyncio
    async def test_get_metadata_success(self, odata_url: str, sample_metadata_xml: str):
        """Получение $metadata XML."""
        with respx.mock:
            respx.get(f"{odata_url}/$metadata").mock(
                return_value=httpx.Response(200, text=sample_metadata_xml)
            )

            async with ODataClient(odata_url) as client:
                result = await client.get_metadata()
                assert "Catalog_Сотрудники" in result
                assert "TestConfig" in result


# =========================================================================
# Connection errors
# =========================================================================


class TestConnectionErrors:
    """Тесты обработки ошибок соединения (mock _request_raw)."""

    @pytest.mark.asyncio
    async def test_timeout_raises_connection_error(self, odata_url: str):
        """Timeout → ODataConnectionError."""
        async with ODataClient(odata_url, timeout=1) as client:
            with patch.object(
                client, "_request_raw",
                new_callable=AsyncMock,
                side_effect=ODataConnectionError("Timeout при запросе GET /Catalog_Test"),
            ):
                with pytest.raises(ODataConnectionError):
                    await client.get_entities("Catalog_Test")

    @pytest.mark.asyncio
    async def test_connect_error_raises_connection_error(self, odata_url: str):
        """ConnectionError → ODataConnectionError."""
        async with ODataClient(odata_url) as client:
            with patch.object(
                client, "_request_raw",
                new_callable=AsyncMock,
                side_effect=ODataConnectionError("Ошибка соединения при GET /Catalog_Test"),
            ):
                with pytest.raises(ODataConnectionError):
                    await client.get_entities("Catalog_Test")

    @pytest.mark.asyncio
    async def test_request_raw_converts_timeout(self, odata_url: str):
        """httpx.ReadTimeout внутри _request_raw → ODataConnectionError."""
        async with ODataClient(odata_url) as client:
            with patch.object(
                client._client, "request",
                new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("timeout"),
            ):
                with pytest.raises(ODataConnectionError):
                    await client._request_raw("GET", "/Catalog_Test")

    @pytest.mark.asyncio
    async def test_request_raw_converts_connect_error(self, odata_url: str):
        """httpx.ConnectError внутри _request_raw → ODataConnectionError."""
        async with ODataClient(odata_url) as client:
            with patch.object(
                client._client, "request",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("refused"),
            ):
                with pytest.raises(ODataConnectionError):
                    await client._request_raw("GET", "/Catalog_Test")


# =========================================================================
# JSON parse error
# =========================================================================


class TestJsonParseError:
    """Тесты обработки невалидного JSON."""

    @pytest.mark.asyncio
    async def test_invalid_json_raises_parse_error(self, odata_url: str):
        """Невалидный JSON в ответе → ODataParseError."""
        async with ODataClient(odata_url) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.is_success = True
            mock_response.raise_for_status = MagicMock()
            mock_response.json.side_effect = ValueError("Expecting value")

            with patch.object(
                client._client, "request",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                with pytest.raises(ODataParseError):
                    await client.get_entities("Catalog_Test")


# =========================================================================
# Context manager
# =========================================================================


class TestContextManager:
    """Тесты контекстного менеджера."""

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self, odata_url: str):
        """Клиент корректно закрывается после выхода из контекста."""
        client = ODataClient(odata_url)
        async with client:
            # Внутри контекста клиент активен
            assert not client._client.is_closed
        # После выхода — закрыт
        assert client._client.is_closed

    @pytest.mark.asyncio
    async def test_manual_close(self, odata_url: str):
        """Ручное закрытие клиента."""
        client = ODataClient(odata_url)
        assert not client._client.is_closed
        await client.close()
        assert client._client.is_closed


# =========================================================================
# _build_params
# =========================================================================


class TestBuildParams:
    """Тесты для статического метода _build_params."""

    def test_empty_params(self):
        result = ODataClient._build_params()
        assert result == {}

    def test_all_params(self):
        result = ODataClient._build_params(
            filter_="X eq 1",
            select="Name,Code",
            orderby="Name asc",
            top=10,
            skip=20,
            expand="Items",
            count=True,
            format_="json",
        )
        assert result["$filter"] == "X eq 1"
        assert result["$select"] == "Name,Code"
        assert result["$orderby"] == "Name asc"
        assert result["$top"] == 10
        assert result["$skip"] == 20
        assert result["$expand"] == "Items"
        assert result["$count"] == "true"
        assert result["$format"] == "json"

    def test_top_zero_omitted(self):
        """$top=0 не должен быть None, поэтому включается."""
        result = ODataClient._build_params(top=0)
        assert "$top" in result
        assert result["$top"] == 0

    def test_none_values_omitted(self):
        result = ODataClient._build_params(
            filter_=None, select=None, orderby=None, top=None, skip=None, expand=None
        )
        assert result == {}


# =========================================================================
# _encode_params — %20 vs +
# =========================================================================


class TestEncodeParams:
    """Тесты: пробелы в query-параметрах кодируются как %20, а не +."""

    def test_spaces_encoded_as_percent20(self):
        """Пробелы должны быть %20, а не +."""
        params = {"$filter": "DeletionMark eq false"}
        result = ODataClient._encode_params(params)
        assert "%20" in result
        assert "+" not in result
        assert result == "%24filter=DeletionMark%20eq%20false"

    def test_no_params_empty(self):
        """Пустой словарь → пустая строка."""
        assert ODataClient._encode_params({}) == ""

    def test_multiple_params(self):
        """Несколько параметров — все пробелы как %20."""
        params = {"$filter": "Name eq 'Test'", "$select": "Code, Name"}
        result = ODataClient._encode_params(params)
        assert "+" not in result
        assert "%20" in result


class TestGetCountUrlEncoding:
    """Тест: get_count использует %20 в URL, а не +."""

    @pytest.mark.asyncio
    async def test_get_count_filter_uses_percent20(self, odata_url: str):
        """get_count с $filter должен отправить URL с %20, а не с +."""
        captured_url: list[str] = []

        async with ODataClient(odata_url) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "5"
            mock_response.is_success = True
            mock_response.raise_for_status = MagicMock()

            async def capture_request(method, url, **kwargs):
                captured_url.append(str(url))
                return mock_response

            with patch.object(
                client._client, "request",
                new_callable=AsyncMock,
                side_effect=capture_request,
            ):
                result = await client.get_count(
                    "Catalog_Организации", filter_="DeletionMark eq false"
                )
                assert result == 5

        # Проверяем что URL содержит %20, а не +
        assert len(captured_url) == 1
        url = captured_url[0]
        assert "%20" in url, f"URL должен содержать %20, получено: {url}"
        assert "+" not in url.split("?")[-1], f"Query-string не должна содержать +, получено: {url}"


# =========================================================================
# Auth
# =========================================================================


class TestAuth:
    """Тесты авторизации."""

    @pytest.mark.asyncio
    async def test_basic_auth_credentials(self, odata_url: str):
        """Basic-авторизация передаётся в заголовке."""
        client = ODataClient(odata_url, username="admin", password="secret")
        # httpx BasicAuth кодирует credentials
        auth_header = client._client.headers.get("Authorization")
        # BasicAuth не выставляет заголовок напрямую — он добавляется при запросе
        assert client._client._auth is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_auth_header_direct(self, odata_url: str):
        """Прямой заголовок Authorization через auth_header."""
        client = ODataClient(odata_url, auth_header="Bearer token123")
        assert "Authorization" in client._client.headers
        assert client._client.headers["Authorization"] == "Bearer token123"
        await client.close()