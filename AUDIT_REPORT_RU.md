# Независимый аудит DISK Diagnostic Tool

Дата: 2026-06-07

## Короткий вывод

Серж, программа уже не игрушка: сильная база по SMART, NVMe fallback-цепочке, USB-SATA/USB-NVMe, GUI, экспорту, прямому Windows I/O и диагностике в стиле Victoria. Но в SSD-бенчмарках есть методический перекос: сейчас это скорее "быстрый destructive sanity/raw benchmark", а не воспроизводимое SSD-тестирование по SNIA/JEDEC.

Самый большой просчёт: write-бенчмарки работают по `PhysicalDriveN` и пишут с начала диска или по случайным LBA без core-level safety-модели. GUI предупреждает, но ядро и CLI позволяют обойти защиту. Для тестового пустого диска это нормально. Для живого диска это опасная хрень: можно снести начало диска, таблицу разделов, boot/EFI, файловые системы и данные.

Второй крупный просчёт: текущий Python-движок делает синхронный QD1 I/O. Это полезно для пользовательской latency/sanity-проверки, но не может честно показать NVMe Gen3/Gen4/Gen5 throughput/IOPS на очередях QD4/QD16/QD32, которые описаны в спецификации и в baseline-таблицах.

## Что проверено

- Markdown-документация: `README_RU.md`, `README.md`, `SSD_TESTING_SPEC.md`, `Roadmap.txt`, `docs/USER_GUIDE_RU.md`, `docs/DEVELOPER_GUIDE_RU.md`, `docs/nvme_smart_methods.md`, `docs/SandForce_POH_Issue.md`.
- `.docx` в `docs/`: техническое задание по SNIA/JEDEC и оценка ТЗ.
- Код: `cli.py`, `run.py`, `disk_diag/core/*`, `disk_diag/gui/*`, `disk_diag/data/*`.
- Проверка компиляции ключевых Python-модулей через `python -m py_compile` прошла успешно.

В проекте нет unit/integration-тестов. Проверка была статическим аудитом и компиляцией, без запуска raw I/O на дисках.

## Сильные стороны

1. Низкоуровневый слой сделан серьёзно: `DeviceHandle`, `DeviceIoControl`, `FILE_FLAG_NO_BUFFERING`, `FILE_FLAG_WRITE_THROUGH`, `VirtualAlloc`-буферы, volume lock/dismount.
2. SMART/NVMe часть сильная: ATA SMART, SAT, USB-NVMe мосты, NVMe QueryProperty/ProtocolCommand/SCSI_MINIPORT/WMI fallback.
3. Ты уже поймал реальные vendor-specific проблемы: SandForce packed raw, WMI fallback, USB-bridge нюансы.
4. Документация по SSD-методологии зрелая: там правильно описаны TRIM, SLC cache, preconditioning, steady-state, host writes delta, seed, environment snapshot.
5. GUI умеет предупреждать о write-тестах и системном диске. Это хороший первый слой защиты.

## Критические замечания

### 1. Write-бенчмарки пишут в начало физического диска

Код:

- `disk_diag/core/benchmark.py:542` - sequential write.
- `disk_diag/core/benchmark.py:465` - write-read-verify.
- `disk_diag/core/benchmark.py:592` - SLC cache test.
- `disk_diag/core/benchmark.py:343` и `:400` - random write и mixed I/O по случайным LBA.

`_run_sequential_write()`, `_run_verify()` и `_run_slc_cache()` открывают `PhysicalDriveN` и пишут с текущей позиции, то есть с начала диска. Это разрушительно не "вообще", а конкретно по самым болезненным зонам: MBR/GPT, bootloader, начало первого раздела.

Что улучшить:

- Ввести `WritePlan`/`TestTarget`: `raw_full_disk`, `raw_range`, `test_file`.
- По умолчанию write-тесты должны работать через файл на выбранном томе, а raw-доступ - только в явном lab/destructive режиме.
- Для raw-режима требовать отдельный core-level флаг вроде `destructive_ack="MODEL SERIAL"` или `--i-know-what-i-am-doing`.
- До старта показывать точный диапазон LBA/байт и ожидаемый write volume.
- Запретить raw write на системном диске на уровне core/CLI, а не только GUI.

### 2. CLI обходит GUI-защиту

Код:

- `cli.py:173` - `cmd_benchmark`.
- `cli.py:193` - создание `BenchmarkEngine`.
- `cli.py:264` - флаг `--write`.

`diskdiag.exe --benchmark 0 --write` запускает destructive write-набор без интерактивного подтверждения, без проверки системного диска, без оценки ресурса записи и без печати точного плана записи.

