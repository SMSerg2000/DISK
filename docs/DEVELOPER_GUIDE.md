# DISK Diagnostic Tool — Developer Guide

## 1. Architecture Overview

### Package Structure

```
disk_diag/
├── __init__.py          # Version (__version__)
├── app.py               # QApplication, theme loading
├── i18n.py              # Localization: tr("en", "ru"), lang.cfg
├── core/                # Backend (no GUI dependencies)
│   ├── constants.py     # IOCTL codes, Windows API constants
│   ├── structures.py    # ctypes Structure (SMART, Storage API, SCSI)
│   ├── winapi.py        # CreateFile, DeviceIoControl, ReadFile, WriteFile,
│   │                    # AlignedBuffer, volume lock/dismount, is_system_drive
│   ├── models.py        # Dataclass: DriveInfo, SmartAttribute, NvmeHealthInfo,
│   │                    # HealthStatus, BenchmarkResult, SurfaceScanResult, ScanMode
│   ├── drive_enumerator.py  # PhysicalDrive0..31 scanning
│   ├── smart_ata.py     # ATA SMART: legacy IOCTL + ATA PT + SCSI SAT
│   ├── smart_nvme.py    # NVMe Health: QueryProperty (3 sizes) + ProtocolCommand
│   │                    # + SCSI_MINIPORT + WMI fallback
│   ├── smart_usb_nvme.py # USB-NVMe: JMicron/ASMedia/Realtek vendor SCSI
│   ├── health_assessor.py  # Health Score (0-100), TBW, WAF
│   ├── benchmark.py     # 7 benchmark tests + temperature monitoring
│   └── surface_scan.py  # Surface scan engine (Ignore/Erase/Refresh/Write)
├── data/
│   ├── smart_db.py      # 70+ SMART attributes (EN/RU), SmartAttributeInfo
│   └── nvme_fields.py   # NVMe fields (EN/RU), NvmeFieldInfo
├── gui/
│   ├── main_window.py   # Main window, menus, export, SMART worker
│   ├── drive_selector.py    # Drive ComboBox
│   ├── info_panel.py    # Drive info display
│   ├── smart_table.py   # QTableWidget for ATA/NVMe SMART
│   ├── health_indicator.py  # Health badge (GOOD/WARNING/CRITICAL)
│   ├── benchmark_panel.py   # 7 cards + 4 charts + worker
│   ├── surface_panel.py     # Block map + stats + worker
│   └── theme.py         # Catppuccin Mocha QSS
├── utils/
│   ├── admin.py         # Admin check + UAC elevation
│   └── formatting.py    # Capacity, hours, temperature formatting
└── resources/app.ico    # Application icon
run.py                   # Entry point with UAC elevation
```

### Design Principle: GUI → Core → Windows API

```
GUI (PySide6)
  │
  ├── QThread Workers (background tasks)
  │     │
  │     ▼
  Core (pure Python + ctypes)
  │     │
  │     ▼
  Windows API (kernel32.dll)
        │
        ▼
  PhysicalDriveN / \\.\X: / \\?\Volume{GUID}
```

Single external dependency: **PySide6**. Disk access: pure `ctypes` + `kernel32.dll`.

---

## 2. Windows API Layer (winapi.py)

### DeviceHandle

```python
class DeviceHandle:
    def __init__(self, drive_number=-1, read_only=False, flags=0, device_path=""):
        # Opens \\.\PhysicalDriveN or device_path
```

Context manager. Methods:
- `ioctl(code, in_struct, out_size)` — DeviceIoControl with ctypes Structure
- `ioctl_raw(code, in_bytes, out_size)` — DeviceIoControl with raw bytes
- `ioctl_inplace(code, buffer)` — single buffer for input/output (NVMe)
- `read(ptr, size)` / `write(ptr, size)` — ReadFile / WriteFile
- `read_at(offset, ptr, size)` / `write_at(offset, ptr, size)` — seek + read/write
- `seek(offset)` — SetFilePointerEx

### Critical Notes

- **INVALID_HANDLE_VALUE**: `ctypes.c_void_p(-1).value` (NOT just `-1`!)
- **SetLastError(0)** before checking after CreateFileW
- **AlignedBuffer**: `VirtualAlloc` for page-aligned buffers (FILE_FLAG_NO_BUFFERING requirement)

### Volume Lock/Dismount

