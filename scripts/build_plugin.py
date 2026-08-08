#!/usr/bin/env python3
"""Build Loon plugin / rule lists from Animeko media-source JSON feeds."""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "sources.json"
DEFAULT_OUT = ROOT / "dist"

URL_RE = re.compile(r"https?://([^/\s\"'<>\\]+)", re.I)
MULTI_SUFFIXES = (
    "github.io",
    "githubusercontent.com",
    "bgm.tv",
    "pages.dev",
    "vercel.app",
)


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str, timeout: int = 45) -> tuple[bytes, str]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "animeko-loon-builder/1.0 (+https://github.com/beckyeeky/animeko-loon)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("content-type", "")


def registrable_domain(host: str) -> str:
    host = host.lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        return host
    parts = host.split(".")
    if len(parts) < 2:
        return host
    for suffix in sorted(MULTI_SUFFIXES, key=len, reverse=True):
        if host == suffix or host.endswith("." + suffix):
            need = len(suffix.split(".")) + 1
            if len(parts) >= need:
                return ".".join(parts[-need:])
            return host
    return ".".join(parts[-2:])


def extract_hosts(text: str) -> set[str]:
    hosts: set[str] = set()
    for raw in URL_RE.findall(text):
        h = raw.lower().split("@")[-1].split(":")[0].strip(".")
        if not h or h.replace(".", "").isdigit():
            continue
        if "." not in h:
            continue
        hosts.add(h)
    return hosts


def excluded(host: str, roots: set[str], exclude_hosts: set[str], exclude_suffixes: set[str]) -> bool:
    if host in exclude_hosts:
        return True
    root = registrable_domain(host)
    if root in exclude_suffixes:
        return True
    for suf in exclude_suffixes:
        if host == suf or host.endswith("." + suf):
            return True
    # drop pure IP-like leftovers
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        return True
    return False