Что улучшить:

- `--write` не должен быть достаточным флагом.
- Добавить `--destructive-confirm MODEL_OR_SERIAL` или `--i-know-this-destroys-data`.
- Для системного диска либо полный запрет, либо отдельный ещё более жёсткий флаг.
- CLI должен печатать: диск, serial, capacity, interface, range, estimated writes, affected volumes.

### 3. Volume lock/dismount работает не fail-closed

Код:

- `disk_diag/core/winapi.py:473` - `_try_lock_volume`.
- `disk_diag/core/winapi.py:507` - `FSCTL_LOCK_VOLUME`.
- `disk_diag/core/winapi.py:514` - `FSCTL_DISMOUNT_VOLUME`.
- `disk_diag/core/winapi.py:521` - возвращает handle даже если lock/dismount failed.

Сейчас при неудачном lock/dismount код логирует warning, но всё равно возвращает handle. Дальше write-тест продолжает работу. Для destructive I/O это неправильная философия: если том не заблокирован, нужно явно останавливать операцию или переводить её в режим "unsafe, user explicitly accepted".

Что улучшить:

- Возвращать структуру `VolumeLockResult`: `locked`, `dismounted`, `failed_volumes`, `handles`.
- Для raw write требовать успешной блокировки всех томов на диске.
- Если lock не удался - abort по умолчанию.
- В GUI/CLI показывать, какие тома не заблокировались.

### 4. Surface Scan может не разблокировать тома при исключении

Код:

- `disk_diag/core/surface_scan.py:115` - lock volumes.
- `disk_diag/core/surface_scan.py:123` - работа с `DeviceHandle`.
- `disk_diag/core/surface_scan.py:269` - unlock после основного блока, но не в outer `finally`.

Если после lock произойдёт исключение до строки unlock, handles могут остаться открытыми до завершения процесса. В `BenchmarkEngine` сделано правильно через `try/finally` (`benchmark.py:130-149`), в `SurfaceScanEngine` нужно так же.

Что улучшить:

- Обернуть весь scan после lock в `try/finally`.
- В `finally` всегда вызывать `unlock_volumes(volume_handles)`.

## Высокий приоритет

### 5. Профили Benchmark в GUI не соответствуют поведению кода

Код:

- `disk_diag/gui/benchmark_panel.py:462-465` - Quick/Standard/Full/Stress.
- `disk_diag/gui/benchmark_panel.py:583` - `include_write = profile in ("standard", "full", "stress")`.
- `disk_diag/core/benchmark.py:131-137` - все write-фазы всегда запускаются при `include_write`.
- `disk_diag/core/benchmark.py:72-75` - реально отличается только `stress`.

По tooltip: Standard - basic write tests, Full - all tests including SLC cache. По факту Standard, Full и Stress запускают один и тот же набор write-фаз; Stress только увеличивает verify до 1 GB и SLC до 100 GB. Значит Standard тоже запускает SLC cache, хотя UI обещает другое.

Что улучшить:

- Сделать явный список фаз по профилям:
  - `quick`: seq read, random read, sweep.
  - `standard`: seq write, random write, verify малым объёмом.
  - `full`: mixed, SLC cache, expanded verify.
  - `stress`: длительный sustained write, temp guard, больше samples.
- Warning dialog должен показывать фактический план именно выбранного профиля.
- Для Stress warning сейчас пишет "SLC Cache: up to 50 GB", хотя код ставит 100 GB.

### 6. Текущий движок не реализует SNIA/JEDEC-воспроизводимость

Документация требует:

- `SSD_TESTING_SPEC.md:54-95` - pipeline с TRIM, host writes before/after, preconditioning, report.
- `SSD_TESTING_SPEC.md:379` - фиксация host writes до тестов.
- `SSD_TESTING_SPEC.md:396-453` - Purge/WIPC/WDPC/steady_state_valid.
- `SSD_TESTING_SPEC.md:494-495` и `:1017-1019` - фиксированный seed.
- `SSD_TESTING_SPEC.md:1149` - environment snapshot.

Код сейчас этого не делает:

- Нет seed и воспроизводимых offsets.
- Нет `host writes before/after`, поэтому нельзя честно посчитать расход ресурса от теста.
- Нет TRIM/free-space/environment confidence.
- Нет preconditioning/steady-state.
- Нет флага `steady_state_valid`.

Что улучшить:

- Добавить `BenchmarkSession`: `run_id`, `profile`, `seed`, `environment`, `preconditions`, `write_budget`.
- Перед write-тестами читать SMART/NVMe host writes, после тестов читать повторно.
- В отчёт добавить `test_writes_gb`, `% от rated TBW`, `steady_state_valid=false`.
- Для профилей без preconditioning честно писать: "best effort, not SNIA-comparable".

