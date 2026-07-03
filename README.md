# Xteink X4 Commonplace Book

> Automatic screenshot archiving and reading diary for the Xteink X4 e‑ink reader, powered by a homelab service and Obsidian.

**Note:** The service runs on your homelab server and creates Obsidian-formatted Markdown files. Obsidian itself does not run on the server — the vault is synced via Syncthing to your laptop/phone where you view and edit notes.

## What It Does

When you press **File Transfer** on your Xteink X4, this homelab service:

- 📸 **Pulls screenshots** from the device, converts them to PNG, and organizes them by book and day into your Obsidian vault.
- 📖 **Captures reading progress** via a self‑hosted KOReader sync server, building a daily reading diary and per‑book timeline.
- 📱 **Shows real‑time progress** on the X4's File Transfer screen, just like the Calibre plugin does.
- 🔄 **Syncs automatically** to any device running Obsidian via Syncthing or your preferred sync tool.

**No custom firmware required.** No flashing. No app to install on your phone. Just press the button and your notes appear.

## Quick Start (Docker - Recommended)

1. **Configure docker-compose.yml** with your paths:
   ```yaml
   services:
     xteink:
       volumes:
         - ./vault:/data/vault          # Your Obsidian vault
         - ./config:/data/config        # Configuration file
   ```

2. **Set up your Obsidian vault** with these folders (on your laptop/phone, synced via Syncthing):
   - `Commonplace/<Book Title>/` — daily notes with screenshot embeds
   - `Reading Log/` — daily reading diary entries
   - `Books/` — per‑book timelines and metadata

3. **Run the container**:
   ```bash
   docker-compose up -d
   ```

4. **Configure the X4**: In Settings → KOReader Sync, enter your server URL and enable "Send Document Metadata":
   - **At home:** `http://homelab-ip:8081`
   - **Away (phone hotspot):** install [Tailscale](https://tailscale.com) on both the X4 (Android app) and the server, then use `http://server-tailscale-ip:8081`. Also set `DEVICE_HOST` to the X4's Tailscale IP in `docker-compose.yml` so screenshot sync works too.

5. **Press File Transfer** on the X4. Watch the screen show progress as screenshots sync. Reading progress updates automatically when you sync KOReader.

## Alternative: Running Without Docker

If you prefer to run without Docker:

1. **Set up your Obsidian vault** with the folders above

2. **Configure Syncthing** to sync the vault folder to your server

3. **Install dependencies and run**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python -m xteink_service
   ```

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System design, components, data flow
- **[IMPLEMENTATION.md](IMPLEMENTATION.md)** — Detailed implementation guide, code snippets, API reference
- **[PROJECT_SPEC.md](PROJECT_SPEC.md)** — Full requirements, user stories, constraints
- **[TODO.md](TODO.md)** — Build order and task list

## Technologies

- Python with `asyncio`, `aiohttp`, `websockets`
- Pillow for image conversion
- FastAPI for status page
- KOReader sync protocol (self‑hosted)
- Syncthing for cross‑device vault sync
- Obsidian as the note‑taking frontend

## License

MIT