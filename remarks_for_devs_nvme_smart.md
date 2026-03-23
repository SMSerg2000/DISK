# Замечания для разработчиков по отчёту `NVMe SMART: методы чтения на Windows`

## Короткий вывод

Основная проблема, скорее всего, **не в том, что Windows «не даёт» читать SMART**, а в том, что запрос в **Методе 1** упакован неверно: используются **жёстко забитые смещения и размеры** (`44`, `52`, `564`) вместо расчёта через реальные размеры структур.

Именно **Метод 1 (`IOCTL_STORAGE_QUERY_PROPERTY`) должен быть основным**, потому что это штатный путь для стандартных NVMe-запросов типа **Identify** и **Get Log Page / SMART Health Information**.

## Что в отчёте выглядит проблемно

### 1. Захардкоженные смещения и размеры

В отчёте используются такие значения:

- `ProtocolDataOffset = 44`
- данные считаются лежащими в `[52..563]`
- общий размер буфера = `564`

Это плохая практика для такого кода.

Правильный подход:

- описать структуры через `ctypes.Structure`
- все размеры и смещения брать через `ctypes.sizeof(...)`
- входной и выходной layout строить от фактических размеров структур, а не от «магических чисел»

Если разметка буфера не совпадает с тем, что ожидает драйвер, `DeviceIoControl` может вернуть `ERROR_INVALID_FUNCTION (1)` или дать пустой/битый ответ.

### 2. Слишком уверенное допущение про layout выходного буфера

В отчёте предполагается:

- `[0..51]` — `STORAGE_PROTOCOL_DATA_DESCRIPTOR`
- `[52..563]` — 512 байт Health Log

Но это нужно **не предполагать**, а **проверять после ответа**:

- `Version`
- `Size`
- `ProtocolSpecificData.ProtocolDataOffset`
- `ProtocolSpecificData.ProtocolDataLength`

То есть смещение полезных данных должно браться из поля `ProtocolDataOffset` возвращённой структуры, а не из заранее зашитого `52`.

### 3. Вероятно смешаны «логическое описание» и реальный ABI layout

В отчёте `STORAGE_PROTOCOL_SPECIFIC_DATA` описана как 44 байта. На практике полагаться на это как на «вечную константу» нельзя.

Нужен не текстовый расчёт «по офсетам на бумаге», а реальный layout через структуру `ctypes`.

## Что по методам

## Метод 1 — чинить, а не выбрасывать

`IOCTL_STORAGE_QUERY_PROPERTY` + `StorageDeviceProtocolSpecificProperty` + `ProtocolTypeNvme` + `NVMeDataTypeLogPage` — это правильное направление.

Что сделать:

1. Убрать все ручные `pack_into` с магическими офсетами.
2. Собрать буфер через структуры.
3. Использовать **единый буфер in/out**.
4. После `DeviceIoControl` валидировать descriptor и смещения.
5. Данные health log читать по вычисленному смещению, а не по фиксированному `52`.

## Метод 2 — не делать из него основной путь

`IOCTL_STORAGE_PROTOCOL_COMMAND` полезен для **vendor-specific** команд.

Для обычных NVMe команд вроде **Get Log Page** и **Identify** это не тот путь, на который стоит делать ставку как на основной. Даже если его удастся оживить через реестр, это не означает, что именно так работает Victoria.

Итог:

- оставьте его как экспериментальный/диагностический
- не стройте архитектуру на нём

## Метод 3 — тупиковый на современных Windows

`IOCTL_SCSI_MINIPORT` / `NvmeMini` на современных сборках Windows часто не работает так, как хотелось бы.

Ваш `ERROR_INVALID_FUNCTION (1)` здесь не выглядит неожиданностью.

Итог:

- не тратить на этот путь много времени
- максимум использовать как исторический/исследовательский след

## Что исправить прямо сейчас

## 1. Переписать packing буфера через `ctypes.Structure`

Нужны как минимум такие структуры:

- `STORAGE_PROPERTY_QUERY`
- `STORAGE_PROTOCOL_SPECIFIC_DATA`
- `STORAGE_PROTOCOL_DATA_DESCRIPTOR`

Принцип:

- выделяется один буфер
- в его начале лежит `STORAGE_PROPERTY_QUERY`
- в `AdditionalParameters` размещается `STORAGE_PROTOCOL_SPECIFIC_DATA`
- `ProtocolDataOffset = sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA)`
- `ProtocolDataLength = 512`

## 2. Не читать ответ по жёсткому офсету

После вызова нужно брать:

- `descriptor = STORAGE_PROTOCOL_DATA_DESCRIPTOR.from_buffer(...)`
- `data_offset = descriptor.ProtocolSpecificData.ProtocolDataOffset`
- `data_length = descriptor.ProtocolSpecificData.ProtocolDataLength`

И только потом вырезать полезные данные.

## 3. Добавить диагностику ответа

Перед разбором health log логировать:

- `GetLastError()`
- `bytes_returned`
- `descriptor.Version`
- `descriptor.Size`
- `ProtocolType`
- `DataType`
- `ProtocolDataOffset`
- `ProtocolDataLength`

