# Деревья значений в управляемых формах 1С

## Form.xml — правильная структура

### Атрибут формы

Тип — `v8:ValueTree`. Колонки объявляются в секции `<Columns>`:

```xml
<Attribute name="ДеревоОбъектов" id="2">
    <Type>
        <v8:Type>v8:ValueTree</v8:Type>
    </Type>
    <Columns>
        <Column name="Наименование" id="3">
            <Type>
                <v8:Type>xs:string</v8:Type>
                <v8:StringQualifiers>
                    <v8:Length>200</v8:Length>
                    <v8:AllowedLength>Variable</v8:AllowedLength>
                </v8:StringQualifiers>
            </Type>
        </Column>
        <Column name="Включен" id="4">
            <Type><v8:Type>xs:boolean</v8:Type></Type>
        </Column>
    </Columns>
</Attribute>
```

### Элемент управления

Дерево — это `<Table>` с `<Representation>Tree</Representation>`:

```xml
<Table name="ДеревоОбъектов" id="15">
    <Representation>Tree</Representation>
    <DataPath>ДеревоОбъектов</DataPath>
    <HorizontalStretch>true</HorizontalStretch>
    <VerticalStretch>true</VerticalStretch>
    <SearchStringLocation>None</SearchStringLocation>
    <ViewStatusLocation>None</ViewStatusLocation>
    <SearchControlLocation>None</SearchControlLocation>
    <ContextMenu name="ДеревоОбъектовКонтекстноеМеню" id="16"/>
    <AutoCommandBar name="ДеревоОбъектовКоманднаяПанель" id="17">
        <Autofill>false</Autofill>
    </AutoCommandBar>
    <ExtendedTooltip name="ДеревоОбъектовРасширеннаяПодсказка" id="35"/>
    <ChildItems>
        <!-- Булева колонка — CheckBoxField -->
        <CheckBoxField name="ДеревоОбъектовВключен" id="18">
            <DataPath>ДеревоОбъектов.Включен</DataPath>
            <Title><v8:item><v8:lang>ru</v8:lang><v8:content> </v8:content></v8:item></Title>
            <EditMode>EnterOnInput</EditMode>
            <CheckBoxType>Auto</CheckBoxType>
            <ContextMenu name="ДеревоОбъектовВключенКонтекстноеМеню" id="19"/>
            <ExtendedTooltip name="ДеревоОбъектовВключенРасширеннаяПодсказка" id="36"/>
            <Events>
                <Event name="OnChange">ДеревоОбъектовВключенПриИзменении</Event>
            </Events>
        </CheckBoxField>
        <!-- Строковая колонка — InputField -->
        <InputField name="ДеревоОбъектовНаименование" id="20">
            <DataPath>ДеревоОбъектов.Наименование</DataPath>
            <ReadOnly>true</ReadOnly>
            <EditMode>EnterOnInput</EditMode>
            <HorizontalStretch>true</HorizontalStretch>
            <ContextMenu name="ДеревоОбъектовНаименованиеКонтекстноеМеню" id="21"/>
            <ExtendedTooltip name="ДеревоОбъектовНаименованиеРасширеннаяПодсказка" id="37"/>
        </InputField>
    </ChildItems>
</Table>
```

### Кнопки и команды

Кнопки в `AutoCommandBar` ссылаются на команды через `Form.Command.ИмяКоманды`:

```xml
<AutoCommandBar name="ФормаКоманднаяПанель" id="-1">
    <ChildItems>
        <Button name="КнопкаВключить" id="11">
            <Type>CommandBarButton</Type>
            <CommandName>Form.Command.Включить</CommandName>
            <ExtendedTooltip name="КнопкаВключитьРасширеннаяПодсказка" id="30"/>
        </Button>
    </ChildItems>
</AutoCommandBar>
```

Команды объявляются с `<Action>ИмяПроцедуры</Action>`, заголовок через `<v8:item>`:

```xml
<Commands>
    <Command name="Включить" id="8">
        <Title><v8:item><v8:lang>ru</v8:lang><v8:content>Применить</v8:content></v8:item></Title>
        <Action>Включить</Action>
    </Command>
</Commands>
```

### События формы

Объявляются в `<Events>` на уровне `<Form>` — сиблинг `<AutoCommandBar>`, `<ChildItems>`:

```xml
<Events>
    <Event name="OnOpen">ПриОткрытии</Event>
</Events>
```

