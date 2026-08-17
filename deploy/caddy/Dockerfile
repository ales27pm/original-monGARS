FROM caddy@sha256:df7f1c2fb114453b951de51a98efc010db1655a92c2e86be6706714e2417a78d

# The upstream binary carries cap_net_bind_service. monGARS listens only on
# unprivileged container ports, so remove the file capability and run without
# any process capabilities.
USER root
RUN setcap -r /usr/bin/caddy \
    && test -z "$(getcap /usr/bin/caddy)"

USER 65534:65534
