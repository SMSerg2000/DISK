# NVMe SMART: методы чтения на Windows

## Проблема

NVMe SMART/Health Information — это 512-байтная структура (Log Page 02h),
которую контроллер NVMe хранит и обновляет. Содержит 16 полей: температуру,
износ, объём записи/чтения, часы работы, ошибки и т.д.

Victoria HDD читает все 16 полей на нашем KINGSTON SNV2S4000G (Phison E21T,
драйвер stornvme 10.0.26100.7934, Windows 11 build 26220).

Наша программа — нет. Ниже все методы, которые мы пробовали.

---

## Метод 1: IOCTL_STORAGE_QUERY_PROPERTY (protocol-specific)

**IOCTL код:** `0x002D1400`
**Целевое устройство:** `\\.\PhysicalDriveN` или `\\.\ScsiN:` (адаптер)

### Суть
Стандартный Windows API для чтения NVMe данных через storage stack.
Используется CrystalDiskInfo и большинством утилит.

### Как работает
Вызываем `DeviceIoControl` с `IOCTL_STORAGE_QUERY_PROPERTY`.
Входной буфер — `STORAGE_PROPERTY_QUERY` + `STORAGE_PROTOCOL_SPECIFIC_DATA`:

```
Offset  Size  Поле                          Значение
------  ----  ----------------------------  --------------------------
0       4     PropertyId                    50 (Device) или 49 (Adapter)
4       4     QueryType                     0 (PropertyStandardQuery)
8       4     ProtocolType                  3 (ProtocolTypeNvme)
12      4     DataType                      2 (NVMeDataTypeLogPage)
16      4     RequestValue                  0x02 (SMART/Health Info LID)
20      4     RequestSubValue               0
24      4     ProtocolDataOffset            44 (sizeof протокольных данных)
28      4     ProtocolDataLength            512 (размер Log Page)
32-51   20    Reserved поля                 0
```

**Выходной буфер** (564 байт):
- [0..51] — `STORAGE_PROTOCOL_DATA_DESCRIPTOR` (Version, Size, ProtocolSpecificData)
- [52..563] — сырые 512 байт NVMe Health Information Log Page

### Что пробовали

| Вариант | Device | PropertyId | Буферы | Handle | Результат |
|---------|--------|-----------|--------|--------|-----------|
| 1a | PhysicalDrive0 | 50 (Device) | Раздельные in/out | GENERIC_READ | error 1 |
| 1b | PhysicalDrive0 | 50 (Device) | Единый (inplace) | GENERIC_READ | error 1 |
| 1c | PhysicalDrive0 | 49 (Adapter) | Раздельные | GENERIC_READ | error 1 |
| 1d | PhysicalDrive0 | 49 (Adapter) | Единый | GENERIC_READ | error 1 |
| 1e | PhysicalDrive0 | 50 | Раздельные | GENERIC_READ\|WRITE | error 1 |
| 1f | PhysicalDrive0 | 50 | Единый | GENERIC_READ\|WRITE | error 1 |
| 1g | PhysicalDrive0 | 49 | Раздельные | GENERIC_READ\|WRITE | error 1 |
| 1h | PhysicalDrive0 | 49 | Единый | GENERIC_READ\|WRITE | error 1 |
| 1i | **Scsi0:** | 50 | Раздельные | GENERIC_READ\|WRITE | error 1 |
| 1j | **Scsi0:** | 50 | Единый | GENERIC_READ\|WRITE | error 1 |
| 1k | **Scsi0:** | 49 | Раздельные | GENERIC_READ\|WRITE | error 1 |
| 1l | **Scsi0:** | 49 | Единый | GENERIC_READ\|WRITE | error 1 |

**Ошибка:** `ERROR_INVALID_FUNCTION (1)` — драйвер не поддерживает этот PropertyId.

### Код (Python/ctypes)
```python
buf = bytearray(564)
struct.pack_into("<I", buf, 0, 50)   # PropertyId
struct.pack_into("<I", buf, 4, 0)    # QueryType
struct.pack_into("<I", buf, 8, 3)    # ProtocolTypeNvme
struct.pack_into("<I", buf, 12, 2)   # NVMeDataTypeLogPage
struct.pack_into("<I", buf, 16, 0x02) # SMART/Health Info
struct.pack_into("<I", buf, 24, 44)  # DataOffset
struct.pack_into("<I", buf, 28, 512) # DataLength

result = DeviceIoControl(handle, 0x002D1400, buf, 564, out_buf, 564, ...)
```

