#!/bin/bash
# Запросить данные из справочника 1С через OData
#
# Использование:
#   bash query-catalog.sh <ИмяСправочника> [top] [select]
#
# Примеры:
#   bash query-catalog.sh Сотрудники
#   bash query-catalog.sh Сотрудники 5
#   bash query-catalog.sh Сотрудники 5 "Ref_Key,Description"
#
# Требования:
#   - env.json рядом с скриптом (см. env.example.json)
#   - curl, python

set -e

CATALOG_NAME="${1}"
TOP="${2:-10}"
SELECT="${3:-}"
ENV_FILE="${ENV_FILE:-env.json}"

if [ -z "$CATALOG_NAME" ]; then
  echo "Использование: bash query-catalog.sh <ИмяСправочника> [top] [select]"
  echo "Пример: bash query-catalog.sh Сотрудники 5 Ref_Key,Description"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Ошибка: файл '$ENV_FILE' не найден."
  echo "Скопируйте env.example.json в env.json и заполните credentials."
  exit 1
fi

ODATA_URL=$(python -c "import json; d=json.load(open('$ENV_FILE', encoding='utf-8')); print(d['default']['odata_url'])")
ODATA_AUTH=$(python -c "import base64,json; d=json.load(open('$ENV_FILE', encoding='utf-8')); u=d['default']['odata_user']; p=d['default']['odata_password']; print(base64.b64encode(f'{u}:{p}'.encode()).decode())")

# URL-кодируем имя справочника
ENCODED_NAME=$(python -c "from urllib.parse import quote; print(quote('$CATALOG_NAME'))")

# Строим URL
QUERY_URL="$ODATA_URL/Catalog_${ENCODED_NAME}?\$top=${TOP}&\$format=json"
if [ -n "$SELECT" ]; then
  QUERY_URL="${QUERY_URL}&\$select=${SELECT}"
fi

echo "Запрос: $QUERY_URL"
echo ""

curl -s \
  -H "Authorization: Basic $ODATA_AUTH" \
  -H "Accept: application/json" \
  "$QUERY_URL"
