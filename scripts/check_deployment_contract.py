#!/usr/bin/env python3
"""Validate production Compose isolation and deployed image pin parity."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SHA256_IMAGE_PATTERN = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class ContractError(RuntimeError):
    """Raised when a deployment invariant is not satisfied."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def read_text(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(bool(separator), f"invalid .env.example entry: {raw_line!r}")
        values[key] = value
    return values


def compose_defaults(compose_text: str, variable: str) -> set[str]:
    return set(re.findall(rf"\$\{{{re.escape(variable)}:-([^}}]+)\}}", compose_text))


def readonly_shell_value(script_text: str, variable: str) -> str:
    match = re.search(rf'^readonly {re.escape(variable)}="([^"]+)"$', script_text, re.MULTILINE)
    require(match is not None, f"missing readonly {variable} image reference")
    return match.group(1)


def supply_chain_matrix(workflow_text: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    current_name: str | None = None
    for line in workflow_text.splitlines():
        name_match = re.match(r"^ {10}- name: (\S+)$", line)
        if name_match:
            current_name = name_match.group(1)
            entries[current_name] = {}
            continue
        if current_name is None:
            continue
        value_match = re.match(r"^ {12}(ref|build): (\S+)$", line)
        if value_match:
            entries[current_name][value_match.group(1)] = value_match.group(2)
    return {name: values for name, values in entries.items() if values}


def compose_model() -> dict[str, object]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith(("MONGARS_", "POSTGRES_", "COMPOSE_")):
            environment.pop(key)

    command = [
        "docker",
        "compose",
        "--env-file",
        ".env.example",
        "--profile",
        "gpu",
        "--profile",
        "web-search",
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(  # noqa: S603 - fixed local Docker CLI invocation
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    require(
        result.returncode == 0,
        "docker compose could not render .env.example:\n" + result.stderr.strip(),
    )
    payload = json.loads(result.stdout)
    require(isinstance(payload, dict), "Compose model is not a JSON object")
    return payload


def validate_image_parity(model: dict[str, object]) -> None:
    environment = parse_dotenv(read_text(".env.example"))
    compose_text = read_text("compose.yaml")
    workflow_matrix = supply_chain_matrix(read_text(".github/workflows/supply-chain.yml"))

    pinned_images = {
        "MONGARS_POSTGRES_IMAGE": environment.get("MONGARS_POSTGRES_IMAGE", ""),
        "MONGARS_OLLAMA_IMAGE": environment.get("MONGARS_OLLAMA_IMAGE", ""),
        "MONGARS_SEARXNG_IMAGE": environment.get("MONGARS_SEARXNG_IMAGE", ""),
        "MONGARS_SQUID_IMAGE": environment.get("MONGARS_SQUID_IMAGE", ""),
    }
    for variable, image in pinned_images.items():
        require(
            bool(SHA256_IMAGE_PATTERN.fullmatch(image)),
            f"{variable} must be an immutable sha256 image reference",
        )
        defaults = compose_defaults(compose_text, variable)
        require(
            defaults == {image},
            f"{variable} differs between .env.example and compose.yaml: {defaults}",
        )

    caddy_tag = environment.get("MONGARS_CADDY_IMAGE", "")
    require(caddy_tag == "mongars-caddy:local", "Caddy must use the local hardened build tag")
    require(
        compose_defaults(compose_text, "MONGARS_CADDY_IMAGE") == {caddy_tag},
        "Caddy build tag differs between .env.example and compose.yaml",
    )
    dockerfile_match = re.search(
        r"^FROM (\S+)$", read_text("deploy/caddy/Dockerfile"), re.MULTILINE
    )
    require(dockerfile_match is not None, "Caddy Dockerfile is missing a base image")
    caddy_base = dockerfile_match.group(1)
    require(
        bool(SHA256_IMAGE_PATTERN.fullmatch(caddy_base)),
        "Caddy Dockerfile base image must be pinned by sha256 digest",
    )

    require(
        readonly_shell_value(read_text("scripts/ci-local.sh"), "ci_postgres_image")
        == pinned_images["MONGARS_POSTGRES_IMAGE"],
        "ci-local PostgreSQL image differs from the production pin",
    )
    require(
        readonly_shell_value(read_text("deploy/searxng/check.sh"), "searxng_image")
        == pinned_images["MONGARS_SEARXNG_IMAGE"],
        "SearXNG check image differs from the production pin",
    )
    for check_path in ("deploy/searxng/check.sh", "deploy/egress-proxy/check.sh"):
        require(
            readonly_shell_value(read_text(check_path), "squid_image")
            == pinned_images["MONGARS_SQUID_IMAGE"],
            f"{check_path} Squid image differs from the production pin",
        )

    expected_matrix = {
        "postgres-pgvector": pinned_images["MONGARS_POSTGRES_IMAGE"],
        "ollama": pinned_images["MONGARS_OLLAMA_IMAGE"],
        "searxng": pinned_images["MONGARS_SEARXNG_IMAGE"],
        "search-egress-proxy": pinned_images["MONGARS_SQUID_IMAGE"],
    }
    for name, expected_ref in expected_matrix.items():
        entry = workflow_matrix.get(name, {})
        require(entry.get("ref") == expected_ref, f"supply-chain {name} image pin differs")
        require(entry.get("build") == "pull", f"supply-chain {name} must scan the pulled pin")
    require(
        workflow_matrix.get("caddy") == {"ref": "mongars-caddy:security", "build": "caddy"},
        "supply-chain Caddy scan must build the hardened Dockerfile",
    )

    services = model.get("services")
    require(isinstance(services, dict), "Compose model has no services object")
    expected_service_images = {
        "postgres": pinned_images["MONGARS_POSTGRES_IMAGE"],
        "ollama": pinned_images["MONGARS_OLLAMA_IMAGE"],
        "searxng": pinned_images["MONGARS_SEARXNG_IMAGE"],
        "search-egress-proxy": pinned_images["MONGARS_SQUID_IMAGE"],
        "https": caddy_tag,
        "https-volume-init": caddy_tag,
    }
    for service_name, expected_image in expected_service_images.items():
        service = services.get(service_name)
        require(isinstance(service, dict), f"Compose service {service_name} is missing")
        require(
            service.get("image") == expected_image,
            f"rendered {service_name} image differs from the reviewed deployment image",
        )


def service_networks(services: dict[str, object], service_name: str) -> set[str]:
    service = services.get(service_name)
    require(isinstance(service, dict), f"Compose service {service_name} is missing")
    networks = service.get("networks", {})
    require(isinstance(networks, dict), f"Compose service {service_name} networks are invalid")
    return set(networks)


def validate_network_isolation(model: dict[str, object]) -> None:
    services = model.get("services")
    networks = model.get("networks")
    require(isinstance(services, dict), "Compose model has no services object")
    require(isinstance(networks, dict), "Compose model has no networks object")

    expected = {
        "api": {"backend", "edge", "search"},
        "worker": {"backend", "parser"},
        "parser": {"parser"},
        "https": {"edge", "ingress"},
        "searxng": {"search", "search-proxy"},
        "search-egress-proxy": {"search-egress", "search-proxy"},
    }
    for service_name, expected_networks in expected.items():
        actual_networks = service_networks(services, service_name)
        require(
            actual_networks == expected_networks,
            f"{service_name} networks changed: expected {sorted(expected_networks)}, "
            f"got {sorted(actual_networks)}",
        )

    egress_members = {
        service_name
        for service_name in services
        if "search-egress" in service_networks(services, service_name)
    }
    require(
        egress_members == {"search-egress-proxy"},
        f"only the forward proxy may join search-egress, got {sorted(egress_members)}",
    )

    for internal_name in ("backend", "edge", "parser", "search", "search-proxy"):
        network = networks.get(internal_name)
        require(isinstance(network, dict), f"Compose network {internal_name} is missing")
        require(
            network.get("internal") is True, f"Compose network {internal_name} must be internal"
        )
    for external_name in ("ingress", "search-egress"):
        network = networks.get(external_name)
        require(isinstance(network, dict), f"Compose network {external_name} is missing")
        require(
            network.get("internal") is not True,
            f"Compose network {external_name} must provide its intended external boundary",
        )

    published_services = {
        service_name for service_name, service in services.items() if service.get("ports")
    }
    require(
        published_services == {"https"},
        f"only Caddy may publish host ports, got {sorted(published_services)}",
    )

    parser = services.get("parser")
    require(isinstance(parser, dict), "Compose parser service is missing")
    require(parser.get("user") == "10001:10001", "parser must run as the fixed non-root UID")
    require(parser.get("read_only") is True, "parser filesystem must be read-only")
    require(parser.get("cap_drop") == ["ALL"], "parser must drop every Linux capability")
    require(
        "no-new-privileges:true" in parser.get("security_opt", []),
        "parser must enable no-new-privileges",
    )
    require(not parser.get("secrets"), "parser must not receive Compose secrets")
    require(not parser.get("volumes"), "parser must not receive persistent or host volumes")
    parser_environment = parser.get("environment", {})
    require(isinstance(parser_environment, dict), "parser environment must be a mapping")
    require(
        all(str(key).startswith("MONGARS_PARSER_") for key in parser_environment),
        "parser environment must contain only non-secret parser settings",
    )


def main() -> int:
    try:
        model = compose_model()
        validate_image_parity(model)
        validate_network_isolation(model)
    except (ContractError, json.JSONDecodeError) as error:
        print(f"deployment contract check failed: {error}", file=sys.stderr)
        return 1
    print("Deployment image and network contract check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
