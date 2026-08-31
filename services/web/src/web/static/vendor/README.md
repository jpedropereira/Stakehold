# Vendored third-party JS

- `htmx.min.js` -- htmx 1.9.12, from https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
  sha256: 449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452
  Vendored (not CDN-loaded) so the app works offline and needs no script-src CDN allowance in
  the Content-Security-Policy (see api/security_headers.py). Verified against a second,
  independent CDN (cdn.jsdelivr.net) at vendor time -- both served byte-identical content.
