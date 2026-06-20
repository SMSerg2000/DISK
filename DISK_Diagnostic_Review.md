# DISK Diagnostic Tool v2.4.5  
## Замечания, риски и предложения по улучшению

**Автор ревью:** Ариэль  
**Для:** Serg  
**Дата:** 2026-06-20  
**Формат:** инженерное ревью документации, архитектуры, UX-безопасности и диагностической методологии

---

## 1. Executive Summary

По документации проект выглядит как серьёзная Windows-утилита диагностики HDD/SSD, а не как демонстрационный скрипт. Особенно сильны:

- прямой доступ к дискам через Windows API без внешних disk-библиотек;
- разделение backend/core и GUI;
- fallback-цепочки для ATA/SATA/NVMe/USB;
- защита от false-positive интерпретаций SMART/NVMe;
- аккуратная работа с `ctypes.sizeof()` вместо хрупких hardcoded offsets;
- fail-closed подход к volume locking;
- понимание различий между HDD и SSD;
- честное разделение QD1 latency и маркетинговых QD32/queue-depth показателей.

Главный риск проекта сейчас находится не в SMART-декодере, а в **пользовательской безопасности destructive write-операций**. В документации и интерфейсе нельзя оставлять ни малейшего ощущения, что запись по raw-диску “почти безопасна”, если первые 1 GiB защищены. Это защита от мгновенного уничтожения загрузочной области, но не защита пользовательских данных.

**Ключевой вывод:** перед публичным релизом нужно в первую очередь доработать Safety/UX вокруг write-бенчмарков и Surface Scan write/erase/refresh режимов.

---

## 2. Приоритеты

### P0 — обязательно до публичного релиза

| № | Область | Что сделать | Почему важно |
|---|---|---|---|
| 1 | Safety wording | Убрать или переписать формулировку `disk stays bootable` | Она может создать ложное чувство безопасности |
| 2 | GUI destructive confirmation | Ввести typed confirmation по serial/model для всех raw write операций | CLI защищён лучше, чем GUI |
| 3 | Benchmark profiles | Переименовать `standard`, если он пишет на диск | `Standard` звучит безопасно, хотя операция destructive |
| 4 | SSD Surface Scan | Ограничить/скрыть healing-режимы для SSD | Запись “для лечения” на SSD методологически вредна |
| 5 | README consistency | Исправить `7 tests` vs `8 benchmark tests` | Видимый дефект качества документации |
| 6 | Known Limitations | Добавить явный раздел ограничений | Повышает доверие и снижает неверные ожидания |

### P1 — очень желательно

| № | Область | Что сделать | Зачем |
|---|---|---|---|
| 1 | Health Score | Добавить Confidence Score | Цифра здоровья без уверенности выглядит слишком категорично |
| 2 | History | Использовать историю для trend analysis | Динамика важнее разового снимка |
| 3 | SMART/NVMe self-tests | Добавить запуск/просмотр self-test и error logs | Это стандартная часть диагностики накопителей |
| 4 | Support Bundle | Экспорт диагностического пакета | Нужно для разбора проблем у пользователей |
| 5 | Privacy | Redact serial numbers в экспорте | Серийники не всегда можно светить в issue/logs |
| 6 | Temperature safety | Abort/pause thresholds для стресс-тестов | Защита от перегрева и ложных выводов |

### P2 — развитие продукта

| № | Область | Что сделать |
|---|---|---|
| 1 | Benchmark | QD4/QD16/QD32 через overlapped I/O |
| 2 | SLC Cache | Улучшенный cliff detection с несколькими ступенями |
| 3 | Random writes | Проверять, не стал ли `os.urandom()` bottleneck |
| 4 | Verify | Заменить MD5 на BLAKE2b/SHA-256 |
| 5 | Enumeration | Расширить перебор за пределы `PhysicalDrive0..31` |
| 6 | TBW | Добавить vendor endurance DB вместо одного эвристического коэффициента |

---

## 3. Главный риск: destructive write операции

### 3.1. Проблема

В документации указано, что write-тесты стартуют после первого 1 GiB, чтобы защитить MBR/GPT/EFI-зону. Это хорошая защита, но её нельзя подавать как гарантию безопасности.

Фраза вида:

```md
MBR/GPT/EFI zone protection — all write tests start past the first 1 GiB (disk stays bootable)
```

