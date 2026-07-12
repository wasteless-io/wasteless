# Fonts

Self-hosted webfonts (the UI must work offline / air-gapped — no CDN).

| File | Family | Weight | Source |
|---|---|---|---|
| `space-grotesk-latin-500.woff2` | Space Grotesk | 500 | fontsource `@fontsource/space-grotesk@5.2.5` |
| `space-grotesk-latin-700.woff2` | Space Grotesk | 700 | fontsource `@fontsource/space-grotesk@5.2.5` |
| `ibm-plex-mono-latin-400.woff2` | IBM Plex Mono | 400 | fontsource `@fontsource/ibm-plex-mono@5.2.5` |
| `ibm-plex-mono-latin-600.woff2` | IBM Plex Mono | 600 | fontsource `@fontsource/ibm-plex-mono@5.2.5` |

Latin subset only. Both families are licensed under the SIL Open Font
License 1.1 (see each family's upstream repository for the full text).

Usage (declared in `ui/templates/base.html`):
- **Space Grotesk** — headings and the sidebar wordmark (`--font-display`).
- **IBM Plex Mono** — money figures, KPI values, resource IDs (`--font-mono`).
