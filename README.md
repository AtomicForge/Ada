# LOOMIS

**A transparent, no-binary-blobs multiboot solution — no proprietary bootloader components, fully auditable from source.**

> ⚠️ **Status:** Early development / pre-MVP. Architecture and scope are still being finalized. This README reflects the current target design and will be updated as decisions are made.

## Why LOOMIS?

Existing multiboot USB tools (like Ventoy) work well but are closed enough, in parts, that a full independent audit isn't practical for security-sensitive deployments. LOOMIS aims to be a fully open, auditable alternative:

- No proprietary or unauditable binary blobs
- Every component built from pinned, reviewable source
- Suitable for private users up to enterprise, government, and public-sector deployment
- A contribution to digital sovereignty — usable equally by anyone, anywhere, independent of any single country's software supply chain

## Goals

- **Simple UX:** install LOOMIS once, copy ISO files to your boot medium, boot — no manual extraction, no per-distro config, no manual bootloader entries.
- **Broad OS support:** Linux and Windows as first-class targets.
- **Security-first boot chain:** Secure Boot, UEFI and legacy BIOS, x86_64 and aarch64.
- **Generic over special-cased:** prefer generic ISO-boot mechanisms; isolate any OS-specific handling behind a modular compatibility layer instead of hardcoding per-distro logic.
- **Reproducible and minimal:** small dependency surface, reproducible builds, clear interfaces, automated testing.

## Non-goals (for now)

- BSD support (FreeBSD/OpenBSD/NetBSD) — planned as future/optional scope, not blocking the Linux/Windows MVP.
- Exhaustive per-distro special handling — niche distros get generic/best-effort support rather than dedicated code paths.

## Architecture

- Bootloader: GRUB2
- Partition layout: *not yet documented*
- Secure Boot trust model: *not yet decided* (shim + Microsoft signing infrastructure vs. a LOOMIS-controlled trust chain with own keys)
- ISO compatibility handling: *not yet documented*

See [`docs/architecture.md`](docs/architecture.md) for full design rationale and rejected alternatives.

## Supported targets (planned)

| Category | Targets |
|---|---|
| Linux (priority) | Debian, Ubuntu LTS, Linux Mint, Pop!_OS, Fedora, RHEL, Rocky Linux, AlmaLinux, SUSE Linux Enterprise, openSUSE, Arch Linux, Gentoo, NixOS, Void Linux, Alpine |
| Linux (generic support) | Kali, elementary OS, Zorin OS, MX Linux, Manjaro, EndeavourOS, Tails, Qubes OS |
| Windows | Windows 10, Windows 11 |
| Future/optional | FreeBSD, OpenBSD, NetBSD |

## Installation

*Coming soon — no installable release yet.*

## Usage

1. Install LOOMIS to your target boot medium.
2. Copy your ISO file(s) onto it.
3. Boot and select the OS to boot.

## Contributing

LOOMIS is intended to be fully auditable — contributions and independent code review are especially welcome, including from people without prior context on the project's design decisions.

*Contribution guidelines coming soon.*

## License

Licensed under the [GNU General Public License v3.0](LICENSE) (GPLv3).

## Status / Roadmap

- [ ] Windows ISO boot investigation (unmodified ISO on data partition, direct boot)
- [ ] Secure Boot trust model decision (shim vs. MOK/own keys)
- [ ] MVP: Linux + Windows core boot flow
- [ ] Enterprise/government hardening (TPM 2.0, signed components, supply-chain security docs)