```python
def lock_and_dismount_volumes(drive_number: int) -> list:
    # 1) Iterate drive letters A-Z
    # 2) FindFirstVolumeW/FindNextVolumeW for hidden volumes (EFI, Recovery)
    # 3) IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS → physical disk number
    # 4) FSCTL_LOCK_VOLUME + FSCTL_DISMOUNT_VOLUME
    # Returns list of handles (close after writing!)
```

**Critical:** lock required for ALL write modes (Erase, Refresh, Write). Without it, Windows blocks writes to mounted partition areas.

---

## 3. Drive Enumeration (drive_enumerator.py)

Scans PhysicalDrive0..31:
- `STORAGE_QUERY_PROPERTY` → model, serial, firmware, bus_type
- Bus type: SATA=0x0B, USB=0x07, NVMe=0x11
- `SMART_GET_VERSION` for ATA smart_supported check
- Capacity: 3 fallback methods (GET_LENGTH_INFO → GEOMETRY_EX → STORAGE_READ_CAPACITY)

**Storage API structures: NO `_pack_ = 1`** (native Windows alignment).
**ATA/SMART structures: WITH `_pack_ = 1`** (fixed binary format).

---

## 4. SMART Reading

### 4.1 ATA/SATA (smart_ata.py)

Two methods:
1. **Legacy IOCTL** (SMART_RCV_DRIVE_DATA) — internal SATA
2. **SCSI SAT** (IOCTL_SCSI_PASS_THROUGH, CDB 0x85) — USB-SATA bridges

Key: `bDriveNumber = 0` always (device selected by handle, not IDE number).

Health level per attribute:
```python
if current <= threshold:
    CRITICAL
elif current < 100 and current <= threshold + 10:
    WARNING  # Don't warn if current=100 (max/best value)!
elif is_critical and (raw & 0xFFFFFFFF) > 0 and id in (5, 196, 197, 198):
    WARNING  # low32 mask for SandForce packed data!
```

### 4.2 NVMe (smart_nvme.py)

18-combination fallback chain:
1. QueryProperty (disk, 3 proto sizes × 2 access modes)
2. QueryProperty (adapter \\.\ScsiN:)
3. ProtocolCommand
4. SCSI_MINIPORT
5. PowerShell WMI

**KEY LESSON:** All offsets via `ctypes.sizeof()` — **NEVER** hardcode Windows structure sizes!

### 4.3 USB-NVMe Bridges (smart_usb_nvme.py)

```python
_BRIDGE_METHODS = [
    ("JMicron", _jmicron_get_smart),   # CDB 0xA1, 3-step
    ("ASMedia", _asmedia_get_smart),   # CDB 0xE6, 1-step
    ("Realtek", _realtek_get_smart),   # CDB 0xE4, 1-step
]
```

**JMicron 3-step:** Send NVM cmd (DATA_OUT) → DMA-IN data → Completion
**DATA_OUT bug fix:** Response has no data (only SCSI header) — don't check response length!

---

## 5. Health Assessment (health_assessor.py)

### ATA Score Formula

```python
score = 100
score -= min(40, reallocated_low32 * 2)      # ID 5
score -= min(40, uncorrectable_low32 * 5)     # ID 187, 198
score -= min(30, program_fail * 3)             # ID 171
score -= min(30, erase_fail * 3)               # ID 172
score -= min(20, pending_low32 * 4)            # ID 197
# + SSD Life Left, Wear Leveling, Temperature, CRC Errors
```

### SandForce Packed Raw

- **Critical attrs (5, 196-198):** `raw & 0xFFFFFFFF` (low 32 bits)
- **Power-On Hours (ID 9):** `raw & 0xFFFFF` (low 20 bits)
- **Detection:** `raw > 1,000,000`

### TBW Calculation

```python
# ATA: ID 241 (Total Host Writes) in LBA sectors
consumed_tb = host_writes_lba * 512 / (1024**4)
# NVMe: Data Units Written × 512000 / (1024**4)
rated_tb = capacity_tb * 600  # TLC consumer heuristic
```

TBW not shown for HDD (`_is_ssd()` checks for SSD-specific attributes).

---

## 6. SMART Database (smart_db.py)

### Adding a New Attribute

```python
NEW_ID: _a(NEW_ID, "English Name", "Russian Name",
           "English description",
           "Russian description", is_critical=True/False, "unit"),
```

