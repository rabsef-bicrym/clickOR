from __future__ import annotations

import argparse
import os
import secrets
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Optional

from .config import ConfigError, load_config, parse_seed
from .env import EnvError, load_dotenv, read_env
from .ersatztv_db import (
    BuilderError,
    check_existing,
    dump_builder_report,
    generate_create_sql,
    generate_update_sql,
    load_yaml as load_lineup_yaml,
    resolve_media_ids,
    reset_playout,
)
from .export_from_db import ExportError, export_config_from_spec
from .flat import FlatError, build_lineup_config_for_db, expand_flat_to_playlist_entries, load_flat_config
from .generate import GenerateError, solve_to_yaml_obj
from .probe_dir import ProbeError, probe_dir_over_ssh, write_probe_json
from .remote_sqlite import RemoteSqliteError, parse_ssh_prefix
from .verify import VerifyError, verify_yaml_against_config
from .yaml_out import dump_yaml


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _auto_seed() -> int:
    # 31-bit positive integer for CP-SAT.
    return int(secrets.randbits(31))


def _parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def cmd_solve(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        _eprint(f"CONFIG ERROR: {e}")
        return 2

    # Resolve "auto seed" convention.
    seed_value: Optional[str | int] = None
    if args.seed is not None:
        if args.seed.strip().lower() == "auto":
            seed_value = _auto_seed()
        else:
            seed_value = args.seed
    else:
        if cfg.solver.seed == 0:
            seed_value = _auto_seed()

    if seed_value is not None:
        cfg = replace(cfg, solver=replace(cfg.solver, seed=parse_seed(seed_value, "cli.seed")))

    playlist_name = args.playlist_name or f"{cfg.channel['name']} Playlist"
    playlist_group = args.playlist_group or cfg.channel.get("group", cfg.channel["name"])

    try:
        yaml_obj, result = solve_to_yaml_obj(
            cfg,
            playlist_name=playlist_name,
            playlist_group=playlist_group,
            time_limit_sec=args.time_limit_sec,
            block_minutes=_parse_optional_float(args.block_minutes),
            allow_short_overflow_minutes=_parse_optional_float(args.allow_short_overflow_minutes),
            longform_consumes_block=None if args.longform_consumes_block is None else bool(args.longform_consumes_block),
        )
    except (GenerateError, Exception) as e:
        _eprint(f"SOLVE ERROR: {e}")
        return 1

    dump_yaml(yaml_obj, args.out)
    _eprint(f"Wrote lineup YAML: {args.out}")

    # Optional report file.
    if args.report:
        report_obj: dict[str, Any] = {
            "config": {"path": str(args.config)},
            "solver": asdict(cfg.solver),
            "solve_result": {
                "seed": result.seed,
                "blocks": [
                    {
                        "index": b.index,
                        "is_long": b.is_long,
                        "base_items_count": b.base_items_count,
                        "repeat_items_count": b.repeat_items_count,
                        "content_duration_s": b.content_duration_s,
                        "waste_s": b.waste_s,
                        "items": [it.path for it in b.items],
                    }
                    for b in result.blocks
                ],
                "repeats_used": result.repeats_used,
                "total_waste_s": result.total_waste_s,
            },
        }
        Path(args.report).write_text(__import__("json").dumps(report_obj, indent=2))
        _eprint(f"Wrote report JSON: {args.report}")

    if not args.no_verify:
        findings = verify_yaml_against_config(cfg, args.out)
        errors = [f for f in findings if f.level == "ERROR"]
        if errors:
            _eprint("VERIFY FAILED:")
            for f in findings:
                _eprint(f"  {f.level}: {f.message}")
            return 1
        _eprint("Verify: OK")

    _eprint(
        f"Solve summary: blocks={len(result.blocks)} repeats_used={result.repeats_used} "
        f"total_waste_min={result.total_waste_s/60.0:.1f} seed={result.seed}"
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        _eprint(f"CONFIG ERROR: {e}")
        return 2

    try:
        findings = verify_yaml_against_config(cfg, args.yaml)
    except VerifyError as e:
        _eprint(f"VERIFY ERROR: {e}")
        return 2

    errors = [f for f in findings if f.level == "ERROR"]
    if not findings:
        _eprint("Verify: OK (no findings)")
        return 0

    for f in findings:
        _eprint(f"{f.level}: {f.message}")
    return 1 if errors else 0


def cmd_apply(args: argparse.Namespace) -> int:
    env = read_env()

    db_path = args.db or env.db_path or "/mnt/media/config/ersatztv.sqlite3"
    db_path = os.path.expanduser(db_path)

    ssh_prefix = args.ssh or env.ssh_prefix
    if ssh_prefix:
        try:
            ssh = parse_ssh_prefix(ssh_prefix)
        except RemoteSqliteError as e:
            _eprint(f"SSH ERROR: {e}")
            return 2
    else:
        ssh = None

    sudo = bool(args.ssh_sudo) if args.ssh_sudo is not None else env.ssh_sudo

    cfg = load_lineup_yaml(args.yaml)
    if "playlist" not in cfg or "items" not in cfg["playlist"]:
        _eprint("YAML ERROR: missing playlist.items")
        return 2
    items = cfg["playlist"]["items"]
    if not isinstance(items, list):
        _eprint("YAML ERROR: playlist.items must be a list")
        return 2

    # Basic schema checks.
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            _eprint(f"YAML ERROR: playlist.items[{idx}] must be an object")
            return 2
        if "path" not in it:
            _eprint(f"YAML ERROR: playlist.items[{idx}] missing path")
            return 2
        ty = it.get("type")
        if ty not in ("episode", "movie", "music_video", "other_video"):
            _eprint(
                f"YAML ERROR: playlist.items[{idx}] has unknown type {ty!r} "
                f"(allowed: {sorted(['episode','movie','music_video','other_video'])})"
            )
            return 2

    _eprint(f"Channel: {cfg['channel']['name']} (#{cfg['channel']['number']})")
    _eprint(f"Playlist: {cfg['playlist']['name']} ({len(items)} items)")

    try:
        existing = check_existing(cfg, db_path=db_path, ssh=ssh, sudo=sudo)
    except RemoteSqliteError as e:
        _eprint(f"DB ERROR: {e}")
        _eprint("Hint: if your ErsatzTV sqlite DB is remote, set CLICKOR_SSH or pass --ssh.")
        _eprint("Hint: if running locally, pass --db (or set CLICKOR_DB_PATH) to a real local sqlite path.")
        return 2

    is_update = existing.channel_id is not None and existing.playlist_id is not None
    if is_update:
        _eprint(f"Mode: UPDATE (ChannelId={existing.channel_id}, PlaylistId={existing.playlist_id})")
    else:
        _eprint("Mode: CREATE")

    _eprint("Resolving MediaItemIds by path...")
    try:
        resolved, errors = resolve_media_ids(items, db_path=db_path, ssh=ssh, sudo=sudo)
    except RemoteSqliteError as e:
        _eprint(f"DB ERROR: {e}")
        return 2

    if errors:
        _eprint(f"Resolution errors: {len(errors)}")
        for e in errors[:50]:
            _eprint(f"  {e}")
        if len(errors) > int(args.allow_missing):
            _eprint(f"ABORT: unresolved paths ({len(errors)}) exceeds --allow-missing ({args.allow_missing}).")
            return 1
        _eprint(f"Proceeding with {len(resolved)}/{len(items)} resolved items due to --allow-missing.")

    mode = args.mode
    try:
        sql = (
            generate_update_sql(cfg, resolved, existing, mode=mode)
            if is_update
            else generate_create_sql(cfg, resolved)
        )
    except BuilderError as e:
        _eprint(f"BUILDER ERROR: {e}")
        return 2

    if args.output:
        Path(args.output).write_text(sql)
        _eprint(f"SQL written to: {args.output}")

    if args.report:
        dump_builder_report(
            yaml_path=str(args.yaml),
            existing=existing,
            resolved_count=len(resolved),
            total_items=len(items),
            mode=mode,
            out_path=str(args.report),
        )
        _eprint(f"Report written to: {args.report}")

    if args.dry_run:
        print(sql)

    if args.apply:
        _eprint("Applying SQL...")
        try:
            from .remote_sqlite import run_sqlite as run_sqlite2

            run_sqlite2(sql=sql, db_path=db_path, ssh=ssh, sudo=sudo)
        except (BuilderError, RemoteSqliteError) as e:
            _eprint(f"APPLY FAILED: {e}")
            return 2
        _eprint("Apply succeeded.")

        do_reset = env.reset_after_apply if args.reset is None else bool(args.reset)
        base_url = args.base_url or env.base_url
        if do_reset:
            if not base_url:
                _eprint("Reset requested but no base URL configured. Set CLICKOR_BASE_URL or pass --base-url.")
                return 2
            _eprint("Resetting playout...")
            try:
                reset_playout(base_url=base_url, channel_number=cfg["channel"]["number"])
            except BuilderError as e:
                _eprint(f"Reset failed: {e}")
                return 2
            _eprint("Done (playout reset request sent).")

    return 0


def cmd_probe_dir(args: argparse.Namespace) -> int:
    env = read_env()
    ssh_prefix = args.ssh or env.ssh_prefix
    if not ssh_prefix:
        _eprint("PROBE ERROR: missing --ssh (or set CLICKOR_SSH in .env)")
        return 2
    try:
        items = probe_dir_over_ssh(
            ssh_prefix=ssh_prefix,
            remote_dir=str(args.dir),
            rewrite_prefix=args.rewrite_prefix,
            media_type=str(args.type),
            exts=list(args.ext),
        )
    except ProbeError as e:
        _eprint(f"PROBE ERROR: {e}")
        return 2
    write_probe_json(items=items, out_path=str(args.out))
    _eprint(f"Wrote {len(items)} items to {args.out}")
    if not items:
        _eprint("WARNING: no items found; check --dir and extensions")
    return 0


def cmd_flat(args: argparse.Namespace) -> int:
    env = read_env()

    db_path = args.db or env.db_path or "/mnt/media/config/ersatztv.sqlite3"
    db_path = os.path.expanduser(db_path)

    ssh_prefix = args.ssh or env.ssh_prefix
    if ssh_prefix:
        try:
            ssh = parse_ssh_prefix(ssh_prefix)
        except RemoteSqliteError as e:
            _eprint(f"SSH ERROR: {e}")
            return 2
    else:
        ssh = None

    sudo = bool(args.ssh_sudo) if args.ssh_sudo is not None else env.ssh_sudo

    try:
        flat_cfg = load_flat_config(args.config)
    except FlatError as e:
        _eprint(f"FLAT CONFIG ERROR: {e}")
        return 2

    try:
        entries = expand_flat_to_playlist_entries(flat_cfg)
    except FlatError as e:
        _eprint(f"FLAT ERROR: {e}")
        return 2

    playlist_items = [
        {
            "path": e.path,
            "type": e.media_type,
            "include_in_guide": bool(e.include_in_guide),
        }
        for e in entries
    ]
    lineup_cfg = build_lineup_config_for_db(flat_cfg, items=playlist_items)

    _eprint(f"Channel: {flat_cfg.channel_name}")
    _eprint(f"Playlist: {flat_cfg.playlist_name} ({len(playlist_items)} items after loop expansion)")

    try:
        existing = check_existing(lineup_cfg, db_path=db_path, ssh=ssh, sudo=sudo)
    except RemoteSqliteError as e:
        _eprint(f"DB ERROR: {e}")
        return 2

    is_update = existing.playlist_id is not None
    if is_update:
        _eprint(f"Mode: UPDATE (PlaylistId={existing.playlist_id})")
    else:
        if flat_cfg.channel_number is None:
            _eprint("ABORT: playlist does not exist, and channel_number is not set (required for CREATE).")
            _eprint("Hint: add channel_number to the flat config, or create the playlist/channel first.")
            return 2
        _eprint("Mode: CREATE")

    _eprint("Resolving MediaItemIds by path...")
    try:
        resolved, errors = resolve_media_ids(playlist_items, db_path=db_path, ssh=ssh, sudo=sudo)
    except RemoteSqliteError as e:
        _eprint(f"DB ERROR: {e}")
        return 2

    if errors:
        _eprint(f"Resolution errors: {len(errors)}")
        for e in errors[:50]:
            _eprint(f"  {e}")
        if len(errors) > int(args.allow_missing):
            _eprint(f"ABORT: unresolved paths ({len(errors)}) exceeds --allow-missing ({args.allow_missing}).")
            return 1
        _eprint(f"Proceeding with {len(resolved)}/{len(playlist_items)} resolved items due to --allow-missing.")

    try:
        sql = (
            generate_update_sql(lineup_cfg, resolved, existing, mode=str(args.mode))
            if is_update
            else generate_create_sql(lineup_cfg, resolved)
        )
    except BuilderError as e:
        _eprint(f"BUILDER ERROR: {e}")
        return 2

    if args.output:
        Path(args.output).write_text(sql)
        _eprint(f"SQL written to: {args.output}")

    # Flat mode always outputs SQL to stdout so users can redirect it.
    print(sql, end="")

    if args.apply:
        _eprint("Applying SQL...")
        try:
            from .remote_sqlite import run_sqlite as run_sqlite2

            run_sqlite2(sql=sql, db_path=db_path, ssh=ssh, sudo=sudo)
        except (BuilderError, RemoteSqliteError) as e:
            _eprint(f"APPLY FAILED: {e}")
            return 2
        _eprint("Apply succeeded.")

    return 0


def cmd_export_from_db(args: argparse.Namespace) -> int:
    env = read_env()
    db_path = args.db or env.db_path or "/mnt/media/config/ersatztv.sqlite3"
    db_path = os.path.expanduser(db_path)
    ssh_prefix = args.ssh or env.ssh_prefix
    sudo = bool(args.ssh_sudo) if args.ssh_sudo is not None else env.ssh_sudo
    try:
        export_config_from_spec(
            spec_path=str(args.spec),
            out_path=str(args.out),
            db_path=db_path,
            ssh_prefix=ssh_prefix,
            sudo=sudo,
        )
    except (ExportError, RemoteSqliteError) as e:
        _eprint(f"EXPORT ERROR: {e}")
        return 2
    _eprint(f"Wrote config JSON: {args.out}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    ap = argparse.ArgumentParser(prog="clickor", description="clickOR: generate, verify, and apply ErsatzTV lineups")
    try:
        from importlib.metadata import version as _pkg_version  # type: ignore

        _ver = _pkg_version("clickor")
    except Exception:  # pragma: no cover
        _ver = "unknown"
    ap.add_argument("--version", action="version", version=f"clickor {_ver}")
    ap.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file to load (default: .env, ignored if missing)",
    )
    ap.add_argument("--no-env", action="store_true", help="Do not load .env")

    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_solve = sub.add_parser("solve", help="Solve a channel config JSON into an ErsatzTV lineup YAML")
    ap_solve.add_argument("--config", required=True, help="Path to channel config JSON")
    ap_solve.add_argument("--out", required=True, help="Output lineup YAML path")
    ap_solve.add_argument("--seed", help="Seed for deterministic output (int or string). Use 'auto' for random.")
    ap_solve.add_argument("--time-limit-sec", type=int, help="Override solver time limit per phase")
    ap_solve.add_argument("--block-minutes", help="Override solver block size (content minutes)")
    ap_solve.add_argument("--allow-short-overflow-minutes", help="Allow short blocks to exceed block-minutes by this much")
    ap_solve.add_argument(
        "--longform-consumes-block",
        type=int,
        choices=[0, 1],
        help="Override: 1 means items >= block consume a whole block (solo). 0 disables.",
    )
    ap_solve.add_argument("--playlist-name", help="Override playlist name in YAML")
    ap_solve.add_argument("--playlist-group", help="Override playlist group in YAML")
    ap_solve.add_argument("--report", help="Write a JSON report for debugging")
    ap_solve.add_argument("--no-verify", action="store_true", help="Do not auto-verify generated YAML")
    ap_solve.set_defaults(func=cmd_solve)

    ap_verify = sub.add_parser("verify", help="Verify a generated lineup YAML against the config JSON")
    ap_verify.add_argument("--config", required=True, help="Path to channel config JSON")
    ap_verify.add_argument("--yaml", required=True, help="Path to lineup YAML")
    ap_verify.set_defaults(func=cmd_verify)

    ap_apply = sub.add_parser("apply", help="Apply a lineup YAML to an ErsatzTV sqlite DB (create/update)")
    ap_apply.add_argument("--yaml", required=True, help="Path to lineup YAML")
    ap_apply.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    ap_apply.add_argument("--apply", action="store_true", help="Execute SQL against DB")
    ap_apply.add_argument("--ssh", help="SSH prefix for remote host (e.g. \"ssh -i key user@host\")")
    ap_apply.add_argument("--db", help="DB path (on remote if --ssh). Default from CLICKOR_DB_PATH or /mnt/media/config/ersatztv.sqlite3")
    ap_apply.add_argument("--ssh-sudo", type=int, choices=[0, 1], help="Whether to run sqlite3 under sudo when using --ssh")
    ap_apply.add_argument("--mode", choices=["replace", "append"], default="replace", help="UPDATE mode behavior")
    ap_apply.add_argument("--output", "-o", help="Write SQL to file")
    ap_apply.add_argument("--report", help="Write a JSON report for debugging")
    ap_apply.add_argument("--allow-missing", type=int, default=0, help="Allow up to N unresolved paths (default: 0)")
    ap_apply.add_argument("--base-url", help="ErsatzTV base URL for playout reset (e.g. http://nuc:8409)")
    ap_apply.add_argument("--reset", type=int, choices=[0, 1], help="Whether to reset playout after apply")
    ap_apply.set_defaults(func=cmd_apply)

    ap_probe = sub.add_parser("probe-dir", help="Probe a remote directory (ffprobe over SSH) and emit JSON items")
    ap_probe.add_argument("--ssh", help="SSH prefix (default from CLICKOR_SSH)")
    ap_probe.add_argument("--dir", required=True, help="Remote directory to scan (SSH-visible path)")
    ap_probe.add_argument("--rewrite-prefix", help="Optional prefix rewrite FROM=TO (example: /mnt/media=/media)")
    ap_probe.add_argument("--type", required=True, help="Media type to emit (episode|movie|music_video|other_video)")
    ap_probe.add_argument("--out", required=True, help="Output JSON path")
    ap_probe.add_argument(
        "--ext",
        action="append",
        default=["mkv", "mp4", "avi", "mpg", "ogv"],
        help="File extension to include (repeatable). Default: mkv, mp4, avi, mpg, ogv",
    )
    ap_probe.set_defaults(func=cmd_probe_dir)

    ap_export = sub.add_parser("export-from-db", help="Export a full solve config JSON from an ErsatzTV sqlite DB")
    ap_export.add_argument("--spec", required=True, help="Export spec JSON describing pool prefixes and knobs")
    ap_export.add_argument("--out", required=True, help="Output config JSON path")
    ap_export.add_argument("--ssh", help="SSH prefix for remote host (default from CLICKOR_SSH)")
    ap_export.add_argument("--db", help="DB path (default from CLICKOR_DB_PATH)")
    ap_export.add_argument("--ssh-sudo", type=int, choices=[0, 1], help="Whether to run sqlite3 under sudo when using --ssh")
    ap_export.set_defaults(func=cmd_export_from_db)

    ap_flat = sub.add_parser("flat", help="Build an ErsatzTV playlist in exact order (no solver) and output SQL")
    ap_flat.add_argument("config", help="Path to flat config JSON")
    ap_flat.add_argument("--ssh", help="SSH prefix for remote host (e.g. \"ssh -i key user@host\")")
    ap_flat.add_argument("--db", help="DB path (on remote if --ssh). Default from CLICKOR_DB_PATH or /mnt/media/config/ersatztv.sqlite3")
    ap_flat.add_argument("--ssh-sudo", type=int, choices=[0, 1], help="Whether to run sqlite3 under sudo when using --ssh")
    ap_flat.add_argument("--mode", choices=["replace", "append"], default="replace", help="UPDATE mode behavior")
    ap_flat.add_argument("--output", "-o", help="Write SQL to file (SQL is always printed to stdout)")
    ap_flat.add_argument("--apply", action="store_true", help="Execute SQL against DB after printing it")
    ap_flat.add_argument("--allow-missing", type=int, default=0, help="Allow up to N unresolved paths (default: 0)")
    ap_flat.set_defaults(func=cmd_flat)

    args = ap.parse_args(argv)

    if not args.no_env:
        try:
            load_dotenv(args.env_file, override=False)
        except EnvError as e:
            _eprint(f"ENV ERROR: {e}")
            return 2

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
