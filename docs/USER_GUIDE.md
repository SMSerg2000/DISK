# DISK Diagnostic Tool — User Guide

## 1. Introduction

### What is DISK Diagnostic Tool

**DISK Diagnostic Tool** is a Windows utility for diagnosing hard drives (HDD) and solid-state drives (SSD). Inspired by [Victoria HDD](https://hdd.by/victoria/), built with Python and a modern GUI.

Features:
- Read and analyze SMART data from any disk
- Health Score assessment (0-100)
- SSD remaining life forecast (TBW)
- Performance benchmarking (7 tests)
- Surface scan and repair (Victoria HDD style)
- Export results to file
- Bilingual interface (English / Russian)

### System Requirements

- **OS:** Windows 10/11 or Windows Server 2019+ (64-bit)
- **Privileges:** Must run as **Administrator**
- **Dependencies:** None (standalone exe)

### Running the Program

1. Download `DISK_Diagnostic.exe`
2. Right-click → **"Run as administrator"**
3. Confirm UAC prompt

> Without admin privileges, the program cannot access SMART data or raw disk I/O.

---

## 2. Interface Overview

### Main Window Layout

```
┌─────────────────────────────────────────────────────┐
│ File  🌐 Language  Help                              │  ← Menu bar
├─────────────────────────────────────────────────────┤
│ [Disk 0: Samsung SSD 990 PRO (1863 GB, NVMe) ▼] [Refresh] │  ← Drive selector
├──────────────────────────┬──────────────────────────┤
│ Drive Information        │     OK                   │
│ Model: Samsung 990 PRO   │   GOOD                   │  ← Health badge
│ Serial: ...              │ Score: 95/100            │
│ Capacity: 1863 GB       │ Uptime: 1y 4mo           │
│ Interface: NVMe         │ TBW: 55/2103 TB          │
│ Temperature: 42°C       │ Forecast: ~42 years       │
├──────────────────────────┴──────────────────────────┤
│ [SMART] [Benchmark] [Surface Scan]                   │  ← Tabs
│                                                      │
│ (selected tab content)                               │
│                                                      │
└─────────────────────────────────────────────────────┘
│ Admin: Yes  Drives: 2                                │  ← Status bar
```

### Menus

- **File:** Refresh (F5), Export SMART (Ctrl+S), Export Benchmark (Ctrl+B), Exit
- **🌐 Language:** English / Русский (restart required after switch)
- **Help:** About

---

## 3. SMART Tab

### ATA/SATA SMART (7-column table)

| Column | Description |
|--------|------------|
| **ID** | Attribute number (1-254) |
| **Attribute** | Name (in current UI language) |
| **Current** | Current value (higher = better, 100 = normal) |
| **Worst** | Worst recorded value ever |
| **Threshold** | If Current drops below this, the drive is failing |
| **Raw Value** | Actual counter value |
| **Status** | GOOD / WARN / CRIT |

### NVMe SMART (3-column table)

| Column | Description |
|--------|------------|
| **Parameter** | Field name (in current UI language) |
| **Value** | Current value |
| **Status** | GOOD / WARN / CRIT |

### Color Coding

- **Blue text** — critical attribute (affects reliability)
- **Green status** — GOOD
- **Yellow status** — WARNING
- **Red status** — CRITICAL

### Tooltips

- **Click a row** — description appears below the table
- **Hover over Raw Value** — shows hex value + Low16/Low32 for packed controllers

### USB Drive Support

- **USB-SATA** — via SCSI SAT pass-through
- **USB-NVMe** — via vendor bridges: JMicron, ASMedia, Realtek

---

## 4. Health Assessment

### Health Score (0-100)

Calculated from multiple SMART attributes:

| Factor | Max penalty | Attributes |
|--------|-----------|------------|
| Reallocated Sectors | -40 | ID 5 |
| Uncorrectable Errors | -40 | ID 187, 198 |
| Program Fail | -30 | ID 171 |
| Erase Fail | -30 | ID 172 |
| Pending Sectors | -20 | ID 197 |
| SSD Wear | -30 | ID 231, 177 |
| Temperature | -15 | ID 194 |
| CRC Errors | -10 | ID 199 |

### Score Ranges

| Score | Color | Status | Action |
|-------|-------|--------|--------|
| 90-100 | Green | GOOD | All fine |
| 70-89 | Green | GOOD | Normal, monitor |
| 50-69 | Yellow | WARNING | Plan replacement |
| 30-49 | Red | CRITICAL | Replace soon |
| 0-29 | Red | CRITICAL | Replace immediately |

### TBW Calculator (SSD only)

- **TBW used** — terabytes written to disk
- **TBW rated** — estimated endurance (~600 TB per 1 TB capacity for TLC)
- **Write/day** — average daily write volume
- **Forecast** — remaining life at current write rate

> **"> 100 years"** means very low write usage — plenty of life remaining.

### Power-On Hours

Displayed as: **"1y 4mo 12d (12,308 hrs)"**

### Special Cases

- **Dead disk** (0 SMART attributes) → "UNKNOWN" instead of false "GOOD"
- **HDD** — TBW not shown (meaningless for HDD)
- **Score < 30** — TBW forecast hidden (disk is dying)

---

## 5. Benchmark Tab

### Read-Only Tests (safe, always run)

| Test | Measures | Duration |
|------|----------|----------|
| **Seq Read** | Sequential read speed (MB/s) | ~1 sec |
| **4K Read** | Random 4KB read — IOPS, latency (P95/P99) | ~1 sec |
| **Drive Sweep** | Speed at 200 points across disk | ~20-60 sec |

### Write Tests (destructive, "+ Write tests" checkbox)

**ALL DATA ON DISK WILL BE DESTROYED!**

| Test | Measures | Duration |
|------|----------|----------|
| **Seq Write** | Write speed (512 MB random data) | ~2 sec |
| **4K Write** | Random 4KB write — IOPS | ~1 sec |
| **Mixed 70/30** | 70% read + 30% write, random 4KB | 30 sec |
| **Verify** | Write 256MB → read → MD5 compare | ~5 sec |
| **SLC Cache** | Continuous write up to 50 GB, cliff detection | 1-10 min |

### 7 Result Cards

Large numbers showing: MB/s, IOPS, ✓ OK / ✗ FAIL, cache size in GB.

### 4 Chart Tabs

- **Latency Scatter** — latency vs disk position
- **Latency Histogram** — distribution (0-50/50-100/100-200/200-500/500-1K/>1K μs)
- **Drive Sweep** — speed vs position (SSD = flat, HDD = declining)
- **SLC Cache** — write speed vs volume (cliff = cache exhaustion)

### Safety

- Warning dialog with **disk model** before write tests
- **System disk** — double confirmation
- **I/O errors** — test continues, shows "✗ I/O Error" in card
- `FILE_FLAG_NO_BUFFERING` + `FILE_FLAG_WRITE_THROUGH` for honest results
- Random data (not zeros) to prevent controller compression

---

## 6. Surface Scan Tab

### Block Map

Victoria HDD-style grid of colored rectangles:

| Color | Category | Meaning |
|-------|----------|---------|
| Gray | < 5 ms | Excellent |
| Green | < 20 ms | Good |
| Teal | < 50 ms | Acceptable |
| Yellow | < 150 ms | Slow |
| Orange | < 500 ms | Very slow |
| Red | ≥ 500 ms | Critical |
| X mark | Error | Unreadable |

### Scan Modes

- **Ignore** — read-only, safe
- **Erase** — write zeros to bad sectors only (HDD firmware remap)
- **Erase +Slow** — also erase slow sectors (≥150ms)
- **Refresh** — read → rewrite same data (shows "Refreshed" not "Repaired")
- **WRITE !!!** — full surface erase, ALL DATA DESTROYED

### Drill-Down

On block read error:
1. Re-reads sector by sector (4096 bytes)
2. Identifies exact bad sector LBAs
3. In Erase/Refresh: writes zeros ONLY to bad sectors
4. Displays bad sector LBAs in real-time scrollable list

### Settings

- **Block size:** 64 KB / 256 KB / 1 MB (default) / 4 MB
- **LBA from / to:** Scan range in LBA sectors
- **+ Slow:** Checkbox for Erase +Slow mode

### Safety

- Warning with disk model before WRITE !!!
- System disk double confirmation
- Volume lock/dismount automatic for all write modes (including hidden EFI/Recovery)

---

## 7. Export

### Export SMART (Ctrl+S)

Saves to text file: drive info, health assessment, full SMART table.
Filename: `SMART_Model_YYYYMMDD_HHMMSS.txt`

### Export Benchmark (Ctrl+B)

Saves to text file: drive info, all test results, temperature log.
Filename: `Benchmark_Model_YYYYMMDD_HHMMSS.txt`

Both exports use current UI language.

---

## 8. Interpreting Results

### Critical SMART Attributes (blue)

| ID | Attribute | What raw > 0 means |
|----|-----------|-------------------|
| 5 | Reallocated Sectors | Drive found bad sectors and remapped them |
| 197 | Pending Sectors | Unstable sectors that may become bad |
| 198 | Offline Uncorrectable | Errors that couldn't be fixed |
| 171 | Program Fail | NAND write problems (SSD) |
| 172 | Erase Fail | NAND erase problems (SSD) |

### Benchmark Reference Values

| Test | SATA SSD | NVMe Gen3 | NVMe Gen4 |
|------|----------|-----------|-----------|
| Seq Read | 400-560 MB/s | 2000-3500 MB/s | 5000-7400 MB/s |
| Seq Write | 300-530 MB/s | 1500-3000 MB/s | 3000-7000 MB/s |
| 4K Read QD1 | 8-12K IOPS | 12-20K IOPS | 15-25K IOPS |

### SLC Cache Chart

- **Flat line** — cache > 50 GB or no cache
- **Drop (cliff)** — cache exhausted, direct NAND write speed after

### Drive Sweep Chart

- **SSD: flat line** — normal
- **HDD: declining curve** — normal (outer tracks faster)
- **Dips on SSD** — possible throttling or controller issues

---

## 9. Troubleshooting

| Problem | Solution |
|---------|----------|
| "SMART not supported" | Run as Admin; USB bridge may not support pass-through |
| "Access Denied" (error 5) | Run as Administrator |
| "I/O Device Error" (1117) | Disk is physically failing — backup data immediately |
| Huge raw values | SandForce packed data — normal. Hover for Low16/Low32 |
| Won't start on Server 2016 | PySide6 requires Windows 10 1809+ |
| Wrong icon after copy | Run `ie4uinit.exe -show` to clear icon cache |

---

## 10. Supported Hardware

### Interfaces

| Interface | SMART | Benchmark | Surface Scan |
|-----------|-------|-----------|-------------|
| SATA (internal) | ✅ | ✅ | ✅ |
| NVMe (internal) | ✅ | ✅ | ✅ |
| USB-SATA | ✅ (via SAT) | ✅ | ✅ |
| USB-NVMe | ✅ (via bridge) | ✅ | ✅ |

### USB-NVMe Bridges

| Chip | Protocol |
|------|----------|
| JMicron JMS583/581 | CDB 0xA1 (3-step) |
| ASMedia ASM2362/2364 | CDB 0xE6 (1-step) |
| Realtek RTL9210/9211/9220 | CDB 0xE4 (1-step) |

---

*DISK Diagnostic Tool v1.6.0 — User Guide*
*Developed by Serge and Claude (Anthropic AI)*