### 7. Синхронный QD1 не подходит для "полного" SSD performance

Код:

- `disk_diag/core/benchmark.py:204` - random 4K read.
- `disk_diag/core/benchmark.py:343` - random 4K write.
- `disk_diag/core/benchmark.py:400` - mixed I/O.
- Все операции идут последовательно через `read_at()` / `write_at()`; нет overlapped I/O.

Для QD1 это нормально и даже важно. Но для NVMe максимальные IOPS и throughput на QD16/QD32 такой движок не покажет. При этом `baselines.py` содержит ranges до 600k/1M IOPS, что QD1 Python-циклом физически не достижимо.

Что улучшить:

- Чётко назвать текущий тест `QD1 latency/sanity`.
- Для QD4/QD16/QD32 использовать:
  - DiskSpd adapter на Windows;
  - fio adapter для lab/cross-platform;
  - или собственный Windows overlapped I/O engine.
- Не сравнивать QD1 Python numbers с QD32 baseline.

### 8. Tail latency метрики статистически слабые

Код:

- `disk_diag/core/benchmark.py:57` - `RANDOM_COUNT = 1000`.
- `disk_diag/core/benchmark.py:258-261` - P95/P99/P99.9/P99.99.

При 1000 операциях P99.9 и P99.99 фактически превращаются в максимум или почти максимум. Для QoS это недостаточно. Документация правильно говорит, что хвосты latency важнее среднего, но текущий объём выборки слишком мал.

Что улучшить:

- Для Quick оставить P95/P99 и явно маркировать "low sample".
- Для QoS режима делать time-based тест 30-300 секунд и десятки/сотни тысяч операций.
- Считать долю операций `>1ms`, `>5ms`, `>10ms`, `>100ms`.
- Сохранять latency histogram/bins в JSON.

### 9. Паттерн записи не полностью random

Код:

- `disk_diag/core/benchmark.py:557-558` - один 1 MB random block для всего seq write.
- `disk_diag/core/benchmark.py:616-617` - один 1 MB random block для всего SLC test.
- `disk_diag/core/benchmark.py:359` - один 4 KB random block для всех random writes.

Комментарий говорит "не нулями - контроллер может сжимать", но один и тот же random block повторяется много раз. Для контроллеров с компрессией/дедупликацией это может исказить скорость и SLC-картину.

Что улучшить:

- Генерировать deterministic pseudo-random stream с фиксированным seed.
- Использовать ring buffer 64-1024 MB из неповторяющихся блоков.
- В отчёт писать `data_pattern=random_seeded`, `seed`.

### 10. Temperature monitoring заявлен шире, чем реализован

Документация:

- `README.md:33` - "Temperature monitoring - during all tests".
- `docs/DEVELOPER_GUIDE_RU.md:291-293` - `_poll_temp()` каждые 5 секунд во время тестов.

Код:

- `disk_diag/core/benchmark.py:100` - стартовая температура.
- `disk_diag/core/benchmark.py:327` - polling в sweep.
- В seq write, random write, mixed, verify, SLC cache polling нет.

Именно SLC/stress/write-тесты сильнее всего греют SSD, но температура там не опрашивается. Это может скрыть thermal throttling.

Что улучшить:

- Вызывать `_poll_temp()` во всех длинных циклах: seq read/write, mixed, verify, SLC.
- Добавить temp guard: warning при 60/70 C, optional pause/abort при 80 C.
- В SLC chart наложить temperature curve или хотя бы report start/max/end.

### 11. SLC cache test слишком эвристический

Код:

- `disk_diag/core/benchmark.py:58` - 50 GB max.
- `disk_diag/core/benchmark.py:73-75` - 100 GB в stress.
- `disk_diag/core/benchmark.py:649-655` - cliff как падение >40% от первых 3 точек.

Проблемы:

- У крупных SSD dynamic SLC cache может быть больше 50/100 GB.
- Первые 300 MB могут быть не representative.
- Thermal throttling может выглядеть как SLC cliff.
- Нет host writes delta.
- Нет temp polling во время SLC.
- Тест пишет с начала диска.

Что улучшить:

- Для raw lab-mode задавать target: `min(2x expected_slc, 10-30% capacity, user cap)`.
- Для user-mode делать file-based SLC test с заранее выделенным большим файлом.
- Отделять `slc_cliff`, `thermal_throttle`, `write_error`.
- Требовать temp log и host writes delta.
- Не писать "No cliff" как хорошую новость без проверки, что объём теста был достаточен.

