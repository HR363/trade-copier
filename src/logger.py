"""
Colored console logger for the Trade Copier.
"""

import logging
import sys
from datetime import datetime


# ANSI color codes
class Colors:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    MAGENTA = "\033[95m"
    GREY    = "\033[90m"


LEVEL_COLORS = {
    "DEBUG":    Colors.GREY,
    "INFO":     Colors.WHITE,
    "WARNING":  Colors.YELLOW,
    "ERROR":    Colors.RED,
    "CRITICAL": Colors.RED + Colors.BOLD,
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = LEVEL_COLORS.get(record.levelname, Colors.WHITE)
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]

        # Color specific parts of the message
        level_tag = f"{color}[{record.levelname[:4]}]{Colors.RESET}"
        time_tag  = f"{Colors.GREY}{ts}{Colors.RESET}"
        msg       = record.getMessage()

        # Highlight keywords
        msg = msg.replace("OPENED",   f"{Colors.GREEN}OPENED{Colors.RESET}")
        msg = msg.replace("CLOSED",   f"{Colors.RED}CLOSED{Colors.RESET}")
        msg = msg.replace("MODIFIED", f"{Colors.CYAN}MODIFIED{Colors.RESET}")
        msg = msg.replace("SUCCESS",  f"{Colors.GREEN}SUCCESS{Colors.RESET}")
        msg = msg.replace("FAILED",   f"{Colors.RED}FAILED{Colors.RESET}")
        msg = msg.replace("SKIPPED",  f"{Colors.GREY}SKIPPED{Colors.RESET}")

        return f"{time_tag} {level_tag} {msg}"


def setup_logger(name: str = "trade_copier", level: str = "INFO") -> logging.Logger:
    """Create and return a configured logger with color output."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(ColorFormatter())
        logger.addHandler(handler)

    return logger


def get_logger(name: str = "trade_copier") -> logging.Logger:
    return logging.getLogger(name)
