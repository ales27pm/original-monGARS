FROM caddy@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648

# The upstream binary carries cap_net_bind_service. monGARS listens only on
# unprivileged container ports, so remove the file capability and run without
# any process capabilities.
USER root
RUN setcap -r /usr/bin/caddy \
    && test -z "$(getcap /usr/bin/caddy)"

USER 65534:65534