опасна, потому что пользователь может прочитать её так:

> “Программа пишет, но диск останется рабочим”.

На самом деле правильнее:

> “Программа не трогает начало диска, но любые raw write-тесты могут уничтожить данные дальше первого 1 GiB”.

### 3.2. Рекомендованная формулировка

```md
MBR/GPT/EFI zone protection — write tests avoid the first 1 GiB to reduce the risk of destroying partition and boot metadata.

This does NOT make write tests safe for existing data. Any raw write operation can overwrite user data beyond the protected area.
```

### 3.3. GUI-подтверждение

Для любого режима, который пишет напрямую в `\\.\PhysicalDriveN`, нужен отдельный destructive workflow.

Рекомендуемый текст:

```text
DESTRUCTIVE RAW DISK WRITE

This operation writes directly to PhysicalDriveN.
It can permanently destroy existing data on this disk.

Drive:
Model: <model>
Serial: <serial>
Capacity: <capacity>

To continue, type the exact disk serial number:
[________________]

If the serial number is unavailable, type:
DESTROY PHYSICALDRIVE<N>
```

Важно:

- кнопка Continue неактивна, пока текст не совпал;
- подтверждение нужно не только для benchmark, но и для Surface Scan Write/Erase/Refresh, если режим пишет на диск;
- для системного диска отдельный отказ по умолчанию;
- если разрешить системный диск через override, нужен ещё более жёсткий сценарий подтверждения.

---

## 4. Названия benchmark-профилей

### 4.1. Проблема

Профиль `standard` включает запись. Слово `Standard` звучит как обычный безопасный тест. Для диагностической утилиты это плохой UX-сигнал.

Пользователь не обязан знать, что внутри `standard` есть raw write.

### 4.2. Предлагаемые названия

| Старое название | Новое название |
|---|---|
| Quick | Quick Read-only |
| Standard | Destructive Write Benchmark |
| Full | Full Destructive Benchmark |
| Stress | Endurance Stress Test |

### 4.3. Отдельный безопасный режим

Нужен режим по умолчанию:

```text
Standard Safe Test
```

Состав:

- SMART/NVMe Health;
- Sequential Read;
- Random 4K Read QD1;
- Full Drive Read Sweep;
- temperature monitoring;
- history comparison;
- без raw write.

Опционально можно добавить file-based write test через временный файл на выбранном томе. Это не заменяет raw benchmark, но безопаснее для обычного пользователя.

---

## 5. README: несоответствие количества тестов

В README указано `Benchmark (7 tests)`, но в структуре проекта `benchmark.py` описан как `8 benchmark tests`.

Фактически по списку получается:

1. Sequential Read  
2. Sequential Write  
3. Random 4K Read  
4. Random 4K Write  
5. Mixed I/O 70/30  
6. Write-Read-Verify  
7. SLC Cache Test  
8. Full Drive Read Sweep  

Нужно выбрать один вариант:

```md
### Benchmark (8 tests)
```

или явно объяснить, что read/write пары считаются одним типом теста.

Лучше поставить `8 tests`, потому что для пользователя это отдельные сценарии.

---

## 6. Health Score: нужен Confidence Score

### 6.1. Проблема

Health Score 0–100 выглядит авторитетно. Но разные источники данных имеют разную полноту:

- полноценный ATA SMART с thresholds;
- NVMe Health Log через Storage Protocol;
- USB bridge passthrough;
- WMI fallback;
- неполные поля;
- неизвестный vendor layout;
- подозрительные raw-значения.

Одинаково уверенно показывать `95/100` во всех случаях неправильно.

### 6.2. Предложение

Добавить рядом:

```text
Health Score: 92/100
Confidence: Medium
Reason: NVMe data was read through WMI fallback; some fields are unavailable.
```

Или:

```text
Health Score: UNKNOWN
Confidence: Low
Reason: SMART transport returned empty or incomplete data.
```

### 6.3. Пример уровней

| Confidence | Условия |
|---|---|
| High | SMART/NVMe прочитан напрямую, поля полные, vendor profile известен |
| Medium | USB bridge passthrough, часть данных валидна, но не всё подтверждено |
| Low | WMI fallback, неизвестный layout, неполные данные |
| Unknown | Данные отсутствуют или выглядят подозрительно |

### 6.4. Почему это важно