def collect_from_sources(cfg: dict, cache_dir: Path | None = None) -> dict:
    sources_meta = []
    all_hosts: set[str] = set()
    exclude_hosts = {h.lower() for h in cfg.get("exclude_hosts", [])}
    exclude_suffixes = {s.lower() for s in cfg.get("exclude_suffixes", [])}

    for src in cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        url = src["url"]
        item = {
            "id": src.get("id") or src.get("name") or url,
            "name": src.get("name") or src.get("id") or url,
            "url": url,
            "ok": False,
            "error": None,
            "bytes": 0,
            "media_sources": None,
            "hosts": [],
        }
        try:
            data, _ctype = fetch(url)
            item["bytes"] = len(data)
            text = data.decode("utf-8", "replace")
            if cache_dir is not None:
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / f"{item['id']}.json").write_text(text, encoding="utf-8")
            try:
                payload = json.loads(text)
                ms = (
                    payload.get("exportedMediaSourceDataList", {})
                    .get("mediaSources")
                )
                if isinstance(ms, list):
                    item["media_sources"] = len(ms)
            except json.JSONDecodeError:
                pass
            hosts = extract_hosts(text)
            item["hosts"] = sorted(hosts)
            all_hosts |= hosts
            item["ok"] = True
        except Exception as exc:  # noqa: BLE001 - surface any fetch/parse failure
            item["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[warn] fetch failed {url}: {item['error']}", file=sys.stderr)
        sources_meta.append(item)

    media_roots: set[str] = set()
    for host in sorted(all_hosts):
        if excluded(host, media_roots, exclude_hosts, exclude_suffixes):
            continue
        media_roots.add(registrable_domain(host))

    # core + hosting are added separately; drop overlaps from media_roots later
    core = {d.lower() for d in cfg.get("core_domains", [])}
    media_roots = {r for r in media_roots if r not in core and r not in exclude_suffixes}

    return {
        "sources": sources_meta,
        "raw_hosts": sorted(all_hosts),
        "media_roots": sorted(media_roots),
        "core_domains": sorted(core),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def parse_hosting_rules(rules: Iterable[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in rules:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 2:
            continue
        out.append((parts[0].strip().upper(), parts[1].strip().lower()))
    return out


def rule_lines(kind: str, value: str, policy: str | None = None) -> str:
    if policy:
        return f"{kind},{value},{policy}"
    return f"{kind},{value}"


def build_rule_body(
    report: dict,
    cfg: dict,
    *,
    with_policy: bool,
    policy_token: str,
) -> list[str]:
    lines: list[str] = []
    pol = policy_token if with_policy else None

    lines.append("# Animeko / Ani core services")
    for d in cfg.get("core_domains", []):
        lines.append(rule_lines("DOMAIN-SUFFIX", d, pol))

    lines.append("")
    lines.append("# Source list hosting")
    for kind, value in parse_hosting_rules(cfg.get("hosting_rules", [])):
        lines.append(rule_lines(kind, value, pol))

    lines.append("")
    lines.append("# Media source sites extracted from source JSON")
    # avoid duplicating hosts already covered by hosting_rules / core
    covered = {d.lower() for d in cfg.get("core_domains", [])}
    for kind, value in parse_hosting_rules(cfg.get("hosting_rules", [])):
        covered.add(value.lower())
        covered.add(registrable_domain(value))

    for root in report["media_roots"]:
        if root in covered:
            continue
        if any(root == c or root.endswith("." + c) for c in covered):
            continue
        lines.append(rule_lines("DOMAIN-SUFFIX", root, pol))

    return lines


def render_plugin(cfg: dict, report: dict, rule_body: list[str]) -> str:
    meta = cfg["plugin"]
    ua_patterns = [u["pattern"] for u in cfg.get("user_agents", []) if u.get("pattern")]
    ua_default = ua_patterns[0] if ua_patterns else "*Animeko*"
    ua_choices = ",".join(f'"{p}"' for p in (ua_patterns or ["*Animeko*", "*Ani*", "off"]))
    if '"off"' not in ua_choices:
        ua_choices = ua_choices + ',"off"' if ua_choices else '"off"'

    # Argument-driven policy: Loon plugin rules support DIRECT/REJECT/PROXY only.
    # PROXY lets the user pick a policy group in the plugin UI / runtime.
    header = f"""#!name = {meta['name']}
#!desc = {meta['desc']}
#!author = {meta['author']}
#!homepage = {meta['homepage']}
#!icon = {meta['icon']}
#!system = {meta['system']}
#!system_version = {meta['system_version']}
#!loon_version = {meta['loon_version']}
#!tag = {meta['tag']}
#!type = normal

[Argument]
policy = select,"DIRECT","PROXY","REJECT",tag=默认策略,desc=插件规则仅支持 DIRECT / PROXY / REJECT。PROXY 表示由你在 Loon 中绑定策略组。
ua_mode = select,"off","on",tag=USER-AGENT 限定,desc=on 时仅当 UA 匹配下方模式才套用策略。须先在最新请求中确认 Animeko 真实 UA；默认 off（仅域名）。
ua_pattern = select,{ua_choices},tag=UA 通配符,desc=仅 ua_mode=on 时生效。选 off 等于不按 UA 过滤。

[Rule]
# generated_at: {report['generated_at']}
# sources_ok: {sum(1 for s in report['sources'] if s['ok'])}/{len(report['sources'])}
# media_roots: {len(report['media_roots'])}
# notes:
# 1. iOS 不能按 App 进程分流；同域名的其他 App 也会命中。
# 2. 源 JSON 多是检索站域名，播放 CDN/m3u8 常不在列表内，需按最新请求补 DOMAIN-SUFFIX。
# 3. USER-AGENT 规则仅匹配 HTTP/HTTPS 且能看到 Header 的请求；默认关闭。
# 4. 逻辑规则 AND 需要 Loon 3.1.7+。

"""

    # Domain-only rules (default path). policy argument is NOT expanded by Loon Rule engine
    # the same way as Script enable=; plugin rules are static. We emit DIRECT as default
    # and also ship a PROXY variant file. For selectable policy inside one plugin, emit
    # three parallel blocks gated is not supported for Rule. Practical approach:
    # generate rules with DIRECT (primary), and document PROXY/REJECT list variants.
    #
    # However user asked to define policy in plugin. Loon plugin rule policy can be PROXY
    # meaning user-selected group — but still static per line. We'll generate using
    # DIRECT by default in the main plugin, and produce companion plugins for PROXY.
    #
    # Better approach used by many plugins: fixed DIRECT in main plugin; Argument only
    # documents intent; plus separate list files without policy for rule-subscription.
    #
    # User explicitly wants selectable policy. Since Rule lines cannot reference
    # ${policy} like Rewrite V2, we generate the main plugin with DIRECT and additional
    # plugins Animeko-PROXY.plugin / Animeko-REJECT.plugin, OR one plugin with PROXY
    # (user binds group) as "custom".
    #
    # Final design:
    # - Animeko.plugin : policy = DIRECT (most common: bypass proxy)
    # - Animeko-PROXY.plugin : policy = PROXY
    # - lists without policy for subscription
    # - optional UA AND rules only in experimental section comments + separate file

    body_lines = []
    for line in rule_body:
        if not line or line.startswith("#"):
            body_lines.append(line)
            continue
        body_lines.append(line)

    # Experimental UA section as comments + separate enabled block file content note
    ua_note = """
# --- USER-AGENT experimental (disabled by default; copy to local [Rule] after confirming UA) ---
# USER-AGENT,*Animeko*,DIRECT
# USER-AGENT,*Ani*,DIRECT
# AND,((USER-AGENT,*Animeko*),(DOMAIN-SUFFIX,example-cdn.com)),DIRECT
"""
    return header + "\n".join(body_lines) + "\n" + ua_note


def render_plugin_with_policy(cfg: dict, report: dict, policy: str) -> str:
    meta = cfg["plugin"]
    body = build_rule_body(report, cfg, with_policy=True, policy_token=policy)
    sources_ok = sum(1 for s in report["sources"] if s["ok"])
    lines = [
        f"#!name = {meta['name']} ({policy})",
        f"#!desc = {meta['desc']} 当前构建策略固定为 {policy}。",
        f"#!author = {meta['author']}",
        f"#!homepage = {meta['homepage']}",
        f"#!icon = {meta['icon']}",
        f"#!system = {meta['system']}",
        f"#!system_version = {meta['system_version']}",
        f"#!loon_version = {meta['loon_version']}",
        f"#!tag = {meta['tag']},{policy}",
        "#!type = normal",
        "",
        "[Rule]",
        f"# generated_at: {report['generated_at']}",
        f"# policy: {policy}",
        f"# sources_ok: {sources_ok}/{len(report['sources'])}",
        f"# media_roots: {len(report['media_roots'])}",
        "# iOS 不能按 App 进程分流；播放 CDN 可能不在源 JSON 内。",
        "",
    ]
    lines.extend(body)
    lines.append("")
    return "\n".join(lines) + "\n"


def render_list(report: dict, cfg: dict) -> str:
    body = build_rule_body(report, cfg, with_policy=False, policy_token="")
    header = [
        "# Animeko Loon rule list (no policy — choose policy when subscribing)",
        f"# generated_at: {report['generated_at']}",
        f"# homepage: {cfg['plugin']['homepage']}",
        f"# media_roots: {len(report['media_roots'])}",
        "",
    ]
    return "\n".join(header + body) + "\n"


def render_ua_experimental(cfg: dict, policy: str = "DIRECT") -> str:
    lines = [
        f"# Experimental USER-AGENT rules for Animeko ({policy})",
        "# Confirm real User-Agent in Loon → 最新请求 before enabling.",
        "# These match ALL hosts for that UA (broad). Prefer AND with DOMAIN-SUFFIX for CDN.",
        "",
    ]
    for ua in cfg.get("user_agents", []):
        pat = ua.get("pattern")
        if not pat:
            continue
        comment = ua.get("comment") or ""
        lines.append(f"# {comment}")
        lines.append(f"USER-AGENT,{pat},{policy}")
    lines.append("")
    return "\n".join(lines)


def write_manifest(report: dict, cfg: dict, out_dir: Path) -> None:
    manifest = {
        "generated_at": report["generated_at"],
        "sources": report["sources"],
        "core_domains": report["core_domains"],
        "media_roots": report["media_roots"],
        "raw_host_count": len(report["raw_hosts"]),
        "media_root_count": len(report["media_roots"]),
        "files": [
            "Animeko-DIRECT.plugin",
            "Animeko-PROXY.plugin",
            "Animeko-REJECT.plugin",
            "Animeko.list",
            "Animeko-UA-experimental.list",
            "report.json",
        ],
        "install": {
            "plugin_direct": "https://raw.githubusercontent.com/beckyeeky/animeko-loon/main/dist/Animeko-DIRECT.plugin",
            "rule_list": "https://raw.githubusercontent.com/beckyeeky/animeko-loon/main/dist/Animeko.list",
        },
    }
    (out_dir / "report.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "hosts-raw.txt").write_text(
        "\n".join(report["raw_hosts"]) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=None, help="optional dir to store fetched JSON")
    parser.add_argument(
        "--offline",
        type=Path,
        default=None,
        help="read source JSON files from a directory instead of network (named by source id)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.offline:
        # offline mode: patch fetch via local files
        report = collect_offline(cfg, args.offline)
    else:
        report = collect_from_sources(cfg, cache_dir=args.cache)

    for policy in ("DIRECT", "PROXY", "REJECT"):
        text = render_plugin_with_policy(cfg, report, policy)
        (args.out / f"Animeko-{policy}.plugin").write_text(text, encoding="utf-8")

    (args.out / "Animeko.list").write_text(render_list(report, cfg), encoding="utf-8")
    (args.out / "Animeko-UA-experimental.list").write_text(
        render_ua_experimental(cfg, "DIRECT"),
        encoding="utf-8",
    )
    write_manifest(report, cfg, args.out)

    ok = sum(1 for s in report["sources"] if s["ok"])
    total = len(report["sources"])
    print(f"generated_at={report['generated_at']}")
    print(f"sources_ok={ok}/{total}")
    print(f"media_roots={len(report['media_roots'])}")
    print(f"out={args.out}")
    if ok == 0 and total > 0:
        print("all sources failed", file=sys.stderr)
        return 2
    return 0


def collect_offline(cfg: dict, offline_dir: Path) -> dict:
    """Build report from local JSON files named {id}.json."""
    exclude_hosts = {h.lower() for h in cfg.get("exclude_hosts", [])}
    exclude_suffixes = {s.lower() for s in cfg.get("exclude_suffixes", [])}
    sources_meta = []
    all_hosts: set[str] = set()

    for src in cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        sid = src.get("id") or "unknown"
        path = offline_dir / f"{sid}.json"
        item = {
            "id": sid,
            "name": src.get("name") or sid,
            "url": src.get("url"),
            "ok": False,
            "error": None,
            "bytes": 0,
            "media_sources": None,
            "hosts": [],
        }
        if not path.exists():
            item["error"] = f"missing {path}"
            sources_meta.append(item)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        item["bytes"] = len(text.encode("utf-8"))
        try:
            payload = json.loads(text)
            ms = payload.get("exportedMediaSourceDataList", {}).get("mediaSources")
            if isinstance(ms, list):
                item["media_sources"] = len(ms)
        except json.JSONDecodeError:
            pass
        hosts = extract_hosts(text)
        item["hosts"] = sorted(hosts)
        all_hosts |= hosts
        item["ok"] = True
        sources_meta.append(item)

    media_roots: set[str] = set()
    for host in sorted(all_hosts):
        if excluded(host, media_roots, exclude_hosts, exclude_suffixes):
            continue
        media_roots.add(registrable_domain(host))
    core = {d.lower() for d in cfg.get("core_domains", [])}
    media_roots = {r for r in media_roots if r not in core and r not in exclude_suffixes}
    return {
        "sources": sources_meta,
        "raw_hosts": sorted(all_hosts),
        "media_roots": sorted(media_roots),
        "core_domains": sorted(core),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
