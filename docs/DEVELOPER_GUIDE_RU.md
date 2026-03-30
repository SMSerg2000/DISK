# DISK Diagnostic Tool — Руководство разработчика

## 1. Обзор архитектуры

### Структура пакетов

```
disk_diag/
├── __init__.py          # Версия (__version__)
├── app.py               # QApplication, загрузка темы
├── i18n.py              # Локализация: tr("en", "ru"), lang.cfg
├── core/                # Backend (без GUI-зависимостей)
│   ├── constants.py     # IOCTL коды, Windows API константы
│   ├── structures.py    # ctypes Structure (SMART, Storage API, SCSI)
│   ├── winapi.py        # CreateFile, DeviceIoControl, ReadFile, WriteFile,
│   │                    # AlignedBuffer, volume lock/dismount, is_system_drive
│   ├── models.py        # Dataclass: DriveInfo, SmartAttribute, NvmeHealthInfo,
│   │                    # HealthStatus, BenchmarkResult, SurfaceScanResult, ScanMode
│   ├── drive_enumerator.py  # Сканирование PhysicalDrive0..31
│   ├── smart_ata.py     # ATA SMART: legacy IOCTL + ATA PT + SCSI SAT
│   ├── smart_nvme.py    # NVMe Health: QueryProperty (3 sizes) + ProtocolCommand
│   │                    # + SCSI_MINIPORT + WMI fallback
│   ├── smart_usb_nvme.py # USB-NVMe: JMicron/ASMedia/Realtek vendor SCSI
│   ├── health_assessor.py  # Health Score (0-100), TBW, WAF
│   ├── benchmark.py     # 7 тестов бенчмарка + temperature monitoring
│   └── surface_scan.py  # Сканирование поверхности (Ignore/Erase/Refresh/Write)
├── data/
│   ├── smart_db.py      # 70+ SMART-атрибутов (EN/RU), SmartAttributeInfo
│   └── nvme_fields.py   # NVMe поля (EN/RU), NvmeFieldInfo
├── gui/
│   ├── main_window.py   # Главное окно, меню, экспорт, SMART worker
│   ├── drive_selector.py    # QComboBox для выбора диска
│   ├── info_panel.py    # Панель информации (модель, серийный, ёмкость...)
│   ├── smart_table.py   # QTableWidget для ATA/NVMe SMART
│   ├── health_indicator.py  # Бейдж здоровья (GOOD/WARNING/CRITICAL)
│   ├── benchmark_panel.py   # 7 карточек + 4 графика + worker
│   ├── surface_panel.py     # Блок-карта + статистика + worker
│   └── theme.py         # Catppuccin Mocha QSS
├── utils/
│   ├── admin.py         # Проверка прав + UAC elevation
│   └── formatting.py    # Форматирование ёмкости, часов, температуры
└── resources/
    └── app.ico          # Иконка приложения
run.py                   # Entry point с UAC elevation
```

### Принцип: GUI → Core → Windows API

```
GUI (PySide6)
  │
  ├── QThread Workers (фоновые задачи)
  │     │
  │     ▼
  Core (чистый Python + ctypes)
  │     │
  │     ▼
  Windows API (kernel32.dll)
        │
        ▼
  PhysicalDriveN / \\.\X: / \\?\Volume{GUID}
```

Единственная внешняя зависимость: **PySide6**. Для работы с дисками — только `ctypes` + `kernel32.dll`.

---

## 2. Windows API слой (winapi.py)

### DeviceHandle

```python
class DeviceHandle:
    def __init__(self, drive_number=-1, read_only=False, flags=0, device_path=""):
        # Открывает \\.\PhysicalDriveN или device_path
```

Контекстный менеджер (`with DeviceHandle(...) as h:`). Методы:
- `ioctl(code, in_struct, out_size)` — DeviceIoControl с ctypes Structure
- `ioctl_raw(code, in_bytes, out_size)` — DeviceIoControl с сырыми байтами
- `ioctl_inplace(code, buffer)` — один буфер для input/output (NVMe)
- `read(ptr, size)` / `write(ptr, size)` — ReadFile / WriteFile
- `read_at(offset, ptr, size)` / `write_at(offset, ptr, size)` — seek + read/write
- `seek(offset)` — SetFilePointerEx

### Ключевые моменты

