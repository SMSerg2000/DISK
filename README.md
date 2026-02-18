# DISK Diagnostic Tool

Windows SSD/HDD diagnostic utility inspired by [Victoria HDD](https://hdd.by/victoria/).
Built with Python + PySide6, using raw Windows API (ctypes) for direct disk access.

## Features

### SMART Monitoring
- **ATA/SATA SMART** — reads all attributes, thresholds, raw values
- **NVMe Health Info** — temperature, spare, wear, media errors, power-on hours
- **Health assessment** — automatic GOOD / WARNING / CRITICAL evaluation
- **Color-coded table** — green/yellow/red rows by attribute health
- **Attribute descriptions** — click any attribute to see its explanation
- **60+ known attributes** — including Kingston, Samsung, WD vendor-specific

### Benchmark
- **Sequential Read** — 1 MB blocks, measures throughput (MB/s)
- **Random 4K Read** — 1000 random reads, measures IOPS and latency
- **Latency scatter plot** — visual distribution across disk surface
- **Direct I/O** — `FILE_FLAG_NO_BUFFERING` bypasses OS cache for honest results
- **Aligned buffers** — `VirtualAlloc` for sector-aligned memory

### Drive Detection
- Scans PhysicalDrive0..15 via Windows API
- Model, serial number, firmware, capacity, interface type
- Auto-detects SSD vs HDD from SMART attributes
- Supports SATA, NVMe, USB, ATA interfaces
- Graceful handling of I/O errors with helpful messages

## Screenshots

Dark theme (Catppuccin Mocha) with SMART table and Benchmark tabs.

## Requirements

- Windows 10/11 (64-bit)
- **Administrator privileges** required for disk access
- Python 3.12+ (for development)
- PySide6 >= 6.6.0

## Quick Start

```bash
# Install dependencies
pip install PySide6

# Run (as Administrator!)
python run.py
```

## Build Executable

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "DISK_Diagnostic" --clean run.py
# Output: dist/DISK_Diagnostic.exe
```

## Project Structure

```
disk_diag/
├── core/               # Backend (no GUI dependencies)
│   ├── constants.py    # IOCTL codes, Windows API constants
│   ├── structures.py   # ctypes Structure definitions
│   ├── winapi.py       # CreateFile, DeviceIoControl, ReadFile, AlignedBuffer
│   ├── models.py       # Dataclasses: DriveInfo, SmartAttribute, BenchmarkResult
│   ├── drive_enumerator.py  # PhysicalDrive scanning
│   ├── smart_ata.py    # ATA SMART attributes + thresholds
│   ├── smart_nvme.py   # NVMe Health Info log page
│   ├── health_assessor.py   # Health evaluation logic
│   └── benchmark.py    # Sequential + Random 4K read engine
├── data/
│   ├── smart_db.py     # SMART attribute database (~60 entries)
│   └── nvme_fields.py  # NVMe health field descriptions
├── gui/
│   ├── main_window.py  # Main window with tabs
│   ├── drive_selector.py    # Drive ComboBox
│   ├── info_panel.py   # Drive info display
│   ├── smart_table.py  # SMART table with color coding
│   ├── health_indicator.py  # Health badge (GOOD/WARNING/CRITICAL)
│   ├── benchmark_panel.py   # Benchmark UI + scatter plot
│   └── theme.py        # Catppuccin Mocha dark theme
└── utils/
    ├── admin.py        # Admin privilege check + UAC elevation
    └── formatting.py   # Capacity, hours, temperature formatting
```

## Technical Notes

- No external disk access libraries — pure `ctypes` + `kernel32.dll`
- Storage API structures use **native alignment** (no `_pack_`)
- ATA/SMART structures use `_pack_ = 1` (fixed binary format)
- `INVALID_HANDLE_VALUE` check: `ctypes.c_void_p(-1).value` for 64-bit compatibility
- Benchmark uses `FILE_FLAG_NO_BUFFERING` + `VirtualAlloc` to bypass OS cache
- SMART `bDriveNumber` always 0 — device selected by handle, not legacy IDE number
- Disk capacity: 3 fallback methods (GET_LENGTH_INFO → GEOMETRY_EX → STORAGE_READ_CAPACITY)

## License

MIT
