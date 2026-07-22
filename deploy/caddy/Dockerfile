FROM caddy@sha256:844f60b64e4724a5aa8245e019dace0d3f199f7433ce6c57676cb30a920dbad9

# The upstream binary carries cap_net_bind_service. monGARS listens only on
# unprivileged container ports, so remove the file capability and run without
# any process capabilities.
USER root
RUN setcap -r /usr/bin/caddy \
    && test -z "$(getcap /usr/bin/caddy)"

USER 65534:65534
