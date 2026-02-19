"""
MT5 Trade Copier — entry point.

Usage:
    python main.py                              # launch UI (default)
    python main.py --headless                   # run without UI (console only)
    python main.py --config path/to/config.json
    python main.py --check                      # validate config & test connections only
"""

import argparse
import sys
import os

# Ensure we can import from src/ regardless of where the script is called from
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.logger import setup_logger, get_logger, Colors
from src.config import load_config, ConfigError
from src.copier import TradeCopier

BANNER = f"""
{Colors.CYAN}{Colors.BOLD}
  ████████╗██████╗  █████╗ ██████╗ ███████╗     ██████╗ ██████╗ ██████╗ ██╗███████╗██████╗ 
  ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝    ██╔════╝██╔═══██╗██╔══██╗██║██╔════╝██╔══██╗
     ██║   ██████╔╝███████║██║  ██║█████╗      ██║     ██║   ██║██████╔╝██║█████╗  ██████╔╝
     ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝      ██║     ██║   ██║██╔═══╝ ██║██╔══╝  ██╔══██╗
     ██║   ██║  ██║██║  ██║██████╔╝███████╗    ╚██████╗╚██████╔╝██║     ██║███████╗██║  ██║
     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝    ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝
{Colors.RESET}{Colors.GREY}  MT5 Multi-Account Trade Copier  |  github.com/HR363/trade-copier{Colors.RESET}
"""


def main():
    parser = argparse.ArgumentParser(description="MT5 Trade Copier")
    parser.add_argument(
        "--config",
        default=os.path.join(ROOT, "config", "accounts.json"),
        help="Path to accounts.json config file",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the UI (console-only mode)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate config and test MT5 connections, then exit",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    args = parser.parse_args()

    # --- Load config first so we can read its log level ---
    try:
        master_config, slave_configs, settings = load_config(args.config)
    except ConfigError as e:
        # Logger not set up yet, use plain print
        print(f"\n{Colors.RED}[CONFIG ERROR]{Colors.RESET} {e}")
        print(
            f"\n{Colors.YELLOW}Tip:{Colors.RESET} Edit "
            f"{Colors.CYAN}config/accounts.json{Colors.RESET} "
            f"with your MT5 credentials.\n"
        )
        sys.exit(1)

    log_level = args.log_level or settings.get("log_level", "INFO")
    logger = setup_logger(level=log_level)

    print(BANNER)

    active_slaves = [s for s in slave_configs if s.enabled]
    disabled_slaves = [s for s in slave_configs if not s.enabled]

    logger.info(f"Config loaded: {args.config}")
    logger.info(
        f"Master : {Colors.CYAN}{master_config.name}{Colors.RESET} "
        f"(login {master_config.login} @ {master_config.server})"
    )
    for s in active_slaves:
        lot_desc = (
            f"fixed {s.lot_value} lot"
            if s.lot_mode.value == "fixed"
            else f"{s.lot_value}x multiplier"
        )
        logger.info(
            f"Slave  : {Colors.CYAN}{s.name}{Colors.RESET} "
            f"(login {s.login} @ {s.server})  [{lot_desc}]"
        )
    for s in disabled_slaves:
        logger.info(f"Slave  : {Colors.GREY}{s.name} (DISABLED){Colors.RESET}")

    logger.info(
        f"Settings: poll={settings['poll_interval_ms']}ms  "
        f"copy_sl={settings['copy_stop_loss']}  "
        f"copy_tp={settings['copy_take_profit']}  "
        f"pending={settings['copy_pending_orders']}"
    )

    # --- Connection check mode ---
    if args.check:
        _run_connection_check(master_config, active_slaves)
        return

    # --- Check MetaTrader5 package ---
    try:
        import MetaTrader5  # noqa: F401
    except ImportError:
        logger.error(
            "MetaTrader5 Python package is not installed.\n"
            "  Run:  pip install MetaTrader5\n"
            "  Note: MetaTrader5 requires Windows and a running MT5 terminal."
        )
        sys.exit(1)

    # --- Launch UI (default) or headless mode ---
    if args.headless:
        copier = TradeCopier(
            master_config=master_config,
            slave_configs=slave_configs,
            settings=settings,
        )
        try:
            copier.start()
        except Exception as e:
            logger.error(f"Fatal error in copier: {e}", exc_info=True)
            sys.exit(1)
    else:
        _launch_ui(master_config, slave_configs, settings, args.config)


def _launch_ui(master_config, slave_configs, settings, config_path):
    """Launch the CustomTkinter UI."""
    try:
        from src.ui_app import TradeCopierApp
    except ImportError as e:
        print(f"\n{Colors.RED}[UI ERROR]{Colors.RESET} Could not import UI: {e}")
        print(f"Run:  pip install customtkinter\n")
        sys.exit(1)

    app = TradeCopierApp(
        master_config=master_config,
        slave_configs=slave_configs,
        settings=settings,
        config_path=config_path,
    )
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


def _run_connection_check(master_config, slave_configs):
    """Test connections to all accounts and report status."""
    from src.mt5_connector import MT5Connector

    logger = get_logger()
    logger.info("─" * 50)
    logger.info("Running connection check…")

    all_ok = True

    for config in [master_config] + slave_configs:
        label = "MASTER" if config is master_config else "SLAVE "
        connector = MT5Connector(config)
        try:
            ok = connector.connect(timeout_ms=10_000)
            if ok:
                try:
                    import MetaTrader5 as mt5
                    info = mt5.account_info()
                    if info:
                        logger.info(
                            f"[{label}] {Colors.GREEN}OK{Colors.RESET}  "
                            f"{config.name} — balance {info.balance:.2f} {info.currency}"
                        )
                    else:
                        logger.warning(f"[{label}] Connected but no account info")
                except Exception:
                    logger.info(f"[{label}] {Colors.GREEN}OK{Colors.RESET}  {config.name}")
            else:
                logger.error(f"[{label}] {Colors.RED}FAILED{Colors.RESET}  {config.name}")
                all_ok = False
        finally:
            connector.shutdown()

    logger.info("─" * 50)
    if all_ok:
        logger.info(f"{Colors.GREEN}All connections successful.{Colors.RESET}")
    else:
        logger.error(
            f"{Colors.RED}Some connections failed.{Colors.RESET} "
            "Check your terminal_path, login, password and server in accounts.json."
        )


if __name__ == "__main__":
    main()
