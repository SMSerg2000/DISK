# DISK Diagnostic Tool v0.9.0

Windows SSD/HDD diagnostic utility inspired by [Victoria HDD](https://hdd.by/victoria/).
Built with Python 3.14 + PySide6, using raw Windows API (ctypes) for direct disk access.
No external dependencies for disk I/O — only `CreateFile` + `DeviceIoControl`.

---

## Features

### SMART Monitoring
- **ATA/SATA SMART** — all attributes, thresholds, raw values, color-coded health
- **NVMe Health Info** — all 16 standard fields (temperature, spare, wear, media errors, etc.)
- **USB-SATA** — SMART via SCSI SAT pass-through (ATA PT / SCSI SAT CDB)
- **USB-NVMe** — vendor bridge pass-through for JMicron, ASMedia, Realtek chips
- **Health Score** — 0-100 score based on SSD Testing Spec formula
- **TBW Calculator** — consumed/rated TBW, daily write rate, remaining life forecast
- **Russian tooltips** — hover any NVMe parameter for detailed description
- **60+ known ATA attributes** — including Kingston, Samsung, WD vendor-specific
- **Export SMART** — File → Export SMART (Ctrl+S), text file with health status + full table

### Health Assessment
- **Health Score (0-100)** — weighted formula: reallocated sectors, media errors, wear, temperature, pending sectors, CRC errors, unsafe shutdowns, NVMe critical warning
- **TBW Forecast** — estimated remaining lifespan based on write history and capacity
- **Color badge** — GOOD (green) / WARNING (yellow) / CRITICAL (red)
- Works for both ATA/SATA and NVMe drives

### Surface Scan (Victoria HDD style)
- **Ignore** — read-only scan, measures latency per block
- **Erase** — write zeros to bad sectors (triggers HDD firmware remap)
- **Refresh** — read → rewrite same data (refreshes degrading sectors)
- **Write** — full surface erase (zeros to every block, all data destroyed)
- **Erase +Slow** — also erase slow sectors (≥150ms, ≥500ms)
- **Drill-down** — on block error, re-read sector-by-sector (4096B), write zeros only to actually bad sectors
- **Bad sector LBA** — real-time display in scrollable list as sectors are found
- **Scan range** — specify start/end LBA for targeted scanning
- **Block map** — real-time color grid (12x12px cells, 30fps), Victoria HDD style
- **Volume lock/dismount** — automatic before Write mode so Windows allows full access
- **Block sizes** — 64 KB, 256 KB, 1 MB (default), 4 MB

### Benchmark
- **Sequential Read** — 1 MB blocks, throughput in MB/s
- **Random 4K Read** — 1000 random reads, IOPS and latency
- **Latency scatter plot** — visual distribution across disk surface
- **Direct I/O** — `FILE_FLAG_NO_BUFFERING` + `VirtualAlloc` for honest results

### Drive Detection
- Scans PhysicalDrive0..31 via Windows API
- Model, serial number, firmware, capacity, interface type, temperature
- Auto-detects SSD vs HDD from SMART attributes
- Supports SATA, NVMe, USB-SATA, USB-NVMe interfaces

### USB Support
- **USB-SATA bridges** — SMART via ATA Pass-Through and SCSI SAT
- **USB-NVMe bridges** — vendor-specific SCSI pass-through:
  - JMicron JMS583/581 (CDB 0xA1, 3-step protocol)
  - ASMedia ASM2362/2364 (CDB 0xE6, 1-step)
  - Realtek RTL9210/9211/9220 (CDB 0xE4, 1-step)
- **Fallback chain**: SAT → USB-NVMe bridges → NVMe IOCTL → WMI

---

## Requirements

- Windows 10/11 or Windows Server 2019+ (64-bit)
- **Administrator privileges** required for disk access
- Python 3.12+ (for development)
- PySide6 >= 6.6.0 (only runtime dependency)

## Quick Start

```bash
# Install dependency
python -m pip install PySide6

# Run (as Administrator!)
python run.py
```

## Build Executable

```bash
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "DISK_Diagnostic" --clean run.py
# Output: dist/DISK_Diagnostic.exe
```

---

## Project Structure

```
disk_diag/
├── core/                   # Backend (no GUI dependencies)
│   ├── constants.py        # IOCTL codes, Windows API constants
│   ├── structures.py       # ctypes Structure definitions
│   ├── winapi.py           # CreateFile, DeviceIoControl, ReadFile, WriteFile,
│   │                       # AlignedBuffer, volume lock/dismount
│   ├── models.py           # DriveInfo, SmartAttribute, NvmeHealthInfo,
│   │                       # HealthStatus (with Score/TBW), ScanMode, etc.
│   ├── drive_enumerator.py # PhysicalDrive scanning (0..31)
│   ├── smart_ata.py        # ATA SMART: legacy IOCTL + ATA PT + SCSI SAT
│   ├── smart_nvme.py       # NVMe Health: QueryProperty (3 proto sizes) +
│   │                       # ProtocolCommand + SCSI_MINIPORT + WMI fallback
│   ├── smart_usb_nvme.py   # USB-NVMe: JMicron/ASMedia/Realtek vendor SCSI
│   ├── health_assessor.py  # Health Score (0-100), TBW calc, WAF
│   ├── benchmark.py        # Sequential + Random 4K read benchmark
│   └── surface_scan.py     # Surface scan engine (Ignore/Erase/Refresh/Write)
├── data/
│   ├── smart_db.py         # SMART attribute database (~60 entries)
│   └── nvme_fields.py      # NVMe health field descriptions (Russian)
├── gui/
│   ├── main_window.py      # Main window, SMART worker, export
│   ├── drive_selector.py   # Drive ComboBox
│   ├── info_panel.py       # Drive info display
│   ├── smart_table.py      # SMART table with color coding + tooltips
│   ├── health_indicator.py # Health badge with Score/TBW/forecast
│   ├── benchmark_panel.py  # Benchmark UI + scatter plot
│   ├── surface_panel.py    # Surface scan UI + block map + bad sector list
│   └── theme.py            # Catppuccin Mocha dark theme
├── utils/
│   ├── admin.py            # Admin privilege check + UAC elevation
│   └── formatting.py       # Capacity, hours, temperature formatting
└── __init__.py             # Version
run.py                      # Entry point with UAC elevation
```

---

## Technical Notes

### Windows API
- Pure `ctypes` + `kernel32.dll` — no external disk libraries
- Storage API structures: **native alignment** (no `_pack_`)
- ATA/SMART structures: `_pack_ = 1` (fixed binary format)
- `INVALID_HANDLE_VALUE`: `ctypes.c_void_p(-1).value` for 64-bit
- Disk capacity: 3 methods (GET_LENGTH_INFO → GEOMETRY_EX → STORAGE_READ_CAPACITY)

### NVMe SMART
- `IOCTL_STORAGE_QUERY_PROPERTY` with 3 `STORAGE_PROTOCOL_SPECIFIC_DATA` sizes (28/40/44 bytes)
- All offsets via `ctypes.sizeof()` — never hardcode Windows structure sizes
- Fallback: QueryProperty(disk) → QueryProperty(adapter) → ProtocolCommand → SCSI_MINIPORT → WMI

### USB-NVMe Bridges
- Windows USB driver doesn't forward NVMe IOCTLs — vendor SCSI pass-through required
- JMicron: 3-step (send NVM cmd → DMA-IN → completion), CDB opcode 0xA1
- ASMedia/Realtek: 1-step, CDB opcodes 0xE6/0xE4
- DATA_OUT response has no data (only SCSI header) — don't check response length

### Surface Scan
- Sequential read without seek (file pointer advances); seek only after errors
- Write mode: `FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME` before writing
- Drill-down: on block error, re-read sector-by-sector (4096B), zeros only to bad sectors
- `bDriveNumber` in SENDCMDINPARAMS: always 0 (device selected by handle)

### Health Score
- Formula based on SSD Testing Spec v2.0
- ATA: Reallocated(5), Uncorrectable(187/198), ProgramFail(171), EraseFail(172), Pending(197), SSDLife(231), WearLeveling(177), Temperature(194), CRC(199)
- NVMe: CriticalWarning, MediaErrors, PercentageUsed, AvailableSpare, Temperature, UnsafeShutdowns, CriticalTempTime
- TBW: Data Units Written (NVMe) or Host Writes attr 241/233 (ATA), ~600 TBW/TB heuristic

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.9.0 | 2026-03-22 | Health Score (0-100), TBW forecast, > 100yr cap, ATA + NVMe |
| 0.8.0 | 2026-03-22 | Surface Write mode (full erase), volume lock/dismount |
| 0.7.0 | 2026-03-22 | USB-NVMe bridge SMART (JMicron/ASMedia/Realtek), SMART export |
| 0.6.0 | 2026-03-19 | USB-NVMe fallback in SMART chain |
| 0.5.0 | 2026-03-14 | Surface Scan: Erase/Refresh healing, drill-down, LBA range |
| 0.4.0 | 2026-03-14 | NVMe SMART via IOCTL (ctypes.sizeof), Russian tooltips |
| 0.3.0 | 2026-03-12 | Surface Scan: block map, speed, ETA |
| 0.2.0 | 2026-03-10 | Benchmark: Sequential + Random 4K read |
| 0.1.0 | 2026-03-08 | Initial: drive detection, ATA SMART, dark theme |

## License

MIT

## Authors

Developed by **Serge** (IT Director, Delivery Auto) with **Claude** (Anthropic AI) 😊
