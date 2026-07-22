# Internal SearXNG service

`settings.yml` is an API-only override for monGARS. It inherits the official
defaults, permits only JSON search responses, disables the public-instance limiter
and image proxy, and bounds outbound requests.

Mount the file read-only at `/etc/searxng/settings.yml`. Keep the service on the
private application network and do not publish port `8080` to the host. Compose
reads a random value from `secrets/searxng_secret.txt` into `SEARXNG_SECRET`; the
value committed in the settings file is an intentional fail-obvious placeholder.

The validated image is:

```text
docker.io/searxng/searxng@sha256:b8ca38ba06eea544d7555e88321e212ddc0d5c3c7de055419cfb2e5c6bf30812
```

It identifies itself as SearXNG `2026.7.19-6da6eee26`. Revalidate a replacement
digest before upgrading because this configuration inherits upstream defaults.

Run the isolated configuration check with:

```bash
deploy/searxng/check.sh
```

The check launches a disposable, loopback-only container, verifies a JSON search
response, and verifies that the HTML search format is rejected.