If SSD-specific → add to `SSD_INDICATOR_ATTRS` set.

### Bilingual Properties

```python
@property
def name(self) -> str:
    return tr(self.name_en, self.name_ru)  # Called each time!
```

---

## 7. Benchmark Engine (benchmark.py)

### Phase Order

```
Read-only: Sequential → Random 4K → Drive Sweep
── volume lock ──
Write: Seq Write → Random 4K Write → Mixed I/O → Verify → SLC Cache
── volume unlock ──
```

### Honest Results

- `FILE_FLAG_NO_BUFFERING` — bypass OS file cache
- `FILE_FLAG_WRITE_THROUGH` — bypass disk controller write-back cache
- `os.urandom()` — random data (controller can't compress zeros)

### I/O Error Handling

Each write phase in `try/except DiskAccessError`. On error: log, add to `result.io_errors`, continue next phase.

---

## 8. Surface Scan Engine (surface_scan.py)

### Main Loop

```python
if mode == WRITE:
    write(zeros)  # No read, just write
else:
    read(block)
    if ERROR: drill_down(sector_by_sector)
    elif should_write:
        REFRESH: write_at(same_data)
        ERASE: write_at(zeros)
```

### Drill-Down

On block error → read 4096-byte sectors → find exact bad LBAs → write zeros only to bad sectors → report via `bad_sector_callback`.

### Volume Lock

```python
if writing:  # ALL write modes: Erase, Refresh, Write
    volume_handles = lock_and_dismount_volumes(drive_number)
```

---

## 9. GUI Architecture

### QThread + Signal/Slot Pattern

All long operations in background threads:
```python
thread = QThread()
worker.moveToThread(thread)
thread.started.connect(worker.run)
worker.finished.connect(self._on_finished)
```

### Custom Widgets (QPainter)

- **BlockMapWidget** — 12×12px cell grid, QTimer 30fps, worst-aggregation
- **LineChartWidget** — generic line chart (Drive Sweep, SLC Cache)
- **LatencyHistogramWidget** — 6 bins with percentages
- **LatencyScatterWidget** — scatter (offset_gb, latency_us)

---

## 10. Localization (i18n.py)

```python
def tr(en: str, ru: str) -> str:
    return ru if _lang == "ru" else en
```

`lang.cfg` location: next to exe (`sys.executable` for PyInstaller).

To add new strings: just use `tr("English", "Russian")` anywhere. Both translations inline.

---

## 11. Build & Distribution

```bash
python -m PyInstaller --onefile --windowed \
    --name "DISK_Diagnostic" \
    --icon "disk_diag/resources/app.ico" \
    --clean run.py
```

Use `python -m pip`, not `pip` — they may point to different Python versions!

---

## 12. Known Issues & Solutions

| Issue | Cause | Fix |
|-------|-------|-----|
| SandForce packed raw | Controller packs data in 6 bytes | low32 for critical, low20 for POH |
| USB-NVMe DATA_OUT | SCSI response = header only | Don't check length for DATA_OUT |
| False write errors in Refresh | Volumes not locked | `lock_and_dismount_volumes` for all write modes |
| False WARNING at current=100 | threshold+10 == 100 | Check `current < 100` before warning |
| PySide6 on WinServer 2016 | Qt 6.5+ requires Win10 1809+ | No fix, Qt limitation |

---

## 13. How to Add...

### New SMART Attribute

Add to `disk_diag/data/smart_db.py`:
```python
NEW_ID: _a(NEW_ID, "English", "Russian", "Eng desc", "Ru desc", False),
```

### New USB-NVMe Bridge

Add to `disk_diag/core/smart_usb_nvme.py`:
```python
def _newbridge_get_smart(handle):
    cdb = bytearray(16)
    cdb[0] = 0xXX  # vendor opcode
    return _scsi_cmd(handle, cdb, _DATA_IN, _SMART_SIZE)
```
Add to `_BRIDGE_METHODS` list.

### New Benchmark Test

1. Add method `_run_new_test()` in `benchmark.py`
2. Add result fields in `BenchmarkResult` (models.py)
3. Add phase to `run()` method
4. Add GUI card and update `_on_progress` / `_on_finished`

### New Localization String

Just wrap in `tr("English", "Russian")`. Import: `from ..i18n import tr`

---

*DISK Diagnostic Tool v1.6.0 — Developer Guide*
*Developed by Serge and Claude (Anthropic AI)*