- **INVALID_HANDLE_VALUE**: `ctypes.c_void_p(-1).value` (не просто `-1`!)
- **SetLastError(0)** перед проверкой после CreateFileW
- **AlignedBuffer**: `VirtualAlloc` для выровненных по странице буферов (FILE_FLAG_NO_BUFFERING)

### Блокировка томов

```python
def lock_and_dismount_volumes(drive_number: int) -> list:
    # 1) Перебираем буквы A-Z
    # 2) FindFirstVolumeW/FindNextVolumeW для скрытых (EFI, Recovery)
    # 3) IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS → номер физ. диска
    # 4) FSCTL_LOCK_VOLUME + FSCTL_DISMOUNT_VOLUME
    # Возвращает список handle'ов (закрыть после записи!)
```

**Критично:** блокировка нужна для ВСЕХ режимов записи (Erase, Refresh, Write), иначе Windows блокирует запись в области смонтированных разделов.

### Определение системного диска

```python
def is_system_drive(drive_number: int) -> bool:
    # %SystemDrive% (обычно C:) → VOLUME_GET_VOLUME_DISK_EXTENTS → disk_number
```

---

## 3. Перечисление дисков (drive_enumerator.py)

Сканирует PhysicalDrive0..31:

```python
for n in range(32):
    with DeviceHandle(n, read_only=True) as h:
        # STORAGE_QUERY_PROPERTY → model, serial, firmware, bus_type
        # bus_type: SATA=0x0B, USB=0x07, NVMe=0x11
        # SMART_GET_VERSION → smart_supported
        # Ёмкость: GET_LENGTH_INFO → GEOMETRY_EX → STORAGE_READ_CAPACITY
```

**Storage API структуры — БЕЗ `_pack_ = 1`** (нативное выравнивание Windows).

---

## 4. Чтение SMART

### 4.1 ATA/SATA (smart_ata.py)

**Два метода:**

1. **Legacy IOCTL** (SMART_RCV_DRIVE_DATA) — для SATA дисков
   - `SENDCMDINPARAMS` / `SENDCMDOUTPARAMS` с `_pack_ = 1`
   - `bDriveNumber = 0` всегда (устройство выбирается по handle)
   - Перед чтением: `SMART_ENABLE_OPERATIONS`

2. **SCSI SAT** (IOCTL_SCSI_PASS_THROUGH) — для USB-SATA мостов
   - CDB opcode 0x85 (ATA Pass-Through 16)
   - PIO Data-In protocol (4 << 1)

**Парсинг:** 512 байт → 30 атрибутов по 12 байт (id, flags, current, worst, raw[6]).

**Health Level на атрибут:**
```python
if current <= threshold:
    health = CRITICAL
elif current < 100 and current <= threshold + 10:
    health = WARNING  # НЕ предупреждать если current=100!
elif is_critical and (raw & 0xFFFFFFFF) > 0 and attr_id in (5, 196, 197, 198):
    health = WARNING  # low32 маска для SandForce!
else:
    health = GOOD
```

### 4.2 NVMe (smart_nvme.py)

**Цепочка fallback (18 комбинаций):**

```
1. IOCTL_STORAGE_QUERY_PROPERTY (disk handle)
   └─ 3 proto sizes: 40B, 44B, 28B × 2 access modes (RW, RO)
2. IOCTL_STORAGE_QUERY_PROPERTY (adapter handle \\.\ScsiN:)
   └─ 3 proto sizes × 2 property IDs
3. IOCTL_STORAGE_PROTOCOL_COMMAND
4. IOCTL_SCSI_MINIPORT (NvmeMini)
5. PowerShell WMI (Get-StorageReliabilityCounter)
```

**КЛЮЧЕВОЙ УРОК:** все офсеты через `ctypes.sizeof()` — **НИКОГДА** не хардкодить размеры Windows структур!

### 4.3 USB-NVMe мосты (smart_usb_nvme.py)

```python
_BRIDGE_METHODS = [
    ("JMicron", _jmicron_get_smart),   # CDB 0xA1, 3-step
    ("ASMedia", _asmedia_get_smart),   # CDB 0xE6, 1-step
    ("Realtek", _realtek_get_smart),   # CDB 0xE4, 1-step
]
```

**JMicron 3-step:**
1. DATA_OUT: отправить NVMe команду (512 байт с сигнатурой "NVME")
2. DATA_IN: получить данные (512 байт SMART log)
3. (опционально) Completion

