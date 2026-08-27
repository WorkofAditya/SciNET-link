# SciNET Link

SciNET Link is a local dashboard and file-transfer server for the SciNET PC and phone ecosystem.

## Features

- Live CPU, RAM, disk and uptime monitoring
- Local file uploads and downloads
- Browser-based dashboard for phones and PCs
- Windows launcher
- Designed for a private phone-hotspot network

## Run on Windows

1. Install Python 3.11+.
2. Download or clone this repository to the SciNET PC.
3. Double-click `start.bat`.
4. On the phone connected to the PC's hotspot, open the local address printed by the launcher.

The first launch creates a `.venv` and installs the dependencies automatically.

## Custom local address

SciNET Link is intended to use a local hostname such as `scinet.local`. Local hostname discovery depends on mDNS support on the network and device. The server always remains reachable through the local IP and port shown by `start.bat` as a fallback.

## Storage

Files uploaded through the dashboard are stored in the `storage` directory. The directory is intentionally ignored by Git.

## Network note

The server listens on the PC's network interfaces so a phone on the same private hotspot can reach it. Do not expose this server to an untrusted network.
