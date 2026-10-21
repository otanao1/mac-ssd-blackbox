# mac-ssd-blackbox

**A flight recorder for macOS storage incidents.**

mac-ssd-blackbox is an open-source project to help investigate unexplained drops in free space on a Mac's internal SSD. It aims to collect lightweight, continuous observations and preserve useful evidence when abnormal storage consumption occurs.

> **Status: Planning / pre-MVP.** The monitoring agent and CLI described below are design goals, not yet implemented features.

## Why?

Free space can disappear rapidly because of caches, temporary files, databases, or other changing local storage. Sometimes restarting the Mac recovers space, leaving little evidence of what happened. A one-off disk usage scan often misses the event.

The goal is to retain enough historical context to investigate *when* space disappeared and *what might have caused it*, while keeping the monitor itself small and low impact.

## Planned approach

### Adaptive, three-layer collection

| Layer | Scope | Proposed baseline |
| --- | --- | --- |
| L1 | Overall internal SSD free/used space | Every minute |
| L2 | Selected directories and known change-prone locations | Hourly and on anomaly |
| L3 | Narrower subdirectory/file-level diagnostics | Daily and on anomaly |

Collection intervals and targets will be configurable. Diagnostic scans will be bounded and must not accidentally traverse external mounts.

### Tiered retention

Recent raw observations (initial proposal: seven days) will be preserved at full granularity. Older ordinary data will be aggregated into hourly/daily summaries with important extremes retained, rather than storing averages alone. Retention windows and storage limits will be configurable.

### Incident evidence

A sharp drop in free space or a low-space threshold can create an **Incident** with timestamps, triggering measurements, historical context, and available diagnostics. Incident evidence will follow separate retention rules so that normal history compaction does not erase useful investigation material. Detection does not guarantee automatic root-cause identification.

### Investigation-friendly output

The initial interface will be a CLI with machine-readable local data/export suitable for diagnostic tools and developer agents.

## Scope and safety

The project focuses on transient or unexpectedly changing **local macOS SSD** usage, not NAS inventory or general-purpose disk visualization.

The MVP will **not** delete files automatically, terminate processes, perform automatic repairs, or require a GUI. It will prioritize low overhead, no elevated privileges by default, bounded local storage, and clear handling of inaccessible paths and disconnected destinations.

**Privacy:** monitored paths and process details may contain personal information. Actual monitoring databases, exports, logs, secrets, and machine-specific configurations must not be committed to this public repository. Public documentation should use generic, synthetic examples.

## Roadmap

- [Issue #1 — Define MVP scope and acceptance criteria](https://github.com/otanao1/mac-ssd-blackbox/issues/1)
- Design collection, anomaly detection, Incident lifecycle, and retention
- Implement and validate a minimal CLI-based monitor
- Document installation, configuration, and removal before a public release

Contributions and suggestions are welcome as the design evolves.