**Баг DATA_OUT:** для DATA_OUT ответ = только заголовок SCSI_PASS_THROUGH (56 байт). Не проверять длину ответа!

---

## 5. Оценка здоровья (health_assessor.py)

### Формула ATA Score

```python
score = 100
score -= min(40, reallocated_low32 * 2)      # ID 5
score -= min(40, uncorrectable_low32 * 5)     # ID 187, 198
score -= min(30, program_fail * 3)             # ID 171
score -= min(30, erase_fail * 3)               # ID 172
score -= min(20, pending_low32 * 4)            # ID 197
# + SSD Life Left (ID 231), Wear Leveling (ID 177)
# + Temperature (ID 194), CRC Errors (ID 199)
```

### SandForce packed raw

- **Критические атрибуты (5, 196-198):** `raw & 0xFFFFFFFF` (low 32 бита)
- **Power-On Hours (ID 9):** `raw & 0xFFFFF` (low 20 бит)
- **Определение packed:** `raw > 1,000,000`

### TBW расчёт

```python
# ATA: ID 241 (Total Host Writes) в LBA секторах
consumed_tb = host_writes_lba * 512 / (1024**4)
# NVMe: Data Units Written × 512000 / (1024**4)
rated_tb = capacity_tb * 600  # эвристика TLC consumer
remaining_days = (rated_tb - consumed_tb) / daily_write_tb
```

**TBW не показывается для HDD** (`_is_ssd()` проверяет наличие SSD-атрибутов).

---

## 6. База SMART атрибутов (smart_db.py)

### Структура

```python
@dataclass(frozen=True)
class SmartAttributeInfo:
    id: int
    name_en: str       # English name
    name_ru: str       # Russian name
    desc_en: str       # English description
    desc_ru: str       # Russian description
    is_critical: bool
    unit: Optional[str] = None

    @property
    def name(self) -> str:
        return tr(self.name_en, self.name_ru)  # вызывается каждый раз!
```

### Добавление нового атрибута

```python
# В SMART_ATTRIBUTES dict:
NEW_ID: _a(NEW_ID, "English Name", "Русское имя",
           "English description",
           "Русское описание", is_critical=True/False, "unit"),
```

Если атрибут SSD-специфичный — добавить ID в `SSD_INDICATOR_ATTRS`.

---

## 7. Движок бенчмарка (benchmark.py)

### Порядок фаз

```
1. Sequential Read (read_only)
2. Random 4K Read (read_only)
3. Drive Sweep (read_only, 200-point skip sampling)
── volume lock/dismount ──
4. Sequential Write (512 MB, os.urandom)
5. Random 4K Write (1000 writes)
6. Mixed I/O 70/30 (30 sec)
7. Write-Read-Verify (256 MB, MD5)
8. SLC Cache (up to 50 GB, cliff detection)
── volume unlock ──
```

### Честные результаты

- `FILE_FLAG_NO_BUFFERING` — обход файлового кэша Windows
- `FILE_FLAG_WRITE_THROUGH` — обход write-back кэша контроллера
- `os.urandom()` — случайные данные (контроллер не сжимает нули)
- `AlignedBuffer` (VirtualAlloc) — выровненный буфер

### I/O ошибки

Каждая write-фаза обёрнута в `try/except DiskAccessError`. При ошибке — логируется, добавляется в `result.io_errors`, бенчмарк продолжает следующую фазу.

### Температура

`_poll_temp()` вызывается каждые 5 секунд во время тестов. Открывает **отдельный handle** для чтения SMART (не мешает основному I/O).

---

## 8. Движок сканирования (surface_scan.py)

### Основной цикл

```python
for i in range(total_blocks):
    if mode == WRITE:
        # Пишем нули без чтения
        h.write(write_buf, bs)
    else:
        # Читаем
        h.read(buf, bs)
        # При ошибке → drill-down
        if category == ERROR:
            _drill_down(h, offset, bs, sector_buf, do_write)
        # При необходимости записи
        elif should_write:
            if REFRESH: memmove + write_at (те же данные)
            elif ERASE: memset(0) + write_at (нули)
```

### Drill-down

