FROM caddy@sha256:14a9c00d4e833ebc2b65d36515b37bde3b73f0b323a2663aaafc88953d8c4e3f

# The upstream binary carries cap_net_bind_service. monGARS listens only on
# unprivileged container ports, so remove the file capability and run without
# any process capabilities.
USER root
RUN setcap -r /usr/bin/caddy \
    && test -z "$(getcap /usr/bin/caddy)"

USER 65534:65534
