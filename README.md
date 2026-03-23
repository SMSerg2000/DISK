# DISK Diagnostic Tool v1.0.0

<p align="center">
  <b>Windows SSD/HDD diagnostic utility inspired by <a href="https://hdd.by/victoria/">Victoria HDD</a></b><br>
  Built with Python 3.14 + PySide6 | Raw Windows API (ctypes) | No external disk libraries
</p>

🇷🇺 [Документация на русском](README_RU.md)

---

## Features

### SMART Monitoring
- **ATA/SATA SMART** — all attributes, thresholds, raw values, color-coded health
- **NVMe Health Info** — all 16 standard fields (temperature, spare, wear, media errors, etc.)
- **USB-SATA** — SMART via SCSI SAT pass-through (ATA PT / SCSI SAT CDB)
- **USB-NVMe** — vendor bridge pass-through: JMicron JMS583/581, ASMedia ASM2362/2364, Realtek RTL9210/9211/9220
- **Health Score (0-100)** — weighted formula based on SSD Testing Spec
- **TBW Calculator** — consumed/rated TBW, daily write rate, remaining life forecast
- **70+ known ATA attributes** — including Kingston, Samsung, WD, Transcend/Silicon Motion
- **Critical attributes** highlighted in blue
- **Bilingual attribute names** — English and Russian
- **Export SMART** — File → Export SMART (Ctrl+S)

### Benchmark (7 tests)
- **Sequential Read/Write** — 1 MB blocks, throughput in MB/s
- **Random 4K Read/Write** — IOPS, latency (avg, P95, P99)
- **Mixed I/O 70/30** — realistic workload simulation, 30 seconds
- **Write-Read-Verify** — 256 MB data integrity check (MD5)
- **SLC Cache Test** — continuous write up to 50 GB, cliff detection
- **Full Drive Read Sweep** — 200-point speed vs position sampling
- **Temperature monitoring** — during all tests
- **4 chart tabs** — Latency Scatter, Latency Histogram, Drive Sweep, SLC Cache
- **Direct I/O** — `FILE_FLAG_NO_BUFFERING` + `FILE_FLAG_WRITE_THROUGH`
- **Export Benchmark** — File → Export Benchmark (Ctrl+B)

### Surface Scan (Victoria HDD style)
- **Ignore** — read-only scan, latency per block
- **Erase** — write zeros to bad sectors (HDD firmware remap)
- **Refresh** — read → rewrite same data
- **WRITE !!!** — full surface erase (zeros to every block)
- **Erase +Slow** — also erase slow sectors (≥150ms)
- **Sector drill-down** — on error, re-read sector-by-sector (4096B)
- **Bad sector LBA** — real-time scrollable list
- **LBA range** — specify start/end for targeted scanning
- **Block map** — real-time color grid, 30fps
- **Volume lock/dismount** — automatic before write operations

### Safety
- **System drive protection** — double confirmation with disk model
- **Destructive warnings** — all write operations require confirmation
- **Disk model shown** in all warning dialogs

### Interface
- **Bilingual UI** — Russian / English (🌐 Language menu)
- **Dark theme** — Catppuccin Mocha
- **Custom icon** — Catppuccin-styled disk with health arc

---

## Requirements

- Windows 10/11 or Windows Server 2019+ (64-bit)
- **Administrator privileges** required
- Python 3.12+ (for development)
- PySide6 >= 6.6.0

## Quick Start

```bash
python -m pip install PySide6
python run.py  # Run as Administrator!
```

## Build Executable

```bash
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "DISK_Diagnostic" --icon "disk_diag/resources/app.ico" --clean run.py
```

---

## Project Structure

```
disk_diag/
├── core/                   # Backend
│   ├── winapi.py           # CreateFile, DeviceIoControl, ReadFile, WriteFile, volume lock
│   ├── smart_ata.py        # ATA SMART: legacy + ATA PT + SCSI SAT
│   ├── smart_nvme.py       # NVMe Health: 5 fallback methods + WMI
│   ├── smart_usb_nvme.py   # USB-NVMe: JMicron/ASMedia/Realtek vendor SCSI
│   ├── health_assessor.py  # Health Score (0-100), TBW, WAF
│   ├── benchmark.py        # 7 benchmark tests + temperature monitoring
│   └── surface_scan.py     # Surface scan (Ignore/Erase/Refresh/Write)
├── data/
│   ├── smart_db.py         # 70+ SMART attributes (EN/RU)
│   └── nvme_fields.py      # NVMe field descriptions (EN/RU)
├── gui/
│   ├── main_window.py      # Main window, menus, export
│   ├── benchmark_panel.py  # 7 cards + 4 chart tabs
│   ├── surface_panel.py    # Block map + stats + bad sector list
│   └── ...                 # info_panel, smart_table, health_indicator, theme
├── i18n.py                 # Localization: tr("en", "ru")
└── resources/app.ico       # Application icon
```

---

## Version History

| Version | Changes |
|---------|---------|
| **1.0.0** | Bilingual UI (RU/EN), SMART attribute translation, icon, theme fixes, benchmark export localization |
| 0.12.0 | Write-Read-Verify, Mixed I/O, latency histogram, line charts, app icon, benchmark export |
| 0.11.0 | Drive Sweep, Random 4K Write, temperature monitoring |
| 0.10.0 | Sequential Write + SLC Cache, system drive protection, Silicon Motion attributes |
| 0.9.0 | Health Score (0-100), TBW forecast, README documentation |
| 0.8.0 | Surface Write mode, volume lock/dismount |
| 0.7.0 | USB-NVMe bridge SMART, SMART export |
| 0.5.0 | Surface Scan healing (Erase/Refresh), drill-down, LBA range |
| 0.4.0 | NVMe SMART via IOCTL, Russian tooltips |

## License

MIT

## Authors

Developed by **Serge** (IT Director, [Delivery Auto](https://delivery-auto.com.ua)) with **Claude** (Anthropic AI) 😊