При ошибке блока (напр. 1 MB) — перечитываем по секторам (4096 байт):
- Находим конкретные битые LBA
- Пишем нули **только** в битые секторы
- Хорошие секторы не затрагиваются
- LBA битых секторов → `bad_sector_callback` → GUI в реалтайме

### Volume lock

```python
if writing:  # Erase, Refresh, Write — все!
    volume_handles = lock_and_dismount_volumes(drive_number)
```

---

## 9. GUI архитектура

### QThread + Signal/Slot

Все длительные операции в фоновых потоках:

```python
class _SmartWorker(QObject):
    finished = Signal(object)
    error = Signal(str)

    def run(self):
        # Чтение SMART в фоне
        result = read_smart(...)
        self.finished.emit(result)

# В GUI:
thread = QThread()
worker.moveToThread(thread)
thread.started.connect(worker.run)
worker.finished.connect(self._on_finished)
```

### Кастомные виджеты (QPainter)

- **BlockMapWidget** — сетка 12×12px ячеек, QTimer 30fps, агрегация по худшему
- **LineChartWidget** — универсальный линейный график (Drive Sweep, SLC Cache)
- **LatencyHistogramWidget** — 6 бинов с процентами
- **LatencyScatterWidget** — точки (offset_gb, latency_us)

---

## 10. Локализация (i18n.py)

### Механизм

```python
_lang = "ru"  # по умолчанию

def tr(en: str, ru: str) -> str:
    return ru if _lang == "ru" else en
```

### Где хранится lang.cfg

```python
if getattr(sys, 'frozen', False):
    # PyInstaller exe → рядом с exe
    os.path.join(os.path.dirname(sys.executable), "lang.cfg")
else:
    # Разработка → корень проекта
    os.path.join(os.path.dirname(__file__), "..", "lang.cfg")
```

### Добавление новых строк

Просто используйте `tr("English", "Русский")` в любом месте кода. Оба перевода inline — никаких ключей или файлов ресурсов.

---

## 11. Сборка и распространение

### PyInstaller

```bash
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed \
    --name "DISK_Diagnostic" \
    --icon "disk_diag/resources/app.ico" \
    --clean run.py
```

> Используйте `python -m pip`, не `pip` — это могут быть разные Python!

### Генерация иконки

Иконка создаётся программно через PySide6 QPainter → PNG → ICO (ручная упаковка).

---

## 12. Известные проблемы и решения

| Проблема | Причина | Решение |
|----------|---------|---------|
| SandForce packed raw | Контроллер пакует данные в 6 байт | low32 для критических, low20 для POH |
| USB-NVMe DATA_OUT | SCSI ответ = только заголовок | Не проверять длину для DATA_OUT |
| Ложные write errors в Refresh | Тома не заблокированы | `lock_and_dismount_volumes` для всех write |
| Ложный WARNING при current=100 | threshold+10 = 100 | Проверять `current < 100` перед предупреждением |
| PySide6 на WinServer 2016 | Qt 6.5+ требует Win10 1809+ | Нет решения, ограничение Qt |

---

## 13. Как добавить...

### Новый SMART-атрибут

1. Добавить в `disk_diag/data/smart_db.py`:
```python
NEW_ID: _a(NEW_ID, "English", "Русский", "Eng desc", "Рус описание", False),
```
2. Если SSD-специфичный → добавить в `SSD_INDICATOR_ATTRS`

### Новый USB-NVMe мост

1. В `disk_diag/core/smart_usb_nvme.py` добавить функцию:
```python
def _newbridge_get_smart(handle: DeviceHandle) -> bytes:
    cdb = bytearray(16)
    cdb[0] = 0xXX  # vendor opcode
    # ... заполнить CDB
    return _scsi_cmd(handle, cdb, _DATA_IN, _SMART_SIZE)
```
2. Добавить в `_BRIDGE_METHODS`

### Новый тест бенчмарка

1. В `disk_diag/core/benchmark.py` добавить метод `_run_new_test()`
2. Добавить поля результата в `BenchmarkResult` (models.py)
3. Добавить фазу в `run()` (в правильном месте — read или write)
4. В GUI: добавить карточку и обновить `_on_progress` / `_on_finished`

### Новая строка локализации

Просто оберните в `tr("English", "Русский")`. Импорт: `from ..i18n import tr`

---

*DISK Diagnostic Tool v1.6.0 — Руководство разработчика*
*Разработано Сержем и Клод (Anthropic AI)*
