"""python -m bridge [serve|check|release-kill|set-passphrase|create-bot-account]

serve               run the bridge on 127.0.0.1 (default)
check               read-only connection test: login, account info, instrument list. No orders.
release-kill        release the kill switch (only possible on this machine)
set-passphrase      set or replace the unlock passphrase (hash only is stored in bridge/.env)
create-bot-account  generate a dedicated bot keypair; secret stays in bridge/.env; prints the public G address only
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
    if cmd == "set-passphrase":
        return _set_passphrase(s)
    if cmd == "create-bot-account":
        allow = ""
        if "--allowlist" in argv:
            i = argv.index("--allowlist")
            allow = argv[i + 1] if i + 1 < len(argv) else ""
        return _create_bot_account(s, allow)
    if cmd != "serve":
        print(__doc__); return 2
    import uvicorn
    from bridge.server import create_app
    print(f"Alpha Hive bridge · broker={s.broker} · backend={s.env if s.broker != 'coinbase' else 'preview'} · "
          f"http://{s.host}:{s.port} · credentials "
          f"{'set' if s.has_credentials else 'NOT set (fill bridge/.env)'}")
    if s.token_generated:
        print(f"No BRIDGE_TOKEN in bridge/.env; using a one-time token for this run: {s.bridge_token}")
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="warning")
    return 0



def _set_passphrase(settings) -> int:
    import getpass
    from bridge.envfile import upsert_env
    from bridge.session import MIN_PASSPHRASE_LEN, hash_passphrase
    path = settings.dotenv_path
    if path is None:
        print("bridge: no .env path configured", file=sys.stderr)
        return 2
    try:
        a = getpass.getpass("New unlock passphrase: ")
        b = getpass.getpass("Confirm passphrase: ")
    except (EOFError, KeyboardInterrupt):
        print("bridge: cancelled", file=sys.stderr)
        return 2
    if a != b:
        print("bridge: passphrases did not match", file=sys.stderr)
        return 2
    try:
        hashed = hash_passphrase(a)
    except ValueError:
        print(f"bridge: passphrase must be at least {MIN_PASSPHRASE_LEN} characters", file=sys.stderr)
        return 2
    upsert_env(path, "ALPHA_HIVE_PASSPHRASE_HASH", hashed)
    print("Unlock passphrase saved (hash only). The passphrase itself was not written or printed.")
    return 0


def _create_bot_account(settings, allowlist: str = "") -> int:
    from stellar_sdk import Keypair
    from bridge.envfile import upsert_env
    from bridge.stellar_dex import is_valid_address
    path = settings.dotenv_path
    if path is None:
        print("bridge: no .env path configured", file=sys.stderr)
        return 2
    if allowlist and not is_valid_address(allowlist):
        print("bridge: --allowlist must be a public G... address", file=sys.stderr)
        return 2
    kp = Keypair.random()
    upsert_env(path, "STELLAR_BOT_PUBLIC_KEY", kp.public_key)
    upsert_env(path, "STELLAR_BOT_SECRET", kp.secret)
    if allowlist:
        upsert_env(path, "STELLAR_WITHDRAW_ALLOWLIST", allowlist.strip())
    print(f"Bot account public key: {kp.public_key}")
    print("Secret was written only to bridge/.env (git-ignored). Back that file up offline or bot funds are lost.")
    return 0



if __name__ == "__main__":
    sys.exit(main(sys.argv))
