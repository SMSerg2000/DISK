# DISK Diagnostic Tool v3.0.2

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
- **SMART trend** — per-attribute change since the last check (**Trend** column with ↑/↓), plus a degradation banner when defect counters grow (Reallocated/Pending/Uncorrectable/CRC; NVMe media-errors / spare drop). Snapshots in SQLite
- **Bilingual attribute names** — English and Russian
- **Export** — SMART (Ctrl+S), Benchmark (Ctrl+B), JSON (Ctrl+J)

### Benchmark (8 tests)
Read-only (safe, always run):
- **Sequential Read** — 1 MB blocks, throughput in MB/s
- **Random 4K Read** — IOPS, latency (avg, P95, P99, P99.9)
- **Full Drive Read Sweep** — 200-point speed vs position sampling

Destructive (write — opt-in via Standard/Full/Stress profile, **destroys data**):
- **Sequential Write** — 1 MB blocks, starts past the protected 1 GiB
- **Random 4K Write** — IOPS, latency
- **Mixed I/O 70/30** — realistic workload simulation
- **Write-Read-Verify** — 256 MB (1 GB in Stress) data integrity check (MD5)
- **SLC Cache Test** — continuous write up to 50/100 GB, cliff detection

- **Temperature monitoring** — recorded during all tests
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

### Self-test (SMART / NVMe)
- **Short / Extended** — commands the drive to run its built-in self-diagnostic (ATA SMART `EXECUTE OFFLINE` / NVMe Device Self-test)
- **Non-destructive** — the drive checks itself; user data is untouched
- **Live progress** — polled every few seconds, abortable anytime
- **History log** — past results read from the drive (test type, status, power-on hours, first error LBA)
- **Runs in firmware** — closing the app does not stop a running test (the drive keeps going)
- **ATA + NVMe + USB-SATA** — direct SATA/NVMe and USB-SATA bridges; USB-NVMe bridges report honestly when unsupported

### Error Log (SMART / NVMe)
- **Drive error log** — the disk's own record of recent command failures (ATA Summary SMART Error Log / NVMe Error Information Log)
- **Decoded** — error type (uncorrectable / aborted / interface-CRC / LBA-out-of-range…), failing LBA, power-on hours (ATA), namespace & status code (NVMe)
- **Read-only & safe** — a single short IOCTL, no writes
- **ATA + NVMe + USB-SATA** — USB-NVMe bridges report honestly when unsupported

