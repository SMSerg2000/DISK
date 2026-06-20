# DISK Diagnostic Tool — How It Works

> A detailed technical breakdown of the algorithms and internals (version 2.4.5).
> This document explains **how** the program works and **why** each engineering decision was made.
> For a feature overview see [README.md](../README.md); for usage see [USER_GUIDE.md](USER_GUIDE.md).

## Table of Contents

1. [Philosophy and overall architecture](#1-philosophy-and-overall-architecture)
2. [Low-level disk access layer](#2-low-level-disk-access-layer)
3. [Drive enumeration](#3-drive-enumeration)
4. [Reading SMART: four transports](#4-reading-smart-four-transports)
5. [Reading NVMe Health](#5-reading-nvme-health)
6. [Health analysis: Health Score, TBW, WAF](#6-health-analysis-health-score-tbw-waf)
7. [Vendor profiles: decoding and name override](#7-vendor-profiles-decoding-and-name-override)
8. [Benchmark engine](#8-benchmark-engine)
9. [Surface Scan](#9-surface-scan)
10. [GUI and multithreading](#10-gui-and-multithreading)
11. [CLI and the safety model](#11-cli-and-the-safety-model)
12. [Infrastructure: i18n, history, formatting, export](#12-infrastructure)
13. [Cross-cutting engineering principles](#13-cross-cutting-engineering-principles)

---

## 1. Philosophy and overall architecture

**The project's core principle is zero external dependencies for disk access.** The only third-party library is PySide6 (GUI). All storage work goes directly through `ctypes` + `kernel32.dll`: `CreateFileW` to open a device and `DeviceIoControl` to issue driver commands. No pywin32, no WMI wrappers, no external tools (PowerShell/WMI is used only as the very last NVMe fallback).

### Layers

```
┌─────────────────────────────────────────────────────────┐
│  GUI (PySide6)  — main_window, smart_table, panels       │
│      │  QThread workers (background tasks)               │
│      ▼                                                    │
│  CORE (pure Python + ctypes)                             │
│   ├─ winapi          — DeviceHandle, AlignedBuffer, lock │
│   ├─ drive_enumerator— enumeration, heuristics           │
│   ├─ smart_ata       — ATA legacy + SAT + IDENTIFY       │
│   ├─ smart_usb_nvme  — JMicron/ASMedia/Realtek bridges   │
│   ├─ smart_nvme      — NVMe fallback chain + WMI          │
│   ├─ health_assessor — Health Score, TBW, WAF            │
│   ├─ benchmark       — performance measurement           │
│   └─ surface_scan    — surface scanning                  │
│      │                                                    │
│      ▼                                                    │
│  WINDOWS API (kernel32.dll) — CreateFileW/DeviceIoControl│
│      │                                                    │
│      ▼                                                    │
│  \\.\PhysicalDriveN  /  \\.\C:  /  \\?\Volume{GUID}       │
└─────────────────────────────────────────────────────────┘
```

**The contract between layers** is `core/models.py`: only `dataclass`/`Enum` (`DriveInfo`, `SmartAttribute`, `NvmeHealthInfo`, `HealthStatus`, `BenchmarkResult`, `SurfaceScanResult`), **with zero ctypes dependency**. This lets the backend be tested and the GUI replaced independently.

**Data flow when reading SMART** (simplified):

```
Select drive in GUI → QThread worker → read SMART (ATA/USB/NVMe)
   → assess_*_health() → signal to main thread → render table + health badge
```

---

## 2. Low-level disk access layer

File: `core/winapi.py`. Home to two key wrappers — `DeviceHandle` and `AlignedBuffer` — plus volume locking.

### 2.1. `DeviceHandle` — context manager around a disk handle

```python
with DeviceHandle(drive_number, read_only=False, flags=0) as h:
    data = h.ioctl(IOCTL_CODE, in_struct, out_size)
```

**Opening** (`__enter__`):
- Path: `\\.\PhysicalDriveN` (or an arbitrary `device_path` such as `\\.\C:` or `\\?\Volume{GUID}`).
- **Access level** — the key rule: `read_only=True → GENERIC_READ` (enough for enumeration, descriptor, NVMe telemetry, read-only surface scan); `read_only=False → GENERIC_READ | GENERIC_WRITE` (needed for SMART ATA pass-through, SAT commands, writes). Write requires admin rights, so the minimum is taken by default.
- Share mode is always `FILE_SHARE_READ | FILE_SHARE_WRITE` — otherwise an open disk would block the OS itself.
- `OPEN_EXISTING` (the device already exists).

**The `INVALID_HANDLE_VALUE` subtlety.** In WinAPI it is `(HANDLE)-1`, which on a 64-bit system equals `0xFFFFFFFFFFFFFFFF`, not `-1`. Hence `_INVALID_HANDLE = ctypes.c_void_p(-1).value`, and the check is done defensively (against `None`, `0`, `-1` and the unsigned form).

**Typed exceptions** by `GetLastError()`:
- code 5 (Access Denied) → `AdminPrivilegeRequired`;
- code 2 (File Not Found) → `DriveNotFound` (a normal "end of scan" signal);
- otherwise → `DiskAccessError`.

This typing is critical for enumeration: different exceptions mean different reactions.

**Three IOCTL methods** (drivers expect data differently):
- `ioctl(code, in_struct, out_size)` — input is a ctypes structure (size via `ctypes.sizeof`, **never hardcoded**), separate output buffer. Returns exactly the bytes the driver wrote (`bytes_returned`).
- `ioctl_raw(code, in_bytes, out_size)` — input is raw bytes (for SAT/SCSI CDBs assembled with `struct.pack_into`).
- `ioctl_inplace(code, buffer)` — **one buffer for both input and output** (NVMe drivers require this).

**Raw I/O** for benchmark and surface scan: `read/write` (via `ReadFile`/`WriteFile` — they advance the OS file pointer themselves), `read_at/write_at` (= `seek` + `read/write`), `seek` (via `SetFilePointerEx`, 64-bit offset — works on drives > 2 TB).

### 2.2. `AlignedBuffer` — aligned buffer for direct I/O

**Problem:** `FILE_FLAG_NO_BUFFERING` bypasses the Windows cache (needed for honest benchmarking and surface scan — reading "clean"), but requires the buffer to be **sector-aligned** (512 or 4096 bytes). A plain `(c_ubyte * N)()` gives no such guarantee.

**Solution:** memory is taken via `VirtualAlloc` — always page-aligned (4096 bytes), more than enough. Freed via `VirtualFree(..., MEM_RELEASE)`. That's why `read/write` take a raw pointer `buf.ptr`, not Python bytes.

### 2.3. Volume locking — `lock_and_dismount_volumes` / `VolumeLockResult`

Before a **destructive** write (surface scan / benchmark), volumes on the disk are locked and dismounted. Otherwise Windows either denies writes to a mounted partition's area, or a write "around" the filesystem desyncs its cache and corrupts the FS.

The algorithm finds volumes **two ways**: iterating letters A–Z and via `FindFirstVolumeW`/`FindNextVolumeW` (catches hidden volumes without a letter — EFI, Recovery, MSR). For each volume, `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS` verifies it belongs to the **target** physical disk (disk number at offset 8), then `FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME`.

**Fail-closed principle** — a key safety rule. The function returns `VolumeLockResult{handles, failed_volumes}`. If `failed_volumes` is non-empty (any volume couldn't be reliably locked) — the **destructive operation must abort**. Better not to start than to corrupt a live FS. The function itself doesn't raise — the caller decides. The enumeration handle is closed in `finally` (`FindVolumeClose`) — no leak even on an exception inside the loop.

`is_system_drive(drive_number)` — a separate check ("is this the disk holding C:?"). Used for an extra warning before writing to the system disk.

---

## 3. Drive enumeration

File: `core/drive_enumerator.py`, function `enumerate_drives()`.

It iterates `PhysicalDrive0..31` (`MAX_PHYSICAL_DRIVES = 32`), **read-only**. A non-existent drive → `DriveNotFound` → `continue` (gaps in numbering are handled correctly). Each drive is isolated in `try/except`: one bad drive doesn't break the whole enumeration.

### 3.1. Descriptor

`IOCTL_STORAGE_QUERY_PROPERTY` → `STORAGE_DEVICE_DESCRIPTOR` buffer. The descriptor holds not the strings themselves but **offsets** to them within the buffer (VendorIdOffset, ProductIdOffset, SerialNumberOffset, ProductRevisionOffset) plus BusType. If the descriptor fails (a cheap USB bridge) — fallback values are used, the drive is **not lost**.

### 3.2. Capacity — three methods

By decreasing reliability, the first non-zero result wins:
1. `IOCTL_DISK_GET_LENGTH_INFO` — a clean int64, the most direct;
2. `IOCTL_DISK_GET_DRIVE_GEOMETRY_EX` — `DiskSize` field (offset 24);
3. `IOCTL_STORAGE_READ_CAPACITY` — goes through the storage stack, bypassing the disk class driver.

**Why the third method:** disk-level IOCTLs sometimes fail with **error 1117** (ERROR_IO_DEVICE) on some USB bridges and RAID controllers. The storage-level request bypasses the problematic layer. If all fail — capacity 0 (GUI shows "0.0 GB" instead of crashing).

### 3.3. Interface-detection heuristic chain (strictly ordered)

The heart of the module. Base `interface = _bus_type_to_interface(bus_type)`, then:

**Step 1. Hypervisor detection** — `_detect_hypervisor(model)`. Recognizes virtual disks by model string (specific to generic): VirtIO/Red Hat → "KVM/QEMU (VirtIO)", QEMU, Msft/Microsoft Virtual → Hyper-V, VMware, VBOX → VirtualBox, Xen/Citrix, Parallels, Google PersistentDisk, Amazon Elastic → AWS EBS. On a match → `InterfaceType.VIRTUAL`, hypervisor name in `DriveInfo.hypervisor`.
*Why:* virtual disks have no physical SMART — they're a hypervisor abstraction over LVM/NFS/Ceph/RAID. The tag lets the GUI show a meaningful message instead of "Unknown".

**Step 2. NVMe by model** (only if `interface == UNKNOWN`) — `_looks_nvme_model(model)`. OEM Intel RST/VMD drivers report a non-standard bus_type, so the interface must be guessed. Logic:
- **Exclusion first:** model starting with `MZ7` or `MZN` → NOT NVMe (Samsung **SATA**: MZ7* = 2.5" 850/860/870 EVO OEM, MZN* = mSATA/M.2 SATA). The prefixes dangerously overlap with Samsung's NVMe line.
- `NVME` substring → NVMe;
- prefixes `HFM` (SK hynix), `KBG` (KIOXIA), `PM9` (Samsung OEM), `MZV`/`MZ1`/`MZQ` (Samsung M.2/U.2 NVMe) → NVMe.

**Step 3. Generic USB → SAT IDENTIFY** (if `interface == USB` and the name is generic: "Mass Storage Device", "USB Device", etc.). Cheap bridges report the bridge name, not the disk's. A **second handle with write access** is opened (SCSI/ATA pass-through needs write), and the real model/serial/firmware is pulled via `identify_device_via_sat` (see §4.4).

**Step 4. SMART check → interface refinement.** For SATA/ATA/USB, `SMART_GET_VERSION` is sent. For UNKNOWN: **if SMART responds → `interface = SATA`**. *Why:* Intel RST/VMD report bus_type=Unknown for ordinary SATA SSDs; the NVMe heuristic already ran at step 2, so if SMART responds it's SATA.

---

## 4. Reading SMART: four transports

Files: `core/smart_ata.py`, `core/smart_usb_nvme.py`. Disk health is read via four transports depending on the connection type.

### 4.1. ATA/SATA legacy IOCTL — `read_smart_attributes`

For internal SATA/ATA disks visible through the native Windows driver.

**The command** is built in a `SENDCMDINPARAMS` structure (`_pack_ = 1` — a strict binary format): an ATA task file with `Features = SMART sub-command`, the SMART signature `LBA Mid=0x4F / LBA High=0xC2` (without it the disk won't recognize a SMART command), `Command = 0xB0`. **`bDriveNumber = 0` always** — on SATA the device is selected by handle (`\\.\PhysicalDriveN`); the field is legacy IDE master/slave.

Sequence: `SMART_ENABLE_OPERATIONS` (best-effort, error swallowed) → `SMART_READ_ATTRIBUTES` (0xD0) → `SMART_READ_THRESHOLDS` (0xD1, optional).

**Parsing 512 bytes:** data starts at offset 2 (2 version bytes), then up to 30 records of 12 bytes:

```
byte [0]      Attribute ID  (0 = empty slot, skip)
byte [1-2]    Flags         (uint16 LE)
byte [3]      Current value (normalized, 1..253)
byte [4]      Worst value
byte [5-10]   Raw value     (6 bytes LE → 48-bit number)
```

### 4.2. Per-attribute HealthLevel

```python
if threshold > 0 and current <= threshold:
    health = CRITICAL                       # factory failure threshold reached
elif threshold > 0 and current < 100 and current <= threshold + 10:
    health = WARNING                        # within 10 points of threshold
elif is_critical and (raw & 0xFFFFFFFF) > 0 and attr_id in (5, 196, 197, 198):
    health = WARNING                        # "event" attributes with non-zero raw
else:
    health = GOOD
```

- **CRITICAL:** normalized `current` dropped to/below the factory `threshold` — the disk officially considers the attribute failed.
- **WARNING (near threshold):** within 10 points of the threshold and `current < 100` (100 = normal, don't alarm).
- **WARNING (low32 mask):** for sector attributes **5/196/197/198** the mere non-zero count matters. Only the **low 32 bits** of raw are taken — SandForce controllers put service junk in the high bytes, and without the mask a healthy disk would get a false WARNING.

### 4.3. USB-SATA via SAT — `read_smart_via_sat`

A USB enclosure doesn't answer legacy SMART IOCTLs — the bridge only speaks SCSI/SAT. ATA commands are "wrapped" two ways, with automatic selection:
1. `IOCTL_ATA_PASS_THROUGH` (`ATA_PASS_THROUGH_EX`, offsets via `.offset` — `DataBufferOffset` is a `ULONG_PTR`, 8/4 bytes);
2. `IOCTL_SCSI_PASS_THROUGH` with **CDB ATA PASS-THROUGH (16), opcode 0x85**, PIO Data-In — more universal, works even on bridges without ATA PT support.

**Probe:** first try `SMART_ENABLE`; if it fails, try reading attributes directly (some bridges block ENABLE but read fine). Parsing and health assessment are the same as legacy.

**`check_scsi_status` — the "lie detector".** `DeviceIoControl` may return success (TRUE) while the SCSI command itself failed (CHECK CONDITION) — the status sits in the `ScsiStatus` field (**offset 2** of the `SCSI_PASS_THROUGH` header), not in the IOCTL return. Without this check, a junk buffer is treated as valid SMART. The function checks ScsiStatus after **every** SCSI command (including DATA_OUT), decodes SAM-5 names, and appends sense data in hex.

### 4.4. Real USB enclosure name — `identify_device_via_sat`

A three-method chain to obtain ATA IDENTIFY DEVICE:
1. **SCSI INQUIRY VPD page 0x89** (ATA Information) — per the SAT standard the bridge must return a copy of IDENTIFY at offset 60 of the page. The most reliable for USB.
2. `IOCTL_ATA_PASS_THROUGH` with command 0xEC (note: LBA Mid/High = 0, **not** the SMART signature!).
3. SCSI SAT CDB 0x85 with command 0xEC.

String decoding (`_ata_string`): ATA IDENTIFY stores ASCII as "big-endian words" — bytes are swapped within each 16-bit word (`'WD'` = bytes `'D','W'`), so a pair swap + strip of spaces AND NULs is needed. Offsets: serial 20-39, firmware 46-53, model 54-93.

### 4.5. USB-NVMe bridges — `read_usb_nvme_smart`

A USB-NVMe bridge doesn't support standard NVMe IOCTLs. SMART is obtained by tunneling the NVMe Admin Get Log Page command through a **vendor-specific SCSI CDB**, different per bridge:
- **JMicron** (JMS583/581) — CDB `0xA1`, **3-step**: send NVMe command (DATA_OUT, 512 bytes with "NVME" signature) → DMA-IN receive (DATA_IN, 512 bytes) → completion;
- **ASMedia** (ASM2362/2364) — CDB `0xE6`, 1-step;
- **Realtek** (RTL9210/9211/9220) — CDB `0xE4`, 1-step.

The chain tries JMicron → ASMedia → Realtek; the first valid response wins. The response is validated by `_looks_valid_health_page`: plausibility of temperature (200-400 K) or spare/threshold (≤100%) — a bridge may return 512 bytes of junk with non-zero bytes, which a naive `any()` check misses. `check_scsi_status` is called for DATA_OUT too — otherwise JMicron step 1 would "silently succeed" and step 2 return junk.

---

## 5. Reading NVMe Health

File: `core/smart_nvme.py`, function `read_nvme_health_auto`. A dispatcher cycling through **5 methods** and up to **~22 combinations**.

### 5.1. Why so many methods

NVMe access under Windows is fragmented: it depends on the Windows/Storage driver version, controller type (native StorNVMe vs OEM Intel RST/VMD), whether the disk hangs directly or behind a RAID/SCSI adapter, access rights (RO/RW), and even the SDK version (hence different structure sizes). The code iterates the Cartesian product of all axes, starting from the most likely on Win11.

```
Method 1: IOCTL_STORAGE_QUERY_PROPERTY on \\.\PhysicalDriveN
          2 access modes (RW,RO) × 2 PropertyId (Dev,Adapter) × 3 sizes = 12 combos
Method 2: same via the SCSI adapter \\.\ScsiN:             = 6 combos
Method 3: IOCTL_STORAGE_PROTOCOL_COMMAND (direct NVMe Admin)  = 2 combos
Method 4: IOCTL_SCSI_MINIPORT + NvmeMini signature         = 1
Method 5: PowerShell/WMI fallback                          = 1
                                                  Up to 22 attempts
```

On a healthy NVMe under Win11 the first or second Method 1 combo usually succeeds. All errors accumulate, and if no method passes — a `DiskAccessError` is raised with the full list of attempts (for diagnostics).

### 5.2. Key principle: offsets via `ctypes.sizeof()`, not hardcoding

Historical problem (the 44/52/564 bugs): `STORAGE_PROTOCOL_SPECIFIC_DATA` has a different field count across SDK versions, hence a different `sizeof`. Offsets used to be hardcoded, and on a machine with a different structure version the data shifted out of place.

The solution — three structure variants (`_ProtoData7`=28 bytes, `_ProtoData10`=40, `_ProtoData11`=44) plus iteration. The buffer `[query header 8][proto struct][512 bytes for health]` is built via `from_buffer` (overlaying the structure onto the buffer without copying), and `ProtocolDataOffset = ctypes.sizeof(proto)` — again from sizeof. The data position in the response is also computed from what the **driver reported** (`ProtocolDataOffset`/`Length` from the descriptor), not from a constant.

**Response validation:** data length not 0 and not > 576; start+length within the buffer; first 32 bytes not all zero. Only then parse.

### 5.3. Parsing the 512-byte Health Log — `_parse_raw_health`

Layout strictly per the NVMe spec (offsets set once by the order of incremental reads):

| Field | Offset | Type | Handling |
|---|---|---|---|
| critical_warning | 0 | 1 byte | bitmask (as-is) |
| temperature | 1 | uint16 LE, Kelvin | sanity 200-400 K → K−273, else 0 |
| available_spare / threshold / percentage_used | 3/4/5 | 1 byte, % | as-is |
| data_units_read/written, host commands, controller_busy, power_cycles, power_on_hours, unsafe_shutdowns | 32..159 | 128-bit LE | each a 16-byte slice |
| media_errors / error_log_entries | 160/176 | 128-bit LE | **& 0xFFFFFFFFFFFFFFFF** |
| warning/critical_temp_time | 192/196 | uint32 LE, minutes | as-is |
| temperature_sensors[0..7] | 200 | 8×uint16, Kelvin | range-check 200-400 K |

**Guards:**
- **Kelvin→Celsius with sanity:** a plain −273 is dangerous (0 K would give −273 °C). Only `200 ≤ K ≤ 400` (−73..+127 °C) → `K−273`; else 0 ("unknown", ignored by the GUI).
- **Mask `& 0xFFFFFFFFFFFFFFFF`** on error counters: **SK hynix BC711** firmware writes junk into the high bytes of the 128-bit fields. The error count can't exceed 2⁶⁴, so it's truncated to the low 64 bits — otherwise "trillions of errors" would appear where there are none.
- **8 sensors with range-check:** unpopulated (0 K) and junk (0xFFFF) sensors are **excluded** from the list (otherwise the table would show −273 °C for every missing one).

### 5.4. WMI fallback

When all IOCTLs fail (typically a USB-NVMe bridge without passthrough), `_read_nvme_health_wmi` runs PowerShell `Get-StorageReliabilityCounter`. `drive_number` is forced to `int` (it's interpolated into the script text). The result is checked with `isinstance(dict)` (ConvertTo-Json may return an array/scalar), each field goes through `_wmi_int` (number/string `"50.5"`/null/junk → int or 0, never crashes). It yields only a subset (temperature, wear, hours, cycles, read errors) and sets the **`wmi_fallback=True`** flag — so health analysis knows the data is partial and won't draw conclusions from zeros.

---

## 6. Health analysis: Health Score, TBW, WAF

File: `core/health_assessor.py`.

### 6.1. Two independent results

The assessment combines **two metrics**:
1. **Health Score (0–100)** — starts at 100, subtracts penalties for problems.
2. **Level (`HealthLevel`)** — GOOD/WARNING/CRITICAL/UNKNOWN — computed **separately**, from the `warnings`/`critical_issues` lists.

The level is **not derived** from the score: the score may be 95, yet one `critical_issue` yields CRITICAL. The score reflects "overall wear"; the level reflects "alarm now?". Convention: **`-1` in any numeric field means "unknown"** (not "zero") — the GUI distinguishes "0 TB written" from "couldn't read".

### 6.2. Health Score formula (ATA)

| Attribute | ID | Penalty formula | Cap |
|---|---|---|---|
| Reallocated Sectors | 5 | `min(40, realloc·2)` | 40 |
| Uncorrectable Errors | 187,198 | `min(40, max(187,198)·5)` | 40 |
| Program Fail | 171 | `min(30, ·3)` | 30 |
| Erase Fail | 172 | `min(30, ·3)` | 30 |
| Pending Sectors | 197 | `min(20, ·4)` | 20 |
| SSD Life Left | 231 | tiered (see below) | 30 |
| Wear Leveling | 177 | tiered (if no 231) | 25 |
| Temperature | 194/190 | tiered (low8) | 15 |
| CRC Errors | 199 | `min(10, crc)` | 10 |

Sector attributes (5/187/196/197/198) go through `decoded()` — vendor profile or low32 fallback (SandForce packs counters into the high 32 bits; without the mask one sector would become an astronomical number). Uncorrectable takes the **max** of 187 and 198 (two representations of one problem; summing would double-penalize). The score never goes negative (`max(0, score)`).

### 6.3. False-positive guards — the conceptual core

The program must not lie. Key guards:

| Guard | Problem it solves |
|---|---|
| Empty attributes → UNKNOWN, score −1 | don't show a fake 100/100 when SMART wasn't read |
| **SSD Life Left (231): penalty only if `life_left>0` OR `raw>0`** | Kingston/Silicon Motion (KC600, SM2259) report 231=all-zeros (placeholder) → caused a false "SSD Wear: 100%" −25 on a new drive |
| Wear Leveling (177) only if ID 231 absent | don't penalize wear twice |
| Temperature `& 0xFF` (low8) | min/max in raw high bytes → false hundreds of °C |
| low32 for sector attrs without profile | SandForce packs counters into the high 32 bits |
| Near-threshold WARNING only if `current<100` | 100 = normal, don't alarm |
| **NVMe OEM driver → UNKNOWN** | Intel RST/VMD: WMI returns only temperature (POH=0, DUW=0, cycles=0) → otherwise a false GOOD 100/100 |
| Available Spare only if `!wmi_fallback` | WMI doesn't return a correct spare/threshold |
| NVMe WAF = −1 | the spec gives no NAND-writes, nothing to compute honestly |
| remaining_days cap = 40000 (~110 years) | near-zero writes → millions of junk days |
| ID 202 override (Crucial) → critical=False | "Address Mark Errors" (HDD) vs "remaining life" (SSD) |

### 6.4. TBW and WAF

**Consumed TBW:** ID 241 (Total LBAs Written × 512) or fallback ID 233 (32 MB units). **Rated TBW:** the heuristic `capacity_tb × 600` (600 TBW/TB for consumer TLC) — **not** a vendor spec; the field is tagged `tbw_estimation_method="heuristic_600_per_tb"` (QLC ~150, enterprise ~3000+).

**Forecast:** `daily_write = consumed / (POH/24)`, `remaining_days = min(remaining_tb / daily_write, 40000)`. Built only with > 24 h power-on.

**WAF (Write Amplification Factor)** = NAND writes / Host writes (ideal ~1.0; GC/SLC cache raise it): ID 249 (NAND GiB) vs 241, or fallback ID 243 vs 241. For NVMe WAF is always −1 (the spec gives no NAND-writes). HDD: TBW isn't computed (early return).

### 6.5. NVMe Health Score

Penalties: Critical Warning (30, fixed), Media Errors (`min(40, ·5)`), Percentage Used (tiered up to 30), Available Spare (20/10, non-WMI only), Temperature (up to 15, thresholds higher than ATA — NVMe runs warmer normally), Unsafe Shutdowns (10/5), Critical Temp Time (10). Critical Warning is decoded bitwise into human-readable causes (spare below threshold, overheat, reliability degraded, read-only, backup failure).

---

## 7. Vendor profiles: decoding and name override

Files: `data/vendor_profiles.py`, `data/smart_db.py`.

### 7.1. Decoding packed raw

Some controllers **pack** several values into one 64-bit raw field. A profile describes how to extract the useful part:

| Method | Mask | Use |
|---|---|---|
| `raw` | as-is | standard controller |
| `low8` | `& 0xFF` | temperature (min/max in high bytes) |
| `low16` | `& 0xFFFF` | low 2 bytes |
| `low20` | `& 0xFFFFF` | SandForce Power-On Hours |
| `low32` | `& 0xFFFFFFFF` | SandForce critical counters |

`match_profile(model, firmware)` finds a profile by model/firmware substring (first match; order matters). `decode_raw` applies the profile rule, or `_DEFAULT_DECODE` (**temperature 190/194 is always low8, even without a profile** — it's packed by almost everyone), or returns raw as-is.

8 profiles: SandForce SF-2281 (the "heaviest" — nearly all attributes packed), Kingston A400/NV2, Transcend, Intel, Samsung, SanDisk, Crucial/Micron MX.

### 7.2. Per-vendor name override — `get_attribute_override`

One SMART ID means different things across vendors. The canonical example — **ID 202**:
- standard = "Data Address Mark Errors" (an HDD error, critical attribute);
- on **Crucial/Micron** = "Percentage Lifetime Remaining" (SSD remaining life: 100=new, 0=exhausted — **not a failure**).

Without the override the tool would panic on a healthy Crucial. The profile carries `"names": {202: {name_en, name_ru, desc_en, desc_ru, critical: False}}`, and `get_attribute_name/description/is_critical(attr_id, override)` give it priority over the base. The `smart_db.py` base **does not depend** on vendor_profiles — it takes a ready dict (loose coupling).

### 7.3. Attribute database `smart_db.py`

`SmartAttributeInfo` (frozen dataclass): `name_en/ru`, `desc_en/ru`, `is_critical`, `unit`. Properties `.name`/`.description` pick the language via `tr()`. ~80 attributes in the `SMART_ATTRIBUTES` dict. The `SSD_INDICATOR_ATTRS` set (23 IDs) marks SSDs.

---

## 8. Benchmark engine

Files: `core/benchmark.py`, `data/baselines.py`. Performance measurement via **raw I/O bypassing the filesystem** (`\\.\PhysicalDriveN` + `FILE_FLAG_NO_BUFFERING`).

### 8.1. Profiles

| Profile | Write | SLC | Write phases |
|---|---|---|---|
| **quick** | no | no | read only |
| **standard** | yes | no | seq/random/mixed/verify (no SLC) |
| **full** | yes | 50 GB | all 5 |
| **stress** | yes | 100 GB | all 5, verify 1 GiB |

The `include_slc` flag is split from `include_write` so Standard can write but skip the long SLC test.

### 8.2. Phases

**Read (non-destructive):**
- **Sequential Read** — 1 warm-up run (discarded) + 3 measured → **median** (more robust than the mean against OS spikes).
- **Random 4K Read (QD1)** — 5000 random reads, per-op latency in µs, percentiles P95/P99/P99.9/P99.99. The `random_low_sample = n<10000` flag signals that tail percentiles are statistically weak (at 5000, P99.99 rests on 1 sample).
- **Full Drive Read Sweep** — 200 points across the disk, a "speed vs position" graph (on HDDs the outer-to-inner falloff is visible).

**Write (destructive):** Sequential Write, Random 4K Write, Mixed I/O 70/30 (30 s), Write-Read-Verify (write→read→MD5 compare), SLC Cache Test (cache "cliff" detection).

### 8.3. QD1 methodology — why it's a latency test

All random tests run strictly synchronously: one op in flight, the loop waits for completion. This measures **response time** (latency), and IOPS = `1/average_latency`. **You can't compare it with vendor QD32 specs:** marketing IOPS are quoted at queue depth 32+ (dozens of ops in parallel, the controller hiding latency across many NAND channels). A synchronous QD1 loop physically can't reach those numbers. So the baseline stores QD1 ranges, and marketing QD32 peaks separately (`qd32_peak_*`), for reference only. QD1 latency is what the user actually feels (system responsiveness).

### 8.4. Safety — three lines of defense

**1. MBR/GPT/EFI zone protection (1 GiB).** `MBR_PROTECT_BYTES = 1 GiB`. **All** write phases start no earlier than this offset: sequential ones (seq_write, verify, slc) `seek(1 GiB)`; random ones (rnd_write, mixed) `randrange(1 GiB, max)`. The subtlest case is **verify** — it opens the disk twice (write and read = different handles), so `seek(1 GiB)` is needed in **both** phases, otherwise the read would start at LBA 0 and the compare would give a 100% mismatch. On small disks (< ~1.5 GB) the phases are **skipped** with a warning instead of writing into the protected zone.

**2. Volume lock fail-closed.** Before writing, `lock_and_dismount_volumes`; if `failed_volumes` is non-empty → **abort** (no writing at all). Unlock is guaranteed in `finally`.

**3. Direct I/O without caches.** `FILE_FLAG_NO_BUFFERING` (bypass Windows cache) + `FILE_FLAG_WRITE_THROUGH` (bypass controller write-back cache) + `AlignedBuffer`. **Anti-compression:** a fresh `os.urandom()` on every write iteration — otherwise controllers with compression/dedup (SandForce) recognize a repeating pattern and report inflated speeds that don't reflect real NAND load.

**Temperature polling** in all phases (at most every 5 s) — lets one distinguish thermal throttling from a real SLC-cache cliff.

### 8.5. SLC Cache Test

Pours continuous writes in 100 MiB chunks, measuring each chunk's speed. The **cliff** is detected in real time: when the average of the last 3 points drops below `initial × SLC_CLIFF_RATIO (0.6)` (60% of initial) — the cache is exhausted; it writes 3 more GB to measure the steady direct-NAND speed and stops. `slc_cache_size_gb` = volume up to the cliff.

### 8.6. Baseline comparison

`detect_class` determines the class by interface + seq_read (250 MB/s separates SATA SSD from HDD, 4000 separates Gen4 from Gen3). `compare_to_baseline` yields pass/warn/fail (`pass` if ≥ lower bound, `warn` if ≥ 60% of it). IOPS are compared **only** with QD1 ranges plus a note "QD1 latency test — vendor specs typically quote QD32 peaks" (a guard against false panic).

---

## 9. Surface Scan

File: `core/surface_scan.py`. A Victoria HDD analog: sequential read of the whole surface, measuring each block's latency and classifying by a "traffic light".

### 9.1. Modes and categories

**Modes (`ScanMode`):**
- **Ignore** — read only, classify; on error, drill-down (identify bad LBAs) but **write nothing**. The safe default.
- **Erase** — zeros **only into bad sectors** (drill-down); optionally (`+slow`) also zeroes slow blocks. Good data preserved.
- **Refresh** — read → rewrite the **same** data (refreshes degrading HDD sectors without data loss).
- **Write** — zeros across the whole surface without reading (full erase).

**Latency categories:** EXCELLENT <5ms, GOOD <20, ACCEPTABLE <50, SLOW <150, VERY_SLOW <500, CRITICAL ≥500, ERROR (exception). A Victoria-style scale: rising latency on an HDD = head recalibration and re-reads of a weak sector.

### 9.2. Range validation (fail-fast)

In the constructor, **before opening the disk**: `ValueError` on `start<0`, `end<0`, `capacity≤0`, `block_size≤0`, `start≥end`, `end>capacity`. The offset is aligned to `block_size` (a `NO_BUFFERING` requirement); `end_offset=0` means "to the end of the disk".

### 9.3. Main loop

**Volume lock fail-closed** for writing modes (abort on failed_volumes), **try/finally guarantees unlock** (the main loop is extracted into `_do_scan` to keep the lock logic compact).

**Key technique — sequential read WITHOUT seek.** `ReadFile` advances the OS file pointer itself, so no seek is needed between blocks. `seek` is called **only** after an error or a write (when the pointer drifted), gated by the `need_seek` flag. *Why:* an extra `SetFilePointerEx` before each block breaks the controller's hardware read-ahead and lowers sequential speed.

**Emergency stop — 100 consecutive errors.** `consecutive_errors` resets on any successful block, so a disk with scattered single bad blocks scans to the end; the stop fires only on a solid run of 100 errors (total failure; each error costs seconds of controller time-out).

### 9.4. Drill-down — per-sector analysis

When a block (1 MB) fails to read, it's unknown which sector is bad. Drill-down re-reads the block in 4096-byte units (safe for 4Kn disks), finds the specific unreadable sectors (LBA in traditional 512-byte units), and in Erase/Refresh/Write modes writes zeros **only into bad sectors** — user data is preserved. `bad_sector_callback` highlights the bad LBA in the GUI immediately. *Why writing "heals":* on an HDD, writing to a bad sector forces the controller to re-magnetize it "clean" or to **remap** it to a spare sector.

### 9.5. Why it's HDD-centric for SSDs

On SSDs, surface-scan conclusions need careful interpretation:
- **LBA ≠ physical cell** — the controller's FTL sits between them; a "bad LBA" doesn't point to a specific failed cell.
- **latency doesn't reflect wear** — it's driven by controller queues, background GC, and the SLC cache, not cell health.
- **writing "to heal" is harmful** — it consumes P/E cycles, while SSD bad-block management is handled transparently by the controller.

For SSDs the methodologically correct tool is **SMART/NVMe health** (Percentage Used, Media Errors), not surface scan. The Ignore mode is useful as a coarse readability check of all LBAs.

---

## 10. GUI and multithreading

Files: `disk_diag/app.py`, `gui/main_window.py`, `gui/smart_table.py`.

### 10.1. Startup

`run.py` checks admin rights **before importing PySide6** (lazy import): `is_admin()` → if not, a `QMessageBox` asks and the app restarts via `ShellExecuteW` with the `runas` verb (UAC trigger), passing the original `sys.argv` (CLI flags preserved). `app.py`: `QApplication`, the Fusion style (identical on all Windows, fully customizable via QSS), the Catppuccin Mocha dark theme, and **`showMaximized()`** (see §10.4).

### 10.2. Qt threading and race protection

SMART reading is slow (especially USB) — it runs in the background via the "worker-object + QThread" pattern (`_SmartWorker(QObject)` + `moveToThread`). Before a new run, the old thread: `quit()` + `wait()` + **`deleteLater()`** (without explicit disposal, Qt objects accumulate until the window closes).

**Generation counter — guard against a "silent false diagnosis".** The user switches drives quickly; the `finished` signal from the **old** worker may arrive after a new drive is selected, and drive A's SMART would render under drive B's name. Solution: `_smart_gen` is incremented on each run, the generation is captured in the lambda (`g=gen` as a default argument), and the handler discards the result if `gen != self._smart_gen`.

**closeEvent** with an active benchmark/surface-scan shows a confirmation "Interrupt the test and exit?" — silently killing a thread mid raw-write is unacceptable (data corruption). `stop()` sends `cancel()` and waits up to 3+15 seconds.

### 10.3. SMART-reading logic in the worker

Branching by interface: **NVMe** → `read_nvme_health_auto`; **USB** → a three-step chain (USB-SATA SAT → USB-NVMe bridges → standard NVMe IOCTL → none); **ATA** → `read_smart_attributes`. **Virtual disks** are intercepted in the main thread (the worker isn't started) — a hint is shown to use hypervisor tools (smartctl/storcli/zpool/mdadm).

### 10.4. Column width — three layers against Stretch "sticking"

The "Attribute" (ATA) / "Parameter" (NVMe) column is **Stretch** (stretches the table to the full window width). But Stretch computes width from the table's current width at fill time. With **asynchronous** filling from a worker thread, the width is still "transitional", and Stretch sticks at a narrow value (table on the left, black gap on the right, names truncated). The fix — three independent application layers:
1. **synchronously** right after filling;
2. **deferred** via `QTimer.singleShot(0)` — runs after the final window geometry is applied;
3. **`resizeEvent`** — reapplies on any window resize.

Plus an early `showMaximized()` — the window is wide from the start, so the table is laid out at full width on the first frame.

Attribute descriptions are stored in the cell's `UserRole` (not by row index) — they "move" with the attribute when the table is sorted. Critical ones are highlighted in blue `#89B4FA`.

---

## 11. CLI and the safety model

File: `cli.py`. Commands: `--list`, `--smart N` (+`--json`), `--benchmark N`, `--history SERIAL`. The check `args.smart is not None` (drive 0 is valid; `if args.smart:` would drop zero).

**Multi-layer protection for `--benchmark --write`:**
1. **Auto-upgrade** quick→standard (quick is read-only by intent).
2. **Print the write plan** with an explicit "All existing data on PhysicalDrive{N} will be DESTROYED".
3. **System-disk protection:** `is_system_drive` → refuse (exit 2) unless `--force-system-drive`.
4. **Serial-number confirmation:** the user types the exact disk serial (or `DESTROY` if there's no serial). A mismatch → exit 3.
5. **TOCTOU guard (re-enumerate):** between confirmation and start the user could replug USB — disk numbers shift, and the write would go to a DIFFERENT disk. The list is re-enumerated and the serial under the same number is re-checked; if the disk vanished/changed → exit 4.

Exit codes: 0 success, 1 disk not found / no SMART, 2 system disk without the flag, 3 confirmation mismatch, 4 TOCTOU, 130 Ctrl+C.

---

## 12. Infrastructure

### 12.1. Localization (`i18n.py`)

`tr(en, ru)` — both strings inline in code, returning the right one by `_lang` (no external .po/.json — minimalism). `lang.cfg` sits next to the exe (via `sys.executable` under PyInstaller) or in the project root during development (`getattr(sys, 'frozen', False)`). Switching language requires a restart (`tr()` is evaluated when widgets are built).

### 12.2. Test history (`history.py`)

SQLite `disk_history.db` (next to the exe / in the project root). All operations use `contextlib.closing` — guaranteeing `db.close()` even on a SQL error (otherwise connections would accumulate in a long-lived GUI). `save_test` stores penalties as JSON (`ensure_ascii=False` — Cyrillic stays readable). History writes are in `try/except` — a failure doesn't break diagnostics.

### 12.3. Formatting (`formatting.py`)

`format_capacity` (binary units 1024, adaptive precision), `format_hours` (hours + "Ny Md"), `format_smart_raw` (type-dependent: temperature 190/194 with min/max unpacking, hours 9/240, LBAs 241/242 → capacity, NAND 249 in GiB).

### 12.4. Export

Three formats via `QFileDialog`: SMART txt (Ctrl+S), Benchmark txt (Ctrl+B, with the temperature log), JSON (Ctrl+J, the full session, `ensure_ascii=False`). All in `try/except OSError`.

---

## 13. Cross-cutting engineering principles

These principles recur across all subsystems and define the program's "character":

1. **Zero external dependencies for disks** — only ctypes + kernel32. WMI/PowerShell as a last resort only.
2. **`_pack_ = 1` only for ATA/SMART structures** (a fixed "wire" binary format); Storage API structures use native Windows alignment. Mixing them = broken offsets.
3. **The `sizeof` discipline** — never hardcode Windows structure sizes/offsets (they float across SDK versions; the historical 44/52/564 bugs).
4. **Fail-safe everywhere** — `is_admin`, history, export, localization in `try/except`; an infrastructure error doesn't break diagnostics. One bad drive doesn't break enumeration.
5. **Fail-closed for destructive ops** — volume lock: if any volume isn't locked, the write aborts. The MBR/GPT zone (1 GiB) is protected in all write phases.
6. **Resource-closing discipline** — `contextlib.closing` (SQLite), `deleteLater` (Qt), `DeviceHandle`/`AlignedBuffer` as context managers. Don't accumulate open handles.
7. **False-positive guards** — many checks against a false GOOD (dead drive, empty WMI), false wear (Kingston 231 placeholder), false overheat (packed temperature), false errors (SK hynix junk in high bytes). The program prefers an honest UNKNOWN to a false conclusion.
8. **Race and TOCTOU guards** — a generation counter in the GUI (stale SMART), re-enumerate in the CLI (USB number shift): "the state changed between intent and action".
9. **Many fallback chains** — NVMe (~22 attempts), USB (SAT → bridges → IOCTL → WMI), capacity (3 methods), IDENTIFY (VPD → ATA PT → SAT). Survives the failure of any single method.
10. **Honest methodology** — QD1 latency strictly separated from marketing QD32 specs; TBW labeled a heuristic, not a spec; surface scan for SSDs honestly called HDD-centric.

---

*DISK Diagnostic Tool v2.4.5 — How It Works.*
*Developed by Serg and Claudine (Anthropic AI).*
