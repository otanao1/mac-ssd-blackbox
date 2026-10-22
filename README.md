# mac-ssd-blackbox

**A flight recorder for macOS storage incidents.**

mac-ssd-blackbox is an open-source project to help investigate unexplained drops in free space on a Mac's internal SSD. It aims to collect lightweight, continuous observations and preserve useful evidence when abnormal storage consumption occurs.

> **Status: Planning / pre-MVP.** The monitoring agent and CLI described below are design goals, not yet implemented features.

## Why?

Free space can disappear rapidly because of caches, temporary files, databases, or other changing local storage. Sometimes restarting the Mac recovers space, leaving little evidence of what happened. A one-off disk usage scan often misses the event.

The goal is to retain enough historical context to investigate *when* space disappeared and *what might have caused it*, while keeping the monitor itself small and low impact.

## Planned approach

### Adaptive layered collection

| Layer | Scope | Proposed baseline |
| --- | --- | --- |
| L1 | Overall internal SSD free/used space | Every minute |
| L2 | Broad internal SSD directories (Common) | Daily and on anomaly |
| L3 | Selected applications and change-prone locations | Hourly and on anomaly |
| L9 | Detailed investigation of suspected directories | On demand, following an Incident |

L2 and L3 use shared scan logic and configurable path lists. A path ending in `/*` records the parent total plus its immediate children; a plain path records its total only. The Incident investigation order is L3, then L2 if needed, then L9. See [monitoring design](docs/monitoring-design.md) and [Issue #2](https://github.com/otanao1/mac-ssd-blackbox/issues/2).

Collection intervals and targets will be configurable. Diagnostic scans will be bounded and must not accidentally traverse external mounts.

### Tiered retention

L1 minute-by-minute data and L2 daily data are retained without aggregation for up to three years. L3 hourly data is retained for 30 days, then aggregated into daily summaries retaining useful change information; those summaries are also retained for up to three years. Retention settings are configurable. There is no separate cap on the total size of mac-ssd-blackbox data.

### Incident evidence

A sharp drop in free space or a low-space threshold can create an **Incident** with timestamps, triggering measurements, historical context, and available diagnostics. Incidents retain evaluated findings for up to three years, with references to related scan IDs and optional excerpts of raw evidence where useful. They do not ordinarily duplicate full raw scan histories. Detection does not guarantee root-cause identification. 

### Investigation-friendly output

The initial interface will be a CLI with machine-readable local data/export suitable for diagnostic tools and developer agents.

## Scope and safety

The project focuses on transient or unexpectedly changing **local macOS SSD** usage, not NAS inventory or general-purpose disk visualization.

The MVP will **not** delete files automatically, terminate processes, perform automatic repairs, or require a GUI. It will prioritize low overhead, no elevated privileges by default, three-year retention, and clear handling of inaccessible paths and disconnected destinations. Storage defaults to the internal SSD but can be configured for external SSD or NAS; writes stop and an error is reported if the configured destination becomes unavailable, and resume when it recovers. No automatic destination failover or synchronization is planned.

**Privacy:** monitored paths and process details may contain personal information. Actual monitoring databases, exports, logs, secrets, and machine-specific configurations must not be committed to this public repository. Public documentation should use generic, synthetic examples.

## Roadmap

- [Issue #1 — Define MVP scope and acceptance criteria](https://github.com/otanao1/mac-ssd-blackbox/issues/1)
- Design collection, anomaly detection, Incident lifecycle, and retention
- Implement and validate a minimal CLI-based monitor
- Document installation, configuration, and removal before a public release

Contributions and suggestions are welcome as the design evolves.