### Safety
- **Read-only by default** — the default diagnostic path (SMART, read benchmarks, Ignore scan) never writes to the disk
- **Raw write operations are destructive** — any benchmark or surface mode that writes to `PhysicalDrive` **overwrites existing data and cannot be undone**
- **MBR/GPT/EFI zone protection** — write tests avoid the first 1 GiB to reduce the risk of destroying partition and boot metadata. This does **not** protect user data beyond that area
- **Typed confirmation (GUI)** — every write operation (benchmark write profiles, surface Erase/Refresh/Write) requires typing the exact disk serial number; the system disk requires typing `DESTROY PHYSICALDRIVE<N>`
- **Fail-closed volume locking** — if any volume on the target disk can't be locked and dismounted, the write is aborted
- **System drive protection** — destructive operations on the Windows disk demand the strongest confirmation by default
- **CLI: serial-number confirmation + TOCTOU guard** — re-verifies the disk by serial right before writing (in case of USB renumbering)
- **SSD-aware surface scan** — on SSD/NVMe, write-based "healing" modes warn that they don't repair flash and only consume endurance
- **Anti-compression** — incompressible random data on every write iteration (reduces cache/controller distortion on SandForce, etc.)

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
│   ├── surface_scan.py     # Surface scan (Ignore/Erase/Refresh/Write)
│   ├── self_test.py        # SMART/NVMe self-test (short/extended, progress, log)
│   └── error_log.py        # ATA/NVMe error log read + decode
├── data/
│   ├── smart_db.py         # 80+ SMART attributes (EN/RU)
│   ├── vendor_profiles.py  # Vendor decoder + name override
│   ├── baselines.py        # Performance baselines (QD1 vs QD32)
│   └── nvme_fields.py      # NVMe field descriptions (EN/RU)
├── gui/
│   ├── main_window.py      # Main window, menus, export
│   ├── benchmark_panel.py  # 7 cards + 4 chart tabs
│   ├── surface_panel.py    # Block map + stats + bad sector list
│   ├── selftest_panel.py   # Self-test launch + progress + history log
│   ├── errorlog_panel.py   # Drive error log table
│   └── ...                 # info_panel, smart_table, health_indicator, theme
├── i18n.py                 # Localization: tr("en", "ru")
└── resources/app.ico       # Application icon
```

---

## Known Limitations

- RAID/HBA controllers may hide or virtualize SMART/NVMe health data.
- USB bridges vary widely; some do not expose SMART/NVMe pass-through at all.
- Surface Scan is HDD-centric. For SSD/NVMe it is only a coarse LBA-readability check, **not** a NAND-cell test.
- Write benchmarks and surface write modes operate on raw `PhysicalDrive` and are **destructive** — they overwrite user data beyond the protected 1 GiB.
- Health Score is a heuristic and does **not** replace vendor diagnostics.
- TBW rating is **estimated** (≈600 TBW/TB heuristic) unless a vendor endurance profile is available — QLC/enterprise drives differ widely.
- WMI fallback (behind OEM Intel RST/VMD drivers) provides partial NVMe data only; health is reported as `UNKNOWN` rather than a false `GOOD`.
- Virtual disks expose no physical SMART data by design.
- Self-tests run via direct SATA/NVMe and USB-SATA bridges; USB-NVMe bridges generally cannot start one (reported honestly).
- Trend history compares against the previous snapshot of the **same** disk (by serial); the first reading has no baseline yet.
- Error log shows the drive's most recent entries (ATA Summary log: last 5; NVMe: last 32); USB-NVMe bridges generally cannot read it.

---

## Version History

| Version | Changes |
|---------|---------|
| **3.0.2** | Fix: ATA self-test/error-log on drivers that reject `SMART READ LOG` via the legacy SMART IOCTL (error 122 INSUFFICIENT_BUFFER) — log reads and EXECUTE OFFLINE now fall back to ATA Pass-Through / SCSI SAT |
| 3.0.1 | Fix: NVMe self-test/error-log reads now use `IOCTL_STORAGE_QUERY_PROPERTY` (like health), with `STORAGE_PROTOCOL_COMMAND` as fallback — drivers that reject the latter (StorNVMe/RAID/VMD, error 87) can now read the logs. NVMe self-test **start** still needs the protocol command and says so clearly |
| 3.0.0 | **Error log** (ATA Summary SMART Error Log / NVMe Error Information Log): decoded error type, failing LBA, power-on hours; read-only. **Completes the diagnostic-depth set** — SMART + trend + self-test + error log |
| 2.7.0 | SMART **trend history**: per-attribute Δ since last check (Trend column) + degradation alert when defect counters grow (Reallocated/Pending/CRC, NVMe media-errors / spare drop); SQLite snapshots, revived the previously write-only history |
| 2.6.0 | SMART/NVMe **self-tests**: Short/Extended, live progress, abort, history log (ATA + NVMe + USB-SATA); non-destructive, runs in drive firmware |
| 2.5.0 | "Honest & Safe": typed confirmation (type serial / `DESTROY PHYSICALDRIVE<N>`) for all GUI write ops, SSD-aware surface-healing warnings, honest safety wording, Known Limitations section, system-disk gate extended to Erase/Refresh, PhysicalDrive scan 32→64 |
| 2.4.x | Per-vendor attribute name override, SMART-table column-width fix (Stretch), maximized window, false "Wear 100%" guard (Kingston/SM2259) |
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