---

## Метод 2: IOCTL_STORAGE_PROTOCOL_COMMAND (прямая NVMe Admin команда)

**IOCTL код:** `0x002DD3C0`
**Целевое устройство:** `\\.\PhysicalDriveN`

### Суть
Отправка «сырой» NVMe Admin команды через storage stack.
Позволяет послать любую команду: Get Log Page, Identify, Get Features и т.д.

### Как работает
Буфер содержит `STORAGE_PROTOCOL_COMMAND` (заголовок 80 байт + NVMe SQE 64 байта)
и место для ответа:

```
Offset  Size  Поле                          Значение
------  ----  ----------------------------  ----------------------------------
0       4     Version                       1
4       4     Length                         144 (80 + 64)
8       4     ProtocolType                  3 (NVMe)
12      4     Flags                         0 или 0x80000000 (ADAPTER_REQUEST)
16      4     ReturnStatus                  [выходное]
20      4     ErrorCode                     [выходное]
24      4     CommandLength                 64 (NVMe SQE)
28      4     ErrorInfoLength               64
32      4     DataToDeviceTransferLength    0
36      4     DataFromDeviceTransferLength  512
40      4     TimeOutValue                  10 (секунд)
44      4     ErrorInfoOffset               144
48      4     DataToDeviceBufferOffset      0
52      4     DataFromDeviceBufferOffset    208
56      4     CommandSpecific               1 (GET_LOG_PAGE_DATA)

--- NVMe Submission Queue Entry (64 байта) ---
80      4     CDW0 (Opcode)                 0x02 (Get Log Page)
84      4     NSID                          0xFFFFFFFF (broadcast)
88-119  ...   Reserved/PRP                  0 (драйвер заполняет PRP)
120     4     CDW10                         0x007F0002 (NUMDL=127, LID=2)
124-143 ...   CDW11-CDW15                   0

--- Error Info (64 байта) ---
144-207       [выходное]

--- Данные ответа (512 байт) ---
208-719       NVMe Health Information Log Page
```

### Что пробовали

| Flags | Handle | Результат |
|-------|--------|-----------|
| 0x80000000 (ADAPTER_REQUEST) | GENERIC_READ\|WRITE | error 87 |
| 0 (DEVICE_REQUEST) | GENERIC_READ\|WRITE | error 87 |

**Ошибка:** `ERROR_INVALID_PARAMETER (87)` — параметры неправильные.

### Причина
**Требуется ключ реестра:**
```
HKLM\SYSTEM\CurrentControlSet\Services\stornvme\Parameters\Device
AllowProtocolCommand = 1 (REG_DWORD)
```
Без этого ключа stornvme возвращает error 87. Это защита от отправки
разрушительных NVMe команд (Format NVM, Secure Erase, Firmware Update).
Работает только после перезагрузки.

**Victoria работает БЕЗ этого ключа** — значит она использует другой метод.

---

## Метод 3: IOCTL_SCSI_MINIPORT (NvmeMini)

**IOCTL код:** `0x0004D008`
**Целевое устройство:** `\\.\ScsiN:` (адаптер, не диск!)

### Суть
Прямое обращение к miniport-драйверу (stornvme.sys) через SCSI-порт.
Используется smartmontools (smartctl) на Windows.

### Как работает
Буфер начинается с `SRB_IO_CONTROL`, далее `NVME_PASS_THROUGH_IOCTL`:

```
Offset  Size  Поле                          Значение
------  ----  ----------------------------  ----------------------------------
=== SRB_IO_CONTROL (28 байт) ===
0       4     HeaderLength                  28
4       8     Signature                     "NvmeMini" (ASCII)
12      4     Timeout                       10 (секунд)
16      4     ControlCode                   0xE0002000
20      4     ReturnCode                    [выходное]
24      4     Length                         636 (данные после SRB_IO_CONTROL)

=== NVME_PASS_THROUGH_IOCTL ===
28      24    VendorSpecific[6]             0
52      64    NVMeCmd[16]                   NVMe SQE (CDW0=0x02, NSID=FFFFFFFF,
                                            CDW10=0x007F0002)
116     16    CplEntry[4]                   [выходное — NVMe CQE]
132     4     Direction                     2 (read from device)
136     4     QueueId                       0 (admin queue)
140     4     DataBufferLen                 512
144     4     MetaDataLen                   0
148     4     ReturnBufferLen               664 (всего)
152     512   DataBuffer                    [выходное — Health Log Page]
```

