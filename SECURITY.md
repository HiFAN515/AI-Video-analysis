# Security

VideoMind is designed as a **local-first** application.

- The server has no authentication and binds to `127.0.0.1` by default. Do not expose it directly to the public internet; if you need remote access, put it behind a reverse proxy with authentication and TLS.
- API keys are stored in plain text in `data/app.db` (SQLite). Protect that file and never commit `data/` or `.env`.
- Transcripts, audio and discussions are stored locally. Deleting a task deletes its database rows and audio; deleting audio alone keeps transcripts, analyses and conversations.
- Model downloads are fetched from Hugging Face (or the configured mirror) and PyTorch/Demucs weight hosts. Review the model licenses before redistributing model files.
- Covers are proxied through a small allowlist (Bilibili / YouTube image hosts); no arbitrary URL fetching is exposed.

If you find a vulnerability, please open a private security advisory on the GitHub repository rather than posting exploit details in a public issue.

When publishing a fork or a packaged release, verify that `data/`, `models/`, `.cache/`, `.venv/`, `frontend/node_modules/` and `.env` are excluded.
