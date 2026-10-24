# Bundled browser libraries

These unmodified release files let maps mount without an external JavaScript CDN.
The loader falls back to unpkg.com and cdnjs.cloudflare.com if a local asset fails.

- Leaflet 1.9.4: https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/
  BSD-2-Clause, retained in LICENSE.leaflet.
- jQuery 3.7.1: https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js
  MIT, retained in LICENSE.jquery. Used by Folium's generated popup markup.

Fetched over certificate-verified HTTPS on 24 October 2026.

SHA-256:

```
db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a  leaflet.js
a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6  leaflet.css
fc9a93dd241f6b045cbff0481cf4e1901becd0e12fb45166a8f17f95823f0b1a  jquery.min.js
```