Диагностическая программа должна показывать не только “что она думает”, но и “насколько она уверена”. Это снижает риск ложного спокойствия и ложной паники.

---

## 7. История тестов должна влиять на диагноз

### 7.1. Текущая сильная сторона

Наличие SQLite history — хорошая база.

### 7.2. Что добавить

Нужен блок trend analysis:

```text
Trend since previous test:
- Reallocated Sectors: 0 → 0
- Pending Sectors: 0 → 0
- CRC Errors: 12 → 12 stable
- Unsafe Shutdowns: 18 → 19 +1
- Media Errors: 0 → 0

Trend status: Stable
```

Или:

```text
Trend status: Rapid degradation
Reason:
- Reallocated Sectors increased from 1 to 17 in 7 days.
- Pending Sectors increased from 0 to 8.
```

### 7.3. Почему это важно

Один и тот же SMART-атрибут имеет разный смысл в зависимости от динамики:

| Ситуация | Интерпретация |
|---|---|
| CRC Errors = 12 и год не растёт | Старый кабельный инцидент |
| CRC Errors = 12 → 900 за день | Текущая проблема кабеля/порта/питания |
| Reallocated = 1 стабильно 3 года | Наблюдать |
| Reallocated = 1 → 4 → 17 за неделю | Срочная замена |

---

## 8. SMART/NVMe self-test и error logs

### 8.1. Чего не хватает

По документации не видно поддержки:

- ATA SMART Short Self-test;
- ATA SMART Extended Self-test;
- SMART Self-test Log;
- SMART Error Log;
- NVMe Device Self-test;
- NVMe Error Information Log;
- NVMe Identify Controller/Namespace для capability.

### 8.2. Зачем это нужно

SMART-атрибуты — пассивный снимок состояния. Self-test позволяет накопителю выполнить внутреннюю диагностику и вернуть результат.

### 8.3. Рекомендуемый UI

```text
Diagnostics:
[Run Short Self-test]
[Run Extended Self-test]
[View Self-test Log]
[View Error Log]
```

С предупреждением:

```text
Self-test is non-destructive, but may take from minutes to hours.
Performance may be reduced while the test is running.
```

---

## 9. Surface Scan: SSD надо отделить жёстче

### 9.1. Проблема

В технической документации честно указано, что Surface Scan HDD-centric и для SSD имеет ограничения. Но в README Surface Scan выглядит как общий инструмент для всех накопителей.

Для SSD:

- LBA не равен физической NAND-ячейке;
- latency не является прямым индикатором wear;
- “лечение” записью не лечит SSD;
- лишняя запись расходует ресурс.

### 9.2. Что сделать в GUI

Если накопитель определён как SSD/NVMe:

- `Ignore` оставить как `Readability Scan`;
- `Erase bad sectors` скрыть или пометить `HDD only`;
- `Refresh` скрыть или требовать жёсткого подтверждения;
- `Erase +Slow` не предлагать;
- `WRITE !!!` оставить только через destructive workflow.

### 9.3. Текст предупреждения

```text
SSD detected.

Surface scan does not test physical NAND cells directly.
For SSD health assessment, use SMART/NVMe Health, Media Errors, Percentage Used and Error Logs.

Write/Erase/Refresh modes can consume SSD write endurance and are not recommended as a repair method.
```

---

## 10. QD1 методология честная, но неполная

### 10.1. Сильная сторона

QD1 latency как показатель отзывчивости системы — правильная методология. Хорошо, что она отделена от маркетинговых QD32 IOPS.

### 10.2. Ограничение

Для SSD-бенчмарка полезно иметь несколько queue depth:

| QD | Что показывает |
|---|---|
| QD1 | пользовательская отзывчивость |
| QD4 | лёгкая многозадачность |
| QD16 | тяжёлая рабочая нагрузка |
| QD32 | сравнение с паспортными/маркетинговыми пиками |

### 10.3. Рекомендация

Добавить отдельный advanced benchmark:

```text
Random 4K Read/Write:
- QD1 latency test
- QD4
- QD16
- QD32 peak test
```

Важно не смешивать эти результаты в одну оценку.

---

## 11. `os.urandom()` может стать bottleneck

### 11.1. Проблема

Свежий `os.urandom()` на каждую запись защищает от сжатия/дедупликации, но на быстрых NVMe может стать ограничителем.

Тогда программа будет измерять не диск, а скорость генерации случайных данных в Python.

