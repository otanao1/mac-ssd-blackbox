# mac-ssd-blackbox

**A flight recorder for macOS storage incidents.**

mac-ssd-blackbox is an open-source project to help investigate unexplained drops in free space on a Mac's internal SSD. It aims to collect lightweight, continuous observations and preserve useful evidence when abnormal storage consumption occurs.

> **Status: MVP implementation / real launchd validation pending.** Monitoring, retention, Incident investigation, CLI, configuration, and setup/removal are implemented. See [operations](docs/operations.md) for installation and commands, [core runtime](docs/core-runtime.md), and [storage lifecycle](docs/storage-incidents.md). No live launchd registration, login/reboot, or physical SSD/NAS disconnect test has been performed for Issue #8.

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

The initial interface is a CLI. The MVP supports monitoring start/stop/reload/status, manual scan execution and cancellation, scan history for L1/L2/L3/L9, Incident list/detail views, and machine-readable output such as JSON. A dedicated export command is not required for the MVP.

### Configuration and daemon

Common settings are version-controlled, while local overrides and machine-specific settings remain outside Git. L2/L3 targets use `conf.d`-style configuration and a shared path notation. L9 targets are generated dynamically during Incident handling and stored in the local data area rather than as permanent configuration.

A lightweight daemon is managed by `launchd`. It performs L1 observations and coordinates scheduling, Incident handling, scan exclusion, L9 waiting state, and child-process supervision. L2/L3/L9 scans run as separate processes so scan work and memory are released when each scan finishes.

### Installation

#### Monitoring engine

For the MVP, users obtain the project from GitHub and run a setup script. The clone working directory is not used directly as the installed runtime. Setup installs the runtime, CLI, configuration, data locations, and `launchd` registration into their intended locations.

Initial setup must ask for the storage destination before monitoring starts and validate that it exists and is writable. For external SSD or NAS destinations, the setup must verify that the intended mount is present.

Uninstallation must be supported, or at minimum documented with explicit manual removal steps. Runtime removal, configuration removal, and monitoring-data removal are treated separately; removing the runtime does not imply deleting collected data.

See [operations](docs/operations.md) for setup commands, configuration, and removal.

#### Blackbox Viewer

Blackbox Viewer is an independent macOS 14 or later application for viewing the existing L1 free-space history, with a menu bar display and desktop widgets. It currently requires building from source in Xcode; it does not install or replace the monitoring engine.

Set up the monitoring engine first. Open `viewer/BlackboxViewer.xcodeproj`, configure signing for both the Viewer and Widget targets, and build the macOS app. On first launch, choose the monitor's existing `local.json`, then its corresponding database storage folder. The Viewer reads the monitoring database without modifying it.

See the [Blackbox Viewer guide](viewer/README.md) for requirements, signing, initial setup, and usage.

## Scope and safety

The project focuses on transient or unexpectedly changing **local macOS SSD** usage, not NAS inventory or general-purpose disk visualization.

The MVP will **not** delete files automatically, terminate processes, perform automatic repairs, or require a GUI. It will prioritize low overhead, no elevated privileges by default, three-year retention, and clear handling of inaccessible paths and disconnected destinations. Storage defaults to the internal SSD but can be configured for external SSD or NAS; writes stop and an error is reported if the configured destination becomes unavailable, and resume when it recovers. No automatic destination failover or synchronization is planned.

**Privacy:** monitored paths and process details may contain personal information. Actual monitoring databases, exports, logs, secrets, and machine-specific configurations must not be committed to this public repository. Public documentation should use generic, synthetic examples.

## Roadmap

- [Issue #1 — Define MVP scope and acceptance criteria](https://github.com/otanao1/mac-ssd-blackbox/issues/1)
- [Issue #6 — Implement core monitoring and scan execution](https://github.com/otanao1/mac-ssd-blackbox/issues/6)
- [Issue #7 — Implement storage, retention, and Incident lifecycle](https://github.com/otanao1/mac-ssd-blackbox/issues/7)
- [Issue #8 — CLI, configuration, daemon, and installation](https://github.com/otanao1/mac-ssd-blackbox/issues/8) — [implementation and operations](docs/operations.md)
- Validate MVP behavior and resource usage on a real Mac
- Document installation, configuration, and removal before a public release

Contributions and suggestions are welcome as the design evolves.

Issue完了・Close判断時は、プロジェクト専用の [closing-issue-check](.agents/skills/closing-issue-check/SKILL.md) を使用します。Codexでは `$closing-issue-check` で明示的に呼び出せます。配置・比較・検証範囲は[確認記録](docs/closing-issue-check.md)を参照してください。