## Surface Scan и SSD

### 12. Surface Scan методологически HDD-centric

Код:

- `disk_diag/core/surface_scan.py:1-15` - Victoria HDD модель.
- `disk_diag/core/models.py:166-171` - Ignore/Erase/Refresh/Write.
- `disk_diag/gui/surface_panel.py:428-431` - эти режимы доступны без разделения SSD/HDD.

Для HDD это полезно. Для SSD термин "поверхность", "секторы", "лечение", "Erase slow" может вводить в заблуждение. У SSD нет поверхности в HDD-смысле, а LBA не связан стабильно с физической NAND-ячейкой. Запись "медленного блока" на SSD не лечит сектор как на HDD, а расходует ресурс и меняет mapping/GC state.

Что улучшить:

- Если выбран HDD: оставить Surface Scan.
- Если выбран SSD: заменить вкладку на `SSD Read Consistency`:
  - latency map по диапазону LBA;
  - read consistency / dips;
  - error map;
  - no "repair" terminology;
  - optional destructive NAND stress отдельно и очень явно.

### 13. Range validation в Surface Scan слабая

Код:

- `disk_diag/core/surface_scan.py:76-90` - `total_blocks = max(n, 1)`.
- `disk_diag/gui/surface_panel.py:696-706` - LBA parsing.

Если пользователь введёт некорректный диапазон (`end_lba <= start_lba`, start beyond capacity), engine всё равно может попытаться читать/писать один блок. Это лучше валидировать до запуска.

Что улучшить:

- Проверять `0 <= start < end <= capacity`.
- Если `end=0`, трактовать как capacity.
- Не использовать `max(n, 1)` для отрицательных диапазонов.
- Показывать понятную ошибку до открытия диска.

## SMART, TBW, WAF

### 14. `rated_tb = capacity_tb * 600` нельзя считать номиналом

Код:

- `disk_diag/core/health_assessor.py:154-157` - ATA.
- `disk_diag/core/health_assessor.py:371-374` - NVMe.

Это полезная эвристика для consumer TLC, но не "rated TBW". Для QLC, enterprise, старых MLC, DRAM-less и разных vendor endurance цифры будут другими. В экспорте уже есть пометка "оценка", но название поля `tbw_rated_tb` может создавать ложную уверенность.

Что улучшить:

- Переименовать internally в `tbw_estimated_tb`, если нет model DB.
- Добавить model/endurance database.
- В UI писать "оценка по классу", а не "номинал", пока нет vendor spec.

### 15. ATA TBW/WAF units vendor-specific

Код:

- `disk_diag/core/health_assessor.py:144-152` - ID 241/233.
- `disk_diag/core/health_assessor.py:169-181` - WAF по ID 249/243.

ID 241, 233, 243, 249 у разных производителей имеют разные units. Где-то это LBAs, где-то GiB, где-то 32 MiB units, где-то vendor-specific raw. Сейчас часть трактовок захардкожена.

Что улучшить:

- Перенести TBW/WAF decoding в vendor profiles.
- Если профиль не распознан, показывать "unknown / raw only".
- WAF считать только когда units обеих величин подтверждены.

### 16. NVMe/ATA temperature parsing требует sanity checks

Код:

- `disk_diag/core/smart_nvme.py:45-47` - Kelvin to Celsius.
- `disk_diag/core/smart_ata.py:468-478` - ATA raw low byte.
- `disk_diag/core/health_assessor.py:107-114` - score temp через raw low byte.

Если NVMe temperature field нулевой или мусорный, можно получить отрицательную температуру. ATA temperature через low byte часто работает, но vendor-specific упаковки лучше декодировать через profile consistently.

Что улучшить:

- Для NVMe игнорировать значения вне разумного диапазона, например `< -20` или `> 120`.
- Для ATA temperature использовать `decode_raw(profile, attr_id, raw)` в score тоже, а не только в warning loop.

## Baselines и интерпретация

### 17. Baseline class определяется по seq read, но сравнивает разные режимы

Код:

- `disk_diag/data/baselines.py:70-85` - `detect_class`.
- `disk_diag/data/baselines.py:88-128` - compare.

NVMe Gen3/Gen4 определяется по seq read threshold. Это может ошибиться при thermal throttling, USB bridge, ограничении PCIe lanes, заполненном SLC, фоновой нагрузке. Random QD1 Python-тест потом сравнивается с диапазонами, в которых верхняя граница похожа на QD32/пиковые значения.

Что улучшить:

