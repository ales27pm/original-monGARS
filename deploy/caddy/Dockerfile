FROM caddy@sha256:685ae3d7c34d9b5f8caeddc5d14051db7bbd88ff1f5a2fa0c16bd8ca328fb7af

# The upstream binary carries cap_net_bind_service. monGARS listens only on
# unprivileged container ports, so remove the file capability and run without
# any process capabilities.
USER root
RUN setcap -r /usr/bin/caddy \
    && test -z "$(getcap /usr/bin/caddy)"

USER 65534:65534
