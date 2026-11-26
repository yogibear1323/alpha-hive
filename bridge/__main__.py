"""python -m bridge [serve|check|release-kill]

serve         run the bridge on 127.0.0.1 (default)
check         read-only connection test: login, account info, instrument list. No orders.
release-kill  release the kill switch (only possible on this machine)
"""
import sys

from bridge.settings import SettingsError, load_settings


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "serve"
    try:
        s = load_settings()
    except SettingsError as e:
        print(f"bridge: {e}", file=sys.stderr); return 2
    if cmd == "release-kill":
        from bridge.guard import GuardState
        g = GuardState(s.state_dir, s.limits)
        print("kill switch released" if g.release_kill() else "kill switch was not engaged")
        if s.kill_switch_env:
            print("note: KILL_SWITCH=1 is still set in the environment / bridge/.env")
        return 0
    if cmd == "check":
        from bridge.check import run_check
        return run_check(s)
    if cmd != "serve":
        print(__doc__); return 2
    import uvicorn
    from bridge.server import create_app
    print(f"Alpha Hive bridge · backend={s.env} · http://{s.host}:{s.port} · credentials "
          f"{'set' if s.has_credentials else 'NOT set (fill bridge/.env)'}")
    if s.token_generated:
        print(f"No BRIDGE_TOKEN in bridge/.env; using a one-time token for this run: {s.bridge_token}")
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
