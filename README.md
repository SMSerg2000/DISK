# DISK Diagnostic Tool v2.4.5

<p align="center">
  <b>Windows SSD/HDD diagnostic utility inspired by <a href="https://hdd.by/victoria/">Victoria HDD</a></b><br>
  Built with Python 3.12+ + PySide6 | Raw Windows API (ctypes) | No external disk libraries
</p>

🇷🇺 [Документация на русском](README_RU.md)

📖 **[How It Works](docs/HOW_IT_WORKS.md)** — detailed breakdown of the algorithms and internals · [User Guide](docs/USER_GUIDE.md) · [Developer Guide](docs/DEVELOPER_GUIDE.md)

---

## Features

### SMART Monitoring
- **ATA/SATA SMART** — all attributes, thresholds, raw values, color-coded health
- **NVMe Health Info** — all 16 standard fields (temperature, spare, wear, media errors, etc.)
- **USB-SATA** — SMART via SCSI SAT pass-through (ATA PT / SCSI SAT CDB)
- **USB-NVMe** — vendor bridge pass-through: JMicron JMS583/581, ASMedia ASM2362/2364, Realtek RTL9210/9211/9220
- **Health Score (0-100)** — weighted formula with false-positive guards
- **TBW/WAF Calculator** — consumed/estimated TBW, daily write rate, remaining life forecast
- **80+ known ATA attributes** — including Kingston, Samsung, WD, Crucial/Micron, Transcend/Silicon Motion
- **Vendor profiles** — correct packed-raw decoding (SandForce, etc.) + per-vendor name override (e.g. ID 202 on Crucial = remaining life, not Address Mark Errors)
- **Virtual disk detection** — hypervisor recognition (VirtIO/Hyper-V/VMware/Xen/Parallels/GCE/AWS), honest "SMART not available"
- **OEM heuristics** — NVMe/SATA detection behind Intel RST/VMD drivers (bus_type=Unknown)
- **Real USB enclosure model** — via ATA IDENTIFY over SAT (instead of "Mass Storage Device")
- **Critical attributes** highlighted in blue
- **Bilingual attribute names** — English and Russian
- **Export** — SMART (Ctrl+S), Benchmark (Ctrl+B), JSON (Ctrl+J)

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
- **MBR/GPT/EFI zone protection** — all write tests start past the first 1 GiB (disk stays bootable)
- **Fail-closed volume locking** — if any volume can't be locked, writing aborts
- **System drive protection** — double confirmation with disk model
- **CLI: serial-number confirmation + TOCTOU guard** — re-verifies the disk before writing (in case of USB replug)
- **Anti-compression** — incompressible random data on every write iteration (honest figures on SandForce, etc.)

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
│   ├── drive_enumerator.py # Drive enumeration + interface heuristics
│   ├── health_assessor.py  # Health Score (0-100), TBW, WAF, false-positive guards
│   ├── benchmark.py        # 8 benchmark tests + temperature monitoring + MBR protection
│   └── surface_scan.py     # Surface scan (Ignore/Erase/Refresh/Write)
├── data/
│   ├── smart_db.py         # 80+ SMART attributes (EN/RU)
│   ├── vendor_profiles.py  # Vendor decoder + name override
│   ├── baselines.py        # Performance baselines (QD1 vs QD32)
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
| **2.4.x** | Per-vendor attribute name override, SMART-table column-width fix (Stretch), maximized window, false "Wear 100%" guard (Kingston/SM2259) |
| 2.3.x | Audit & safety batch: fail-closed volume lock, full MBR/GPT protection in all write phases, CLI confirmation + TOCTOU, ScsiStatus checks for USB bridges, profile separation, QD1 baselines, EN docs |
| 2.2.x | Virtual disks (hypervisor detection), real USB enclosure model via SAT IDENTIFY, OEM NVMe heuristic, new SMART attributes |
| 2.1.0 | Vendor-specific SMART decoder (8 profiles: SandForce, Kingston, Transcend, Intel, Samsung, SanDisk, Crucial/Micron) |
| 2.0.0 | CLI mode, baseline comparison, Stress profile (1GB verify, 100GB SLC), extended verify |
| 1.8.0 | WAF calculation, test history (SQLite), Health Score breakdown, test profiles, JSON export, warm-up+median, P99.9/P99.99, pre-check conditions |
| 1.7.0 | Health Score penalties, P99.9 latency, JSON export, documentation (4 guides) |
| 1.6.0 | Volume lock/dismount for ALL write modes, Refresh "Refreshed" label |
| 1.4.4 | SandForce packed raw fix (20-bit POH mask), false WARNING fix (current=100), new SMART attrs (13, 204, 230) |
| 1.3.0 | Graceful I/O error handling in benchmarks, dead disk detection (UNKNOWN instead of false GOOD) |
| 1.2.0 | NVMe SMART bilingual, critical attributes blue highlight |
| 1.1.0 | Hidden volume lock (EFI/Recovery) via FindFirstVolumeW, bilingual SMART export |
| 1.0.0 | Bilingual UI (RU/EN), SMART attribute translation, icon, theme fixes, benchmark export localization |
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

Developed by **Serg** (IT Director, [Delivery Auto](https://delivery-auto.com.ua)) with **Claudine** (Anthropic AI) 😊