### Результат
Отправляем на `\\.\Scsi0:` → **error 1 (Invalid Function)**.
Miniport stornvme на этом билде Windows не поддерживает NvmeMini passthrough.

---

## Метод 4: PowerShell / WMI (текущий workaround)

**Команда:** `Get-StorageReliabilityCounter`

### Суть
Windows Management Instrumentation — высокоуровневый API.
Полностью другой путь: PowerShell → CIM → WMI Provider → Storage Stack.

### Как работает
```powershell
$disk = Get-PhysicalDisk | Where-Object DeviceId -eq '0'
$r = $disk | Get-StorageReliabilityCounter
$r | Select-Object Temperature, Wear, PowerOnHours, ...
```

### Результат
Возвращает JSON, но данных мало:
```json
{
  "Temperature": 46,
  "Wear": 0,
  "PowerOnHours": null,
  "StartStopCycleCount": null,
  "ReadErrorsUncorrected": null,
  "ReadLatencyMax": 1008,
  "WriteLatencyMax": 1015
}
```

Только **Temperature** и **Wear** (Percentage Used) реально доступны.
Остальные поля — null.

### Нюансы реализации
- `CREATE_NO_WINDOW` — чтобы окно PowerShell не мелькало
- Timeout 15 секунд
- Первый запуск ~6-8 сек (прогрев PowerShell), повторные ~2 сек

---

## Что ЕЩЁ НЕ пробовали

### 5. IOCTL_SCSI_PASS_THROUGH + SCSI LOG SENSE
**IOCTL:** `0x0004D004`
stornvme.sys реализует SCSI Translation Layer (SNTL) — SCSI команды
транслируются в NVMe. Команда LOG SENSE (opcode 0x4D) с определённым
page code может транслироваться в NVMe Get Log Page.
Вопрос: какой page code для SMART/Health?

### 6. Reverse-engineering Victoria HDD
Запустить Victoria под **API Monitor** или **Procmon** (Process Monitor)
и посмотреть:
- Какие IOCTL она вызывает (код, размер буферов)
- На какое устройство (PhysicalDrive? Scsi? другое?)
- Содержимое входного буфера
Это **самый надёжный способ** узнать, как Victoria это делает.

### 7. Vendor-specific CDB через SCSI passthrough
Некоторые NVMe контроллеры (Samsung, Intel) поддерживают
vendor-specific SCSI CDB (opcode 0xC0-0xFF) для NVMe passthrough.
Для Phison E21T — формат CDB неизвестен.

### 8. Direct PCI BAR0 access
NVMe контроллер — PCIe-устройство с BAR0, через который можно отправлять
NVMe Admin команды напрямую. Требует:
- Найти BAR0 адрес через SetupDi / PCI config space
- Memory-map физический адрес (нужен kernel driver)
- Записать команду в Admin SQ, прочитать ответ из Admin CQ

Victoria может использовать этот метод (в комплекте может быть .sys драйвер).

---

## Системная информация

```
Диск:      KINGSTON SNV2S4000G (NVMe, 4TB)
Контроллер: Phison E21T (PCI VEN_2646 DEV_5019)
Драйвер:   stornvme.sys 10.0.26100.7934
ОС:        Windows 11 Enterprise build 26220
SCSI порт:  0 (\\.\Scsi0:)

Реестр:
  HKLM\...\stornvme\Parameters\Device
  - AllowProtocolCommand = ОТСУТСТВУЕТ (нужен для метода 2)
```

## Рекомендованный следующий шаг

**Procmon / API Monitor на Victoria** — 30 минут работы, 100% ответ.
Запустить Procmon, фильтр по Process Name = victoria.exe, показать только
DeviceIoControl. Посмотреть IOCTL код, устройство и буферы.
