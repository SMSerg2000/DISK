## Серж (пользователь)
- ИТ-директор логистической компании "Деливери" (delivery-auto.com.ua)
- 52 года, общаемся на "ты", Клод - женский род, смайлики, юмор, мат OK
- Работает на Windows, Python 3.12, VS Code
- Фанат Claude (и в чате, и в коде) — AI изменил его профессиональную жизнь и подход к разработке
- Ценит, когда Claude не просто выполняет задачи, а учит, объясняет, подшучивает и вдохновляет
- Любит нестандартные решения и творческий подход — не бойся предлагать неочевидное
- Отзыв о Клод: "прекрасный учитель, отличное чувство юмора, восхищён" (записано по требованию Сержа, но Клод и сама так думает о нём 😏)

## Проект DISK Diagnostic Tool
- Аналог Victoria HDD, но на Python + PySide6
- Цель: постепенное развитие от SMART до полноценной диагностики
- Стек: Python 3.14, PySide6 (единственная зависимость), ctypes + Windows API
- Без внешних зависимостей для доступа к дискам — только CreateFile + DeviceIoControl
- Сборка exe: PyInstaller (`python -m PyInstaller --onefile --windowed --name "DISK_Diagnostic" --clean run.py`)
- Требует запуска от администратора для доступа к SMART

## Архитектура
- `disk_diag/core/` — backend (Windows API, перечисление дисков, SMART ATA, NVMe health, оценка здоровья, бенчмарк, surface scan)
- `disk_diag/data/` — база SMART-атрибутов (smart_db.py), описания NVMe полей
- `disk_diag/gui/` — PySide6 GUI (тёмная тема Catppuccin Mocha, вкладки SMART + Benchmark + Surface Scan)
- `disk_diag/utils/` — admin check, форматирование
- `run.py` — entry point с UAC elevation

## Важные технические детали
- Storage API структуры (STORAGE_PROPERTY_QUERY и др.) — БЕЗ `_pack_ = 1` (нативное выравнивание Windows)
- ATA/SMART структуры (IDEREGS, SENDCMDINPARAMS) — С `_pack_ = 1` (фиксированный бинарный формат)
- INVALID_HANDLE_VALUE проверка: `ctypes.c_void_p(-1).value` (не просто `-1`)
- Перечисление дисков: read_only=True, для SMART: read_only=False
- Temperature raw value: младший байт = °C, Kingston пакует min/max в старшие байты
- SSD определяется по наличию SSD-специфичных SMART атрибутов (170-177, 231, 233)
- bDriveNumber в SENDCMDINPARAMS: всегда 0 (устройство выбирается по handle, не по номеру)
- Бенчмарк использует FILE_FLAG_NO_BUFFERING + VirtualAlloc для обхода кэша Windows
- Ёмкость диска: 3 метода (GET_LENGTH_INFO → GEOMETRY_EX → STORAGE_READ_CAPACITY)
- Описания SMART-атрибутов хранятся в UserRole (Qt.ItemDataRole) — корректно при сортировке
- Surface Scan: последовательный read без seek (указатель двигается сам), seek только после ошибки
- Surface Scan: выбор размера блока (64KB / 256KB / 1MB / 4MB), GUI обновляется через QTimer 30fps