### 11.2. Что добавить

В отчёт benchmark:

```text
Random data generation throughput: 8200 MB/s
Measured write throughput: 6900 MB/s
Generator bottleneck: no
```

Если генератор медленнее диска:

```text
Warning:
Random data generation may be limiting measured write speed.
```

### 11.3. Возможный подход

- заранее сгенерировать несколько incompressible buffers по 16–64 MiB;
- ротировать их;
- добавлять offset-dependent mutation;
- для verify хранить checksum/seed.

---

## 12. Verify: заменить MD5

MD5 для проверки целостности не является катастрофой, если задача не криптографическая. Но визуально MD5 выглядит устаревшим и может вызвать лишние вопросы.

Рекомендации:

- заменить на `BLAKE2b-256`;
- или использовать `SHA-256`;
- или дать два режима: `fast checksum` и `strong checksum`.

В отчёте:

```text
Verify hash: BLAKE2b-256
```

---

## 13. SLC Cache Test: улучшить cliff detection

### 13.1. Текущий подход

Падение среднего последних 3 точек ниже 60% от initial — рабочий старт.

### 13.2. Ограничения

Реальные SSD могут иметь:

- многоступенчатый SLC cache;
- thermal throttling;
- DRAMless/HMB особенности;
- каскадное падение скорости на QLC;
- кэш больше фиксированных 50/100 GB.

### 13.3. Что добавить

- детект нескольких cliff-событий;
- отметки температуры на графике;
- режим размера теста: fixed + percent of capacity + manual override;
- статус:

```text
Probable SLC cache: 86 GB
Post-cache speed: 740 MB/s
Thermal throttling suspected: yes/no
```

---

## 14. Enumeration: `PhysicalDrive0..31` может быть мало

Для обычного ПК 32 диска достаточно. Для серверов, стендов, HBA/RAID/USB-dock окружений — нет.

### Рекомендации

Минимально:

```python
MAX_PHYSICAL_DRIVES = 128
```

Лучше:

- сканировать до N consecutive missing после первого найденного диска;
- дать настройку в config;
- в будущем перейти на SetupAPI/Configuration Manager/Storage API enumeration.

---

## 15. Diagnostic Support Bundle

### 15.1. Зачем

С таким количеством fallback-цепочек пользователи будут присылать “у меня SMART не читается”. Скриншота будет мало.

### 15.2. Что экспортировать

```text
Help → Export Diagnostic Bundle
```

Содержимое:

- app version;
- Windows version/build;
- admin/elevated status;
- detected drives;
- model/interface/capacity;
- serial numbers with redact option;
- bus type;
- hypervisor/USB bridge guess;
- attempted SMART/NVMe methods;
- WinAPI error codes;
- SCSI status;
- sense data;
- WMI fallback result;
- chosen structure variant;
- optional raw 512-byte SMART/NVMe page.

### 15.3. Privacy option

```text
[x] Redact serial numbers
[ ] Include raw SMART/NVMe pages
```

---

## 16. Privacy: скрытие серийных номеров

JSON export, history, logs и screenshots могут содержать serial number.

Для корпоративного использования лучше сделать:

```text
Export options:
[x] Redact serial numbers
[ ] Include serial numbers
```

Для GitHub issue это должно быть значение по умолчанию.

---

## 17. Known Limitations

В README нужно добавить отдельный раздел.

Рекомендуемый текст:

```md
## Known Limitations

- RAID/HBA controllers may hide or virtualize SMART/NVMe health data.
- USB bridges vary widely; some do not expose SMART/NVMe passthrough.
- Surface Scan is HDD-centric. For SSDs it is only a coarse LBA readability check.
- Write benchmarks against PhysicalDrive are destructive and can overwrite user data.
- Health Score is heuristic and should not replace vendor diagnostics.
- TBW rating is estimated unless a vendor-specific endurance profile is available.
- WMI fallback provides partial NVMe data only.
- Virtual disks usually do not expose physical SMART data.
```

---

## 18. Документация: тон и точность

### 18.1. Что хорошо

Документация хорошо объясняет не только “что сделано”, но и “почему”. Это сильная сторона проекта.

### 18.2. Что поправить

Для публичной README стоит уменьшить героический тон и усилить юридически/технически осторожные формулировки.

Лучше:

```text
reduces cache distortion
```

чем:

```text
honest figures
```

Лучше:

