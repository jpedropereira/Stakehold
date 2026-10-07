# Broker-parser plugins

Wheels placed here are installed into the portfolio image at build time (see `../Dockerfile`).
They register `BrokerParser`/`CashflowParser` implementations under the entry-point groups
`broker_import/discovery.py` reads. Everything here except this file is gitignored. This repo
never names or ships a concrete plugin.

```bash
uv build --wheel <path-to-plugin-project> -o services/portfolio/plugins/
docker compose -f services/portfolio/docker-compose.yml \
  -f services/portfolio/docker-compose.stakehold-net.yml up -d --build
```

The wheel is a build-time snapshot, like the rest of the image's code. After changing the
plugin, rebuild the wheel and the image. Delete an old wheel before adding a newer one, or both
get installed and discovery fails on the duplicate entry-point name.

Check what the running container registered:

```bash
docker exec portfolio-api /opt/stakehold/.venv/bin/portfolio broker list
```
