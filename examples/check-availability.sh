#!/bin/bash
# Проверить, какие сущности опубликованы через OData
#
# Использование:
#   bash check-availability.sh
#
# Требования:
#   - env.json рядом с скриптом (см. env.example.json)
#   - curl, python

set -e

ENV_FILE="${1:-env.json}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Ошибка: файл '$ENV_FILE' не найден."
  echo "Скопируйте env.example.json в env.json и заполните credentials."
  exit 1
fi

ODATA_URL=$(python -c "import json; d=json.load(open('$ENV_FILE', encoding='utf-8')); print(d['default']['odata_url'])")
ODATA_AUTH=$(python -c "import base64,json; d=json.load(open('$ENV_FILE', encoding='utf-8')); u=d['default']['odata_user']; p=d['default']['odata_password']; print(base64.b64encode(f'{u}:{p}'.encode()).decode())")

echo "Запрашиваю список сущностей: $ODATA_URL/"
echo ""

curl -s \
  -H "Authorization: Basic $ODATA_AUTH" \
  -H "Accept: application/json" \
  "$ODATA_URL/" | python -c "
import json, sys
data = json.load(sys.stdin)
entities = data.get('value', [])
print(f'Опубликовано сущностей: {len(entities)}')
print()
for e in entities:
    print(f'  {e[\"name\"]}')
"
