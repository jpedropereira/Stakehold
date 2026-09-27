# Vendored third-party JS

- `htmx.min.js` -- htmx 1.9.12, from https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
  sha256: 449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452
  Vendored (not CDN-loaded) so the app works offline and needs no script-src CDN allowance in
  the Content-Security-Policy (see api/security_headers.py). Verified against a second,
  independent CDN (cdn.jsdelivr.net) at vendor time -- both served byte-identical content.
- `chart.min.js` -- Chart.js 4.4.7's own UMD build (already minified, no separate .min variant
  published for 4.x), from https://unpkg.com/chart.js@4.4.7/dist/chart.umd.js
  sha256: 2812cb8825fdc57469eb2f7bb055e9429244e599920511ee477e828499b632cb
  Vendored for the same reason as htmx above -- the returns-over-time chart (task 12,
  portfolio.html/portfolio_chart.js) needs no script-src CDN allowance either. Verified against a
  second, independent CDN (cdn.jsdelivr.net) at vendor time -- both served byte-identical content.