Сейчас у вас слишком мало информации, чтобы уверенно утверждать, что именно драйвер «не поддерживает PropertyId». Возможно, он получает криво собранный буфер.

## 4. Сравнить свой запрос с эталонным примером Microsoft

Нужно буквально пройтись по полям и сверить:

- тот ли `PropertyId`
- тот ли `QueryType`
- тот ли `ProtocolType`
- тот ли `DataType`
- тот ли `RequestValue`
- корректно ли выставлен `ProtocolDataOffset`
- правильно ли рассчитан общий размер буфера

## Минимальный ориентир по логике кода

```python
import ctypes as ct

IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
StorageDeviceProtocolSpecificProperty = 50
PropertyStandardQuery = 0
ProtocolTypeNvme = 3
NVMeDataTypeLogPage = 2
NVME_LOG_PAGE_HEALTH_INFO = 0x02

class STORAGE_PROPERTY_QUERY(ct.Structure):
    _fields_ = [
        ("PropertyId", ct.c_uint32),
        ("QueryType", ct.c_uint32),
        ("AdditionalParameters", ct.c_ubyte * 1),
    ]

class STORAGE_PROTOCOL_SPECIFIC_DATA(ct.Structure):
    _fields_ = [
        ("ProtocolType", ct.c_uint32),
        ("DataType", ct.c_uint32),
        ("ProtocolDataRequestValue", ct.c_uint32),
        ("ProtocolDataRequestSubValue", ct.c_uint32),
        ("ProtocolDataOffset", ct.c_uint32),
        ("ProtocolDataLength", ct.c_uint32),
        ("FixedProtocolReturnData", ct.c_uint32),
        ("ProtocolDataRequestSubValue2", ct.c_uint32),
        ("ProtocolDataRequestSubValue3", ct.c_uint32),
        ("ProtocolDataRequestSubValue4", ct.c_uint32),
    ]

class STORAGE_PROTOCOL_DATA_DESCRIPTOR(ct.Structure):
    _fields_ = [
        ("Version", ct.c_uint32),
        ("Size", ct.c_uint32),
        ("ProtocolSpecificData", STORAGE_PROTOCOL_SPECIFIC_DATA),
    ]

health_size = 512
query_base_size = 8
proto_size = ct.sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA)
buf_size = query_base_size + proto_size + health_size

buf = (ct.c_ubyte * buf_size)()
ct.memset(buf, 0, buf_size)

# Заголовок STORAGE_PROPERTY_QUERY
ct.cast(buf, ct.POINTER(ct.c_uint32))[0] = StorageDeviceProtocolSpecificProperty
ct.cast(buf, ct.POINTER(ct.c_uint32))[1] = PropertyStandardQuery

# STORAGE_PROTOCOL_SPECIFIC_DATA начинается с offset 8
proto = STORAGE_PROTOCOL_SPECIFIC_DATA.from_buffer(buf, 8)
proto.ProtocolType = ProtocolTypeNvme
proto.DataType = NVMeDataTypeLogPage
proto.ProtocolDataRequestValue = NVME_LOG_PAGE_HEALTH_INFO
proto.ProtocolDataRequestSubValue = 0
proto.ProtocolDataRequestSubValue2 = 0
proto.ProtocolDataRequestSubValue3 = 0
proto.ProtocolDataRequestSubValue4 = 0
proto.ProtocolDataOffset = proto_size
proto.ProtocolDataLength = health_size

# Дальше DeviceIoControl(handle, IOCTL_STORAGE_QUERY_PROPERTY, buf, buf_size, buf, buf_size, ...)
# Затем разбор ответа через STORAGE_PROTOCOL_DATA_DESCRIPTOR
```

Это не готовый production-код, а **ориентир по правильной идее**:

- никаких магических чисел
- один буфер
- все размеры через `sizeof`
- разбор ответа по возвращённому descriptor

## Практический план действий

### Шаг 1
Переписать Метод 1 полностью, без сохранения старого packing-кода.

### Шаг 2
Сделать подробный debug output всех размеров структур и ключевых полей до и после `DeviceIoControl`.

### Шаг 3
Проверить один и тот же диск:

- своей новой реализацией
- `Get-StorageReliabilityCounter`
- Victoria

### Шаг 4
Если после исправления layout всё ещё будет расхождение — снять вызовы Victoria через API Monitor / Procmon и сравнить:

- на какое устройство она ходит
- каким IOCTL
- какого размера буферы
- какой layout входных данных

## Финальный вывод

Сейчас самое слабое место отчёта — не выбор идеи, а **реализация буфера**.

То есть проблема, скорее всего, не в том, что:

- «Windows не поддерживает SMART для этого диска»
- «Victoria знает секретный способ»
- «нужен только vendor-specific passthrough»

А в том, что ваш основной запрос в Методе 1, вероятно, **упакован неверно**.

Пока не будет сделана чистая реализация через `ctypes.Structure` и `sizeof`, любые выводы про «драйвер не поддерживает» выглядят преждевременными.
