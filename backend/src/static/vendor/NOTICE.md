# Vendored front-end libraries

These files are served from the app itself so that no page loads script or styles from a third
party, the app works offline, and the Content-Security-Policy can restrict scripts to `'self'`.
Each is an unmodified copy of the published file; update by replacing the file and the reference in
`templates/_head_meta.html`.

| Library | Version | File | Source | Licence |
| --- | --- | --- | --- | --- |
| Tailwind CSS (Play CDN build, with the typography plugin 0.5.16) | 3.4.17 | `tailwind/tailwind-3.4.17-typography-0.5.16.js` | `https://cdn.tailwindcss.com/3.4.17?plugins=typography@0.5.16` | MIT |
| Chart.js | 4.6.2 | `chartjs/chart-4.6.2.umd.min.js` | `https://cdn.jsdelivr.net/npm/chart.js@4.6.2/dist/chart.umd.min.js` | MIT |
| chartjs-plugin-datalabels | 2.2.0 | `chartjs/chartjs-plugin-datalabels-2.2.0.min.js` | `https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js` | MIT |
| htmx | 2.0.4 | `htmx/htmx-2.0.4.min.js` | `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` | 0BSD |
| Alpine.js | 3.15.12 | `alpine/alpine-3.15.12.min.js` | `https://unpkg.com/alpinejs@3.15.12/dist/cdn.min.js` | MIT |
| Font Awesome Free (CSS and woff2 fonts) | 6.4.0 | `fontawesome/` | `https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/` | Icons CC BY 4.0, fonts SIL OFL 1.1, code MIT |

The htmx, Alpine and Font Awesome copies match the integrity hashes the templates previously pinned
for the CDN versions. The mana, keyrune and font directories carry their own notices.