- Добавить PCIe generation/lane/link speed, если доступно.
- Разделить baselines: `qd1_user_latency`, `qd32_peak`, `sustained_write`.
- Выводить verdict как "advisory", не pass/fail.
- Не считать `pct_of_max` для QD1 Python теста против QD32-подобного max.

## Документация и версии

### 18. Version drift

Факты:

- `disk_diag/__init__.py:3` - `2.2.1`.
- `README_RU.md:1` и `README.md:1` - `v2.1.0`.
- `docs/USER_GUIDE_RU.md:449` - `v1.6.0`.
- `docs/DEVELOPER_GUIDE_RU.md:462` - `v1.6.0`.

Что улучшить:

- Вынести version badge/генерацию docs из `disk_diag.__version__`.
- Перед релизом делать docs consistency check.

### 19. Документация обещает больше, чем код реально делает

Примеры:

- "Temperature during all tests" - реально не all tests.
- "Standard/Full profiles" - в коде почти одинаковы.
- `SSD_TESTING_SPEC.md` описывает SNIA-like pipeline, но в коде пока quick/raw benchmark.
- Roadmap честно говорит, что нужно развести HDD Surface Scan и SSD Read Consistency, но UI пока общий.

Что улучшить:

- В README явно разделить:
  - implemented now;
  - planned by SSD spec;
  - lab/pro mode future.
- Добавить "methodology limitations" в benchmark export.

### 20. Python version should be explicit

Код использует syntax, который нормально компилируется на Python 3.12+, включая f-string expressions с такими кавычками, как в `health_assessor.py:505-511` и `main_window.py:233`, `:747`. README уже пишет Python 3.12+, но `requirements.txt` этого не фиксирует.

Что улучшить:

- Добавить `pyproject.toml` с `requires-python = ">=3.12"`.
- В build docs указать Python 3.12+ как hard requirement.

## Рекомендованный план улучшений

### Срочно, 1-2 дня

1. Починить safety-модель write-тестов: core-level destructive ack, запрет системного диска, fail-closed volume lock.
2. Развести Standard/Full/Stress профили в коде и warning dialog.
3. Добавить temp polling во все длинные write/SLC циклы.
4. Добавить в CLI destructive confirmation.
5. Исправить version drift в README/User Guide/Developer Guide.

### Ближайший шаг, 3-7 дней

1. Ввести `BenchmarkSession`: seed, profile, run_id, environment snapshot.
2. Перед/после write-тестов читать host writes и показывать реальный write delta.
3. Добавить write volume estimate до запуска.
4. Сделать SSD-specific вкладку вместо HDD Surface semantics для SSD.
5. Добавить unit-тесты на safety/profile/range/percentiles без реального диска через fake `DeviceHandle`.

### Среднесрочно

1. DiskSpd adapter для Windows: QD1/QD4/QD16/QD32, latency histogram, raw output.
2. fio adapter для lab/cross-platform.
3. Model/endurance database для TBW.
4. Baseline сравнение "same platform / same seed / same profile" вместо универсального pass/fail.

### Lab/pro режим

1. Purge statuses: `success|unsupported|failed|skipped_by_policy`.
2. WIPC/WDPC и `steady_state_valid`.
3. SNIA-like report с `comparable=yes|limited|no`.
4. Отдельный destructive NAND stress mode.

## Какие тесты стоит добавить

1. `BenchmarkEngine` profile plan:
   - quick не запускает write-фазы;
   - standard не запускает SLC, если так задумано;
   - full/stress запускают ожидаемые фазы.
2. Safety:
   - write fails when system disk and no explicit ack;
   - write fails when volume lock failed;
   - CLI refuses `--write` без confirm.
3. Surface range:
   - invalid ranges reject before I/O.
4. Percentiles:
   - корректные индексы и понятное поведение при малом `n`.
5. Partial I/O:
   - `read()`/`write()` returning fewer bytes should be treated as error or partial.
6. SMART decoding:
   - vendor-profile units for TBW/WAF.

## Итог

Твой проект уже хорош как Windows-дискодиагностика с сильным SMART/NVMe слоем. Главная доработка - перестать считать текущий raw benchmark "корректным SSD-testing tool" в профессиональном смысле. Он полезен, но его надо честно позиционировать как quick/sanity/QD1/raw destructive.

Если хочешь вывести программу на уровень, который сама же спецификация описывает, главный путь такой:

1. Safety first.
2. Reproducibility second: seed, environment, host writes delta.
3. Correct SSD methodology third: preconditioning/steady-state/lab mode.
4. Performance engine fourth: DiskSpd/fio/overlapped I/O для QD.

После этих шагов это будет уже не "аналог Victoria с SSD-фишками", а реально взрослая утилита для приёмки и сравнения SSD.
