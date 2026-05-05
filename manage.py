#!/usr/bin/env python3
"""Cyclops management CLI.

Sub-commands:
  provision <env>           One-shot host setup (Docker, cyclops user, /srv/cyclops)
  deploy <env>              git pull + docker compose build + up -d
  deploy-config <env>       Render + push gatekeeper fragment only (no rebuild)
  set-secret <env> <KEY>    Write a secret to /srv/cyclops/data/.env on host
  status <env>              docker compose ps
  logs <env> [service]      docker compose logs -f

Cyclops's compose project lives in `deploy/` (subdir of repo root) so
all docker compose invocations cd into /srv/cyclops/deploy/. Standard
host setup (docker + cyclops user UID 1103 + /srv/cyclops/{,data}/)
delegates to skeletor.workflow.provision_host.

The gatekeeper config fragment template lives in `deploy/gatekeeper.yaml.j2`
(per CONVENTIONS §7+§13: app-owned templates), pushed via skeletor's
`deploy-gatekeeper-config.yml` playbook.
"""
import argparse
import getpass
import os
import sys
import textwrap

from skeletor import RemoteError, secrets
from skeletor import workflow as wf

_LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
_DEPLOY_DIR = os.path.join(_LOCAL_DIR, "deploy")
_SKELETOR_DIR = os.environ.get(
    "SKELETOR_DIR",
    os.path.join(os.path.dirname(_LOCAL_DIR), "skeletor"),
)


ENV_HOSTS = {
    "prod":    "prod.callendina.com",
    "staging": "staging.callendina.com",
}
SSH_USER = "skeletor"
REPO_DIR = "/srv/cyclops"
COMPOSE_DIR = "/srv/cyclops/deploy"  # docker compose runs from the deploy/ subdir
DATA_DIR = "/srv/cyclops/data"
GITHUB_REPO = "git@github.com:Callendina/cyclops.git"
CYCLOPS_UID = 1103
CYCLOPS_GID = 1103


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _host(env: str) -> str:
    if env not in ENV_HOSTS:
        print(f"unknown env: {env!r} (must be one of {sorted(ENV_HOSTS)})", file=sys.stderr)
        sys.exit(1)
    return ENV_HOSTS[env]


def _run(host: str, cmd: str) -> None:
    """SSH to host, run cmd, stream output. Exits on failure."""
    print(f"# {SSH_USER}@{host}: {cmd[:120]}{'...' if len(cmd) > 120 else ''}", flush=True)
    try:
        wf.run_remote_script(host, cmd, user=SSH_USER)
    except RemoteError as e:
        sys.exit(e.result.returncode)


# ─── provision ────────────────────────────────────────────────────────────────

def cmd_provision(env: str) -> None:
    """Idempotent host setup. Safe to re-run."""
    host = _host(env)
    # Standard host setup: docker, cyclops user (UID 1103), /srv/cyclops/{,data}
    wf.provision_host(host, app="cyclops", uid=CYCLOPS_UID, gid=CYCLOPS_GID, user=SSH_USER)

    # Bring repo content into /srv/cyclops/. Operator must have a deploy
    # key for Callendina/cyclops on the host.
    wf.git_clone_or_pull(host, dest=REPO_DIR, repo_url=GITHUB_REPO, user=SSH_USER)

    _run(host, textwrap.dedent(f"""\
        echo '=== Provision complete ==='
        echo
        echo 'Next steps:'
        echo '  1. If cyclops needs a .env, place it at {DATA_DIR}/.env (mode 600, owned by skeletor)'
        echo '  2. Run: ./manage.py deploy {env}'
    """))


# ─── deploy ───────────────────────────────────────────────────────────────────

def _deploy_gatekeeper_fragment(env: str) -> None:
    """Render deploy/gatekeeper.yaml.j2 → gk's config.d/cyclops-<env>.yaml via skeletor."""
    print("# Deploying gatekeeper fragment (via skeletor playbook)", flush=True)
    wf.run_playbook(
        "deploy-gatekeeper-config.yml",
        skeletor_dir=_SKELETOR_DIR,
        extra_vars={
            "app_slug": "cyclops",
            "env": env,
            "template_path": os.path.join(_DEPLOY_DIR, "gatekeeper.yaml.j2"),
        },
    )


def cmd_deploy(env: str) -> None:
    """Push gk fragment, then git pull + docker compose build + up -d on host."""
    host = _host(env)
    _deploy_gatekeeper_fragment(env)
    compose_files = "-f docker-compose.yml -f docker-compose.{env}.yml".format(env=env)
    script = textwrap.dedent(f"""\
        set -e
        cd {REPO_DIR}
        echo '=== 1. git pull ==='
        git pull --ff-only
        cd {COMPOSE_DIR}
        echo '=== 2. docker compose build ==='
        docker compose {compose_files} build
        echo '=== 3. docker compose up -d ==='
        docker compose {compose_files} up -d --remove-orphans
        echo '=== 4. ps ==='
        docker compose {compose_files} ps
    """)
    _run(host, script)


def cmd_deploy_config(env: str) -> None:
    """Render + push gatekeeper fragment only (no rebuild)."""
    _deploy_gatekeeper_fragment(env)


# ─── set-secret ───────────────────────────────────────────────────────────────

def cmd_set_secret(env: str, key: str) -> None:
    host = _host(env)
    value = getpass.getpass(f"{key} ({env}): ")
    if not value.strip():
        print("empty value — aborted", file=sys.stderr)
        sys.exit(1)
    secrets.set_secret(host, key, value, app_slug="cyclops", user=SSH_USER)
    print(f"  {key} written to {SSH_USER}@{host}:{DATA_DIR}/.env")


# ─── status / logs ────────────────────────────────────────────────────────────

def cmd_status(env: str) -> None:
    compose_files = f"-f docker-compose.yml -f docker-compose.{env}.yml"
    _run(_host(env), f"cd {COMPOSE_DIR} && docker compose {compose_files} ps")


def cmd_logs(env: str, service: str | None) -> None:
    svc = service or ""
    compose_files = f"-f docker-compose.yml -f docker-compose.{env}.yml"
    _run(_host(env), f"cd {COMPOSE_DIR} && docker compose {compose_files} logs -f --tail=200 {svc}")


# ─── argparse ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Cyclops management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for cmd in ("provision", "deploy", "deploy-config", "status"):
        p = sub.add_parser(cmd, help=cmd)
        p.add_argument("env", choices=list(ENV_HOSTS), help="Target environment")

    p = sub.add_parser("logs", help="docker compose logs -f")
    p.add_argument("env", choices=list(ENV_HOSTS), help="Target environment")
    p.add_argument("service", nargs="?", default=None,
                   help="Service name (default: all)")

    p = sub.add_parser("set-secret", help="Write a secret to /srv/cyclops/data/.env")
    p.add_argument("env", choices=list(ENV_HOSTS), help="Target environment")
    p.add_argument("key", metavar="KEY", help="Environment variable name")

    args = parser.parse_args()

    if args.cmd == "provision":
        cmd_provision(args.env)
    elif args.cmd == "deploy":
        cmd_deploy(args.env)
    elif args.cmd == "deploy-config":
        cmd_deploy_config(args.env)
    elif args.cmd == "set-secret":
        cmd_set_secret(args.env, args.key)
    elif args.cmd == "status":
        cmd_status(args.env)
    elif args.cmd == "logs":
        cmd_logs(args.env, args.service)


if __name__ == "__main__":
    main()