```text
avoids the first 1 GiB
```

чем:

```text
disk stays bootable
```

Лучше:

```text
Health Score is heuristic
```

чем:

```text
health diagnosis
```

---

## 19. Suggested README patch

### Safety section

```md
### Safety

- **Read-only by default** — the default diagnostic path does not write to the disk.
- **Raw write operations are destructive** — any benchmark or scan mode that writes to `PhysicalDrive` can overwrite existing data.
- **MBR/GPT/EFI zone protection** — write tests avoid the first 1 GiB to reduce the risk of destroying partition and boot metadata. This does not protect user data beyond that area.
- **Fail-closed volume locking** — if any volume on the target disk cannot be locked and dismounted, the write operation is aborted.
- **System drive protection** — destructive operations on the system disk are refused by default.
- **Serial-number confirmation** — destructive CLI operations require typing the exact disk serial number.
- **TOCTOU guard** — the target disk is re-enumerated before writing to reduce the risk of USB disk number changes.
```

### Benchmark section

```md
### Benchmark

Read-only tests:
- Sequential Read
- Random 4K Read QD1
- Full Drive Read Sweep

Destructive write tests:
- Sequential Write
- Random 4K Write
- Mixed I/O 70/30
- Write-Read-Verify
- SLC Cache Test

Destructive write tests operate directly on `PhysicalDrive` and can overwrite existing data.
```

---

## 20. Suggested GUI wording

### Default safe mode

```text
Recommended: Safe Diagnostic
SMART/NVMe health, read-only benchmark and temperature monitoring.
No data will be written to the disk.
```

### Destructive mode

```text
Advanced: Destructive Raw Write Benchmark
Writes directly to the physical disk.
Use only on an empty/test disk or after a full backup.
```

### SSD Surface Scan warning

```text
SSD detected.
Read-only scan can check LBA readability, but write/refresh/erase modes are not recommended as SSD repair methods.
```

---

## 21. Acceptance criteria перед публичным релизом

Перед релизом я бы считала минимально необходимым:

- [ ] README явно говорит, что raw write tests destructive.
- [ ] Убрана/переписана фраза `disk stays bootable`.
- [ ] GUI destructive modes требуют typed confirmation.
- [ ] System disk destructive operations запрещены по умолчанию.
- [ ] `standard` profile не выглядит безопасным, если он пишет на диск.
- [ ] SSD healing modes скрыты или жёстко предупреждены.
- [ ] Исправлено `7 tests` vs `8 tests`.
- [ ] Добавлен Known Limitations.
- [ ] Export имеет redact serials.
- [ ] В отчётах есть “method/source of health data”.
- [ ] WMI fallback явно помечается как partial data.
- [ ] История хотя бы отображает предыдущие значения рядом с текущими.

---

## 22. Итоговая оценка

Проект выглядит технически сильным и перспективным. В нём уже есть признаки зрелого инженерного мышления:

- осторожность с WinAPI-структурами;
- fallback design;
- fail-closed logic;
- борьба с false-positive диагнозами;
- осознанное различение HDD и SSD;
- внимание к race conditions и TOCTOU.

Но перед тем как давать это внешним пользователям, нужно сделать интерфейс и документацию **параноидально честными** в отношении destructive операций.

Главный принцип релиза:

> Пользователь должен понимать, что raw write benchmark — это не “проверка диска”, а потенциальное уничтожение данных на выбранном физическом накопителе.

Когда этот риск будет закрыт, проект можно будет развивать в сторону self-tests, trend analysis, confidence scoring и расширенных benchmark-профилей.

---

## 23. Короткий список самых полезных следующих задач

1. Переписать Safety section в README.
2. Добавить GUI typed confirmation для write-операций.
3. Разделить Safe Diagnostic и Destructive Benchmark.
4. Добавить Known Limitations.
5. Добавить Confidence Score.
6. Добавить trend analysis по SQLite history.
7. Добавить SMART/NVMe self-test и error logs.
8. Добавить Diagnostic Support Bundle.
9. Сделать redact serials по умолчанию.
10. Расширить benchmark до advanced QD modes.

---

**Финальная мысль:**  
техническое ядро выглядит сильным; теперь нужно сделать так, чтобы пользовательский интерфейс был таким же дисциплинированным, как backend.  
Безопасность destructive операций должна быть не разделом документации, а частью поведения программы.
