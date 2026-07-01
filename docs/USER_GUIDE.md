# DISK Diagnostic Tool — User Guide

## 1. Introduction

### What is DISK Diagnostic Tool

**DISK Diagnostic Tool** is a Windows utility for diagnosing hard drives (HDD) and solid-state drives (SSD). Inspired by the legendary [Victoria HDD](https://hdd.by/victoria/), but written in Python with a modern graphical interface.

The program lets you:
- Read and analyze SMART data from disks
- Assess disk health (Health Score 0-100, with penalty breakdown)
- Forecast remaining SSD life (TBW estimate)
- Benchmark performance (read-only or destructive write profiles)
- Scan and repair the disk surface
- Export results to text or JSON files
- Track test history per disk (SQLite database)
- Run from the command line (CLI mode)

### Comparison with Victoria HDD

| Feature | Victoria HDD | DISK Diagnostic Tool |
|---------|-------------|---------------------|
| SMART reading | ATA/SATA | ATA/SATA + NVMe + USB-SATA + USB-NVMe |
| Health assessment | No | Health Score 0-100, TBW forecast |
| Surface scan | Yes | Yes (Ignore/Erase/Refresh/Write) |
| Sector drill-down | No | Yes (4096 bytes) |
| Benchmark | Basic | 7 tests + 4 charts, 4 safety profiles |
| SLC Cache test | No | Yes (cliff detection) |
| USB-NVMe bridges | No | JMicron/ASMedia/Realtek |
| Virtual disk detection | No | VirtIO/Hyper-V/VMware and more |
| CLI mode | No | Yes (--list, --smart, --benchmark, --history) |
| Bilingual interface | No | English + Russian |
| Dark theme | No | Catppuccin Mocha |

### System Requirements

- **OS:** Windows 10/11 or Windows Server 2019+ (64-bit)
- **Privileges:** Must run as **Administrator**
- **Dependencies:** None (everything is bundled in the exe)

### Running the Program

1. Download `DISK_Diagnostic.exe`
2. Right-click → **"Run as administrator"**
3. Confirm the UAC prompt

> ⚠️ Without administrator privileges, the program cannot read SMART data or access disks directly.

---

## 2. Interface Overview

### Main Window

The window consists of:

```
┌─────────────────────────────────────────────────────┐
│ File  🌐 Language  Help                              │  ← Menu bar
├─────────────────────────────────────────────────────┤
│ [Disk 0: Samsung SSD 990 PRO (1863 GB, NVMe) ▼] [Refresh] │  ← Drive selector
├──────────────────────────┬──────────────────────────┤
│ Drive Information        │     OK                   │
│ Model: Samsung 990 PRO   │   GOOD                   │  ← Health panel
│ Serial: ...              │ Score: 95/100            │
│ Capacity: 1863 GB       │ Power-On: 1y 4mo         │
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

- **File:**
  - Refresh (F5) — rescan drives
  - Export SMART... (Ctrl+S) — save SMART to a text file
  - Export Benchmark... (Ctrl+B) — save benchmark results
  - Export JSON... (Ctrl+J) — save the full session as machine-readable JSON
  - Exit (Alt+F4)

- **🌐 Language:**
  - English — switch to English
  - Русский — switch to Russian
  - The choice is stored in `lang.cfg` next to the exe; restart the program after switching

- **Help:**
  - About — version and authors

---

## 3. SMART Tab

### What is SMART

SMART (Self-Monitoring, Analysis and Reporting Technology) is the disk's built-in self-diagnostic system. The drive keeps counters of errors, temperature, operating hours and other parameters.

### ATA/SATA SMART (7-column table)

For SATA drives (HDD and SSD) a table is displayed:

| Column | Description |
|--------|-------------|
| **ID** | Attribute number (1-254) |
| **Attribute** | Name (in the current UI language) |
| **Current** | Current value (higher = better, usually 100 = normal) |
| **Worst** | Worst value ever recorded |
| **Threshold** | If Current drops below this, the drive is considered failing |
| **Raw Value** | Raw value (the actual counter) |
| **Status** | GOOD / WARN / CRIT |

### NVMe SMART (3-column table)

For NVMe drives:

| Column | Description |
|--------|-------------|
| **Parameter** | Field name (in the current UI language) |
| **Value** | Current value |
| **Status** | GOOD / WARN / CRIT |

### Color Coding

- **Blue text** — critical attribute (affects drive reliability)
- **Green status** — GOOD, everything is fine
- **Yellow status** — WARNING, needs attention
- **Red status** — CRITICAL, serious problem

### Tooltips

- **Click a row** — the attribute description appears below the table
- **Hover over Raw Value** — a tooltip shows the hex value and Low16/Low32 (useful for packed controllers like SandForce)

### USB Drive Support

The program reads SMART through USB:
- **USB-SATA** — via SCSI SAT pass-through (most USB-SATA bridges)
- **USB-NVMe** — via vendor-specific protocols: JMicron, ASMedia, Realtek

Fallback chain for USB drives: SAT → USB-NVMe bridges → standard NVMe IOCTL → WMI.

### Virtual Disks

Virtual disks (VirtIO, Hyper-V, VMware, VirtualBox, Xen/Citrix, Parallels, QEMU, Google Cloud, AWS EBS) are detected by model string and shown with interface type **Virtual**.

> SMART **does not exist** on virtual disks by design — they are an abstraction over the hypervisor's storage (LVM, NFS, Ceph, hardware RAID, etc.). The program shows an explanatory message instead of errors and hints where to check the real SMART (smartctl/storcli on the host).

---

## 4. Health Assessment

### Health Score (0-100)

The program computes a health score using a penalty formula. Every penalty is listed in the UI and in exports, so you can see exactly *why* points were deducted.

| Factor | Max penalty | Source |
|--------|------------|--------|
| Reallocated Sectors | -40 | ID 5 |
| Uncorrectable Errors | -40 | ID 187, 198 |
| Program Fail | -30 | ID 171 |
| Erase Fail | -30 | ID 172 |
| Pending Sectors | -20 | ID 197 |
| SSD Wear | -30 | ID 231, 177 |
| Temperature | -15 | ID 194/190 |
| CRC Errors | -10 | ID 199 |
| NVMe Critical Warning | -30 | NVMe |
| NVMe Media Errors | -40 | NVMe |
| NVMe Available Spare | -20 | NVMe |
| NVMe Unsafe Shutdowns | -10 | NVMe |
| NVMe Critical Temp Time | -10 | NVMe |

### Score Ranges

| Score | Color | Status | Recommendation |
|-------|-------|--------|----------------|
| 90-100 | Green | GOOD | All fine |
| 70-89 | Green | GOOD | Normal, keep monitoring |
| 50-69 | Yellow | WARNING | Plan a replacement |
| 30-49 | Red | CRITICAL | Replace soon |
| 0-29 | Red | CRITICAL | Replace immediately |

### TBW Calculator (SSD only)

For SSDs the program shows:
- **TBW used** — terabytes written to the disk so far
- **TBW estimate** — estimated endurance using a **heuristic of ~600 TBW per 1 TB of capacity** (typical for TLC consumer drives). This is an *estimate*, **not the vendor's official specification** — check your drive's datasheet for the rated TBW.
- **Write/day** — average daily write volume
- **Forecast** — years/months/days remaining at the current write rate

> If the forecast shows **"> 100 years"** — the disk sees very little write activity. Endurance will last a long time.

### Power-On Hours

Displayed as: **"1y 4mo 12d (12,308 hrs)"**

> For SandForce controllers (Kingston SKC300 and others) a special 20-bit mask is used to extract hours from the packed raw value correctly.

### Special Cases

- **Dead disk** (0 SMART attributes) → "UNKNOWN" instead of a false "GOOD"
- **HDD** — TBW is not shown (meaningless for HDD)
- **Score < 30** — the TBW forecast is hidden (the disk is dying)
- **WMI fallback with empty data** (some Intel RST/VMD setups) → "UNKNOWN" instead of a false 100/100

---

## 5. Benchmark Tab

### Test Profiles

Instead of a single checkbox, the benchmark offers four profiles. The warning dialog always shows the exact write plan for the selected profile.

| Profile | What runs | Approx. duration |
|---------|-----------|------------------|
| **Quick** | Read-only tests, safe | ~30 sec |
| **Standard** | + sequential/random/mixed/verify writes (no SLC) | ~2 min |
| **Full** | All tests + SLC Cache up to 50 GB | ~5 min |
| **Stress** | Verify 1 GB + SLC Cache up to 100 GB | ~15 min |

### Read-Only Tests (always run)

| Test | What it measures | Notes |
|------|------------------|-------|
| **Seq Read** | Sequential read speed (MB/s), 1 MB blocks | Warm-up run + 3 measured runs → median |
| **4K Read** | Random 4KB read — IOPS and latency (P95/P99/P99.9/P99.99) | 5000 reads; P99.9/P99.99 flagged as low-sample below 10,000 |
| **Drive Sweep** | Read speed at 200 points across the entire disk | 50 MB sample per point |

### Write Tests (destructive, Standard/Full/Stress profiles)

**ALL DATA ON THE DISK WILL BE DESTROYED!**

| Test | What it measures | Notes |
|------|------------------|-------|
| **Seq Write** | Write speed (512 MB of random data) | Starts at 1 GiB offset (MBR/GPT protected) |
| **4K Write** | Random 4KB write — IOPS | 1000 writes, never below the 1 GiB mark |
| **Mixed 70/30** | 70% read + 30% write, random 4KB | 30 seconds |
| **Verify** | Write → read back → MD5 compare | 256 MB (1 GB in Stress profile) |
| **SLC Cache** | Continuous write — cliff detection | Up to 50 GB (Full) / 100 GB (Stress) |

### 7 Result Cards

After the run, cards with big numbers show:
- Seq Read / Seq Write — MB/s
- 4K Read / 4K Write — IOPS
- Mixed 70/30 — IOPS (R + W separately)
- Verify — ✓ OK or ✗ FAIL
- SLC Cache — cache size in GB or "No cliff"

After the run, the status bar shows a comparison against built-in baselines (SATA HDD/SSD, NVMe Gen3/Gen4, USB) — pass / near baseline / below baseline.

### 4 Chart Tabs

- **Latency Scatter** — latency vs position on the disk
- **Latency Histogram** — distribution by ranges (0-50/50-100/100-200/200-500/500-1K/>1K μs)
- **Drive Sweep** — speed vs position (SSD = flat line, HDD = declining curve)
- **SLC Cache** — write speed vs volume written (cliff = the point where speed drops)

### Safety

- Warning dialog with the **disk model** and the exact write plan before any write test
- **System disk** — double confirmation
- **MBR/GPT protection** — every write phase skips the first **1 GiB** of the disk, so the partition table and EFI boot zone are never overwritten
- **Fail-closed volume locking** — before write tests, all volumes on the disk (including hidden EFI/Recovery) are locked and dismounted. If even one volume cannot be locked, the write tests are **aborted** — no writes happen over a live filesystem
- **Temperature monitoring** during all tests (every 5 seconds) — thermal throttling becomes visible, especially in the SLC test
- On an **I/O error** — the run continues, the affected card shows "✗ I/O Error"
- `FILE_FLAG_NO_BUFFERING` + `FILE_FLAG_WRITE_THROUGH` bypass the Windows cache and the drive's write-back cache for honest results
- **Fresh random data** (`os.urandom`) on every write iteration — controllers with compression/deduplication (SandForce) cannot inflate the numbers

### Export

**File → Export Benchmark (Ctrl+B)** — saves the results to a text file in the UI language, including temperature log and all metrics.

---

## 6. Surface Scan Tab

### Block Map

Victoria HDD-style visualization — a grid of colored rectangles:

| Color | Category | Meaning |
|-------|----------|---------|
| Gray | < 5 ms | Excellent |
| Green | < 20 ms | Good |
| Teal | < 50 ms | Acceptable |
| Yellow | < 150 ms | Slow |
| Orange | < 500 ms | Very slow |
| Red | ≥ 500 ms | Critically slow |
| X mark | Error | Sector unreadable |

### Scan Modes

#### Ignore (safe)
Read-only. Nothing is written to the disk. Shows the read speed map.

#### Erase
Writes zeros **only to unreadable sectors**. HDD firmware will remap them from the spare area. The data in those sectors is already lost anyway.

#### Erase +Slow
Like Erase, but additionally erases slow blocks (Very Slow ≥150 ms and Critical ≥500 ms). **Data in slow sectors will be destroyed!**

#### Refresh
Read → rewrite **the same data**. "Refreshes" the magnetic recording. The counter shows **"Refreshed"** (not "Repaired"). Data is preserved (barring a power failure during the write).

#### WRITE !!! (full erase)
Writes zeros to **EVERY** sector without reading. **ALL DATA IS DESTROYED!** All bad sectors will be remapped by the firmware. Use it for a full wipe before disposing of the disk.

### Drill-Down

On a block read error the program automatically:
1. Re-reads the block **sector by sector** (4096 bytes each)
2. Identifies the exact bad sectors
3. In Erase/Refresh modes — writes zeros **only to the bad sectors** (good data is untouched)
4. Displays bad-sector LBAs in real time in a scrollable list

### Settings

- **Block size:** 64 KB / 256 KB / 1 MB (default) / 4 MB
- **LBA from / to:** scan range in LBA sectors. Invalid ranges (start ≥ end, end beyond capacity, negative values) are rejected with an error dialog before the scan starts
- **+ Slow:** checkbox for the Erase +Slow mode (visible in Erase mode only)

### Statistics

The right panel shows:
- Counters per speed category
- Current speed (MB/s)
- Elapsed time and ETA
- Current position in LBA
- Refreshed/Repaired counters + write errors
- List of bad sectors (LBA)

### Safety

- Warning with the disk model before **WRITE !!!**
- **System disk** — double confirmation
- **Volume lock/dismount** — automatic for all write modes (including hidden EFI/Recovery partitions)
- 100 consecutive errors → emergency stop

---

## 7. Export

### Export SMART (Ctrl+S)

Saves to a text file:
- Drive information (model, serial number, firmware, capacity)
- Health assessment (Score, penalty breakdown, TBW, forecast, warnings)
- Full table of SMART attributes (ATA) or NVMe parameters

Filename: `SMART_Model_YYYYMMDD_HHMMSS.txt`

### Export Benchmark (Ctrl+B)

Saves to a text file:
- Drive information
- All test results (speed, IOPS, latency percentiles)
- Temperature (start → end, maximum)

Filename: `Benchmark_Model_YYYYMMDD_HHMMSS.txt`

### Export JSON (Ctrl+J)

Saves the **full session** in machine-readable form: drive info, health status with penalties, SMART data, benchmark results.

Filename: `DISK_Model_YYYYMMDD_HHMMSS.json`

Text exports use the current UI language (English or Russian).

---

## 8. Test History

Every time SMART is read, the result is automatically saved to a SQLite database — `disk_history.db`, located next to the exe.

Stored per test: timestamp, program version, Health Score, penalty list, TBW consumed, power-on hours, WAF. Disks are identified by **serial number**, so the history survives moving a drive between SATA and a USB enclosure.

View history from the command line:

```
DISK_Diagnostic.exe --history all          # all disks ever tested
DISK_Diagnostic.exe --history WXN1AB755VSJ # history of one disk by serial
```

---

## 9. CLI Mode

The program can run without the GUI:

| Flag | Description |
|------|-------------|
| `--list` | List all drives |
| `--smart N` | Read SMART from disk N (health score, penalties, full attribute table) |
| `--benchmark N` | Benchmark disk N |
| `--write` | Include destructive write tests (requires confirmation) |
| `--profile {quick,standard,full,stress}` | Benchmark profile (default: quick) |
| `--yes`, `-y` | Skip the interactive confirmation for `--write` (use with care!) |
| `--force-system-drive` | Allow destructive writes on the system disk (DANGEROUS!) |
| `--json` | Output in JSON format (with `--smart`) |
| `--history SERIAL` | Show test history for a serial number (or `all`) |

### Examples

```
DISK_Diagnostic.exe --list
DISK_Diagnostic.exe --smart 0
DISK_Diagnostic.exe --smart 0 --json
DISK_Diagnostic.exe --benchmark 1                          # read-only (quick)
DISK_Diagnostic.exe --benchmark 1 --write --profile full   # destructive
DISK_Diagnostic.exe --history all
```

### Destructive Write Confirmation

When `--write` is given, the CLI:
1. Prints the **exact write plan** (which tests, how many GB, the 1 GiB MBR/GPT-protected start offset)
2. **Refuses to run on the system disk** unless `--force-system-drive` is given
3. Asks you to **type the disk's serial number** to confirm:

```
⚠️  WRITE TESTS PLAN (DESTRUCTIVE):
   • Sequential Write:   512 MB starting at offset 1 GB (MBR/GPT protected)
   • Random 4K Write:    1000 ops at random LBAs
   • Mixed I/O 70/30:    30 sec random R+W
   • Write-Read-Verify:  256 MB
   All existing data on PhysicalDrive1 will be DESTROYED.

Type the disk serial 'WXN1AB755VSJ' to confirm destructive write tests:
```

If the serial does not match, the run is aborted. If the drive reports no serial, you must type `DESTROY` instead. `--yes` skips this prompt (for scripting — make sure you target the right disk!).

> Note: `--write` with the default `quick` profile is automatically bumped to `standard` (quick is read-only by definition).

---

## 10. Interpreting Results

### SMART: what to look at

#### Critical attributes (highlighted in blue)

| ID | Attribute | What raw > 0 means |
|----|-----------|--------------------|
| 5 | Reallocated Sectors | The drive found bad sectors and remapped them from the spare pool |
| 197 | Pending Sectors | Unstable sectors exist — they may turn bad |
| 198 | Offline Uncorrectable | Errors that could not be corrected |
| 171 | Program Fail | NAND write problems (SSD) |
| 172 | Erase Fail | NAND erase problems (SSD) |

#### Informational attributes

| ID | Attribute | Normal values |
|----|-----------|---------------|
| 9 | Power-On Hours | Any (just an hour counter) |
| 194 | Temperature | 25-45°C is normal, >60°C — overheating |
| 199 | CRC Errors | >0 = SATA cable problem, NOT the disk |
| 231 | SSD Life Left | 100 = new, <10 = time to replace |

### Benchmark Reference Values

| Test | SATA SSD | NVMe Gen3 | NVMe Gen4 |
|------|----------|-----------|-----------|
| Seq Read | 400-560 MB/s | 2000-3500 MB/s | 5000-7400 MB/s |
| Seq Write | 300-530 MB/s | 1500-3000 MB/s | 3000-7000 MB/s |
| 4K Read QD1 | 8-12K IOPS | 12-20K IOPS | 15-25K IOPS |

> The program measures at **QD1** (queue depth 1 — single-threaded latency). Vendor spec sheets usually quote QD32 peaks, which are many times higher. Don't compare them directly.

### SLC Cache: how to read the chart

- **Flat line** — the whole test ran at SLC-cache speed (cache larger than the test volume)
- **Drop (cliff)** — the cache is exhausted, writes go directly to NAND (slower)
- **Cache size** — the point where the speed drops (below 60% of the initial speed)

### Drive Sweep: how to read the chart

- **SSD: flat line** — normal, speed is uniform across the disk
- **HDD: declining curve** — normal, outer tracks are faster than inner ones
- **Dips on an SSD** — possible thermal throttling or controller issues

---

## 11. Troubleshooting

### "SMART not supported"

**Causes:**
- The USB bridge does not support pass-through commands
- The disk does not respond to SMART requests
- Insufficient privileges (run as Administrator)
- The disk is **virtual** — SMART does not exist there by design (see section 3)

### "Access Denied" (error 5)

- Run the program as **Administrator**
- For write operations: the program locks volumes automatically

### "I/O Device Error" (error 1117)

- The disk is **physically failing** or dropping off the bus
- Check the cables and connectors
- For USB — try a different port or cable

### Huge numbers in Raw Value

**SandForce** controllers (Kingston SKC300 and others) pack several counters into one 6-byte raw value. This is normal. Hover over the value — the tooltip shows Low16/Low32.

### The program won't start on Windows Server 2016

PySide6 6.5+ requires **Windows 10 version 1809** or newer. Windows Server 2016 is not supported.

### The icon doesn't update after copying the exe

Clear the Windows icon cache:
```
ie4uinit.exe -show
```

---

## 12. Supported Hardware

### Interfaces

| Interface | SMART | Benchmark | Surface Scan |
|-----------|-------|-----------|--------------|
| SATA (internal) | ✅ | ✅ | ✅ |
| NVMe (internal) | ✅ | ✅ | ✅ |
| USB-SATA | ✅ (via SAT) | ✅ | ✅ |
| USB-NVMe | ✅ (via bridge) | ✅ | ✅ |
| Virtual (VirtIO/Hyper-V/VMware/...) | — (by design) | ✅ | ✅ |

### USB-NVMe Bridges

| Chip | Example devices | Protocol |
|------|-----------------|----------|
| JMicron JMS583/581 | M.2 SSD enclosures | CDB 0xA1 (3-step) |
| ASMedia ASM2362/2364 | Samsung T7 | CDB 0xE6 (1-step) |
| Realtek RTL9210/9211/9220 | Sabrent enclosures | CDB 0xE4 (1-step) |

### Controllers with Quirks

| Controller | Quirk | How it's handled |
|------------|-------|------------------|
| SandForce SF-2281 | Packed raw (6 bytes) | POH = raw & 0xFFFFF (20-bit), critical counters = raw & 0xFFFFFFFF |
| Kingston (various) | Packed temperature | Low byte of raw = °C |
| OEM NVMe behind Intel RST/VMD | Non-standard bus type | Model-string heuristic + WMI fallback |

---

*DISK Diagnostic Tool v3.0.4 — User Guide*
*Developed by Serg and Claudine (Anthropic AI)*