---

## BSL — паттерны

### Сервер: РеквизитФормыВЗначение / ЗначениеВРеквизитФормы

В модуле формы на сервере атрибут `v8:ValueTree` доступен как `ДеревоЗначений`:

```bsl
&НаСервере
Процедура ЗаполнитьДеревоОбъектов()
    Дерево = РеквизитФормыВЗначение("ДеревоОбъектов", Тип("ДеревоЗначений"));
    Дерево.Строки.Очистить();

    Группа              = Дерево.Строки.Добавить();   // корневая строка
    Группа.Наименование = "Справочники";

    Строка              = Группа.Строки.Добавить();   // дочерняя строка
    Строка.Наименование = "Контрагенты";
    Строка.Включен      = Истина;

    ЗначениеВРеквизитФормы(Дерево, "ДеревоОбъектов"); // обязательно!
КонецПроцедуры
```

Для чтения на сервере (например, при сохранении):

```bsl
&НаСервере
Функция СобратьВыбранные()
    Дерево = РеквизитФормыВЗначение("ДеревоОбъектов", Тип("ДеревоЗначений"));
    Для Каждого Группа Из Дерево.Строки Цикл
        Для Каждого Строка Из Группа.Строки Цикл
            Если Строка.Включен Тогда
                // обработка
            КонецЕсли;
        КонецЦикла;
    КонецЦикла;
КонецФункции
```

### Клиент: ПолучитьЭлементы()

На клиенте атрибут доступен как `ДеревоФормы` через `ПолучитьЭлементы()`:

```bsl
&НаКлиенте
Процедура УстановитьВсеФлажки(Значение)
    Для Каждого Группа Из ДеревоОбъектов.ПолучитьЭлементы() Цикл
        Группа.Включен = Значение;
        Для Каждого Строка Из Группа.ПолучитьЭлементы() Цикл
            Строка.Включен = Значение;
        КонецЦикла;
    КонецЦикла;
КонецПроцедуры
```

### Развернуть группы при открытии

```bsl
&НаКлиенте
Процедура ПриОткрытии(Отказ)
    ЗаполнитьДеревоОбъектов(); // &НаСервере
    Для Каждого Группа Из ДеревоОбъектов.ПолучитьЭлементы() Цикл
        Элементы.ДеревоОбъектов.Развернуть(Группа.ПолучитьИдентификатор(), Ложь);
    КонецЦикла;
КонецПроцедуры
```

### Обработчик изменения флажка группы

```bsl
&НаКлиенте
Процедура ДеревоОбъектовВключенПриИзменении(Элемент)
    ТекущиеДанные = Элементы.ДеревоОбъектов.ТекущиеДанные;
    Если ТекущиеДанные = Неопределено Тогда
        Возврат;
    КонецЕсли;
    Если ТекущиеДанные.ЭтоГруппа Тогда
        Для Каждого Строка Из ТекущиеДанные.ПолучитьЭлементы() Цикл
            Строка.Включен = ТекущиеДанные.Включен;
        КонецЦикла;
    КонецЕсли;
КонецПроцедуры
```

---

## Сборка EPF

Использовать скилл `.claude/skills/epf-build/scripts/epf-build.ps1`.

Если база занята конфигуратором — запускать без параметров подключения:

```powershell
powershell.exe -NoProfile -File .claude/skills/epf-build/scripts/epf-build.ps1 `
    -SourceFile "processing/МояОбработка.xml" `
    -OutputFile "processing/МояОбработка.epf"
```

Скрипт создаёт временную файловую базу-заглушку, компилирует EPF и удаляет базу.

---

## Типичные ошибки

| Неправильно | Правильно |
|-------------|-----------|
| `<TreeField>` | `<Table><Representation>Tree</Representation>` |
| `lf:FormDataTree` как тип атрибута | `v8:ValueTree` |
| Колонки в `<ChildItems>` атрибута | Колонки в `<Columns>` атрибута |
| `<lf:PathElement>` в DataPath | Просто строка: `ДеревоОбъектов.Включен` |
| `<ActionName>` в команде | `<Action>ИмяПроцедуры</Action>` |
| `дерево.ПолучитьЭлементы()` на сервере | `РеквизитФормыВЗначение(...)` → `.Строки` |
| Забыть `ЗначениеВРеквизитФормы` | Всегда вызывать после изменений на сервере |
