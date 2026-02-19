"""
MT5 Terminal connector.
Wraps the MetaTrader5 Python library to connect to a specific terminal,
read positions, and execute trade operations.
"""

import time
from typing import List, Optional, Dict

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from src.models import AccountConfig, Position, OrderType, CopySignal, SignalType
from src.logger import get_logger

logger = get_logger()

# Map MT5 position type integer → our OrderType
_MT5_POS_TYPE = {
    0: OrderType.BUY,
    1: OrderType.SELL,
}

# Filling modes to try in order (brokers vary)
_FILLING_MODES = [
    mt5.ORDER_FILLING_IOC if MT5_AVAILABLE else 1,
    mt5.ORDER_FILLING_FOK if MT5_AVAILABLE else 0,
    mt5.ORDER_FILLING_RETURN if MT5_AVAILABLE else 2,
]


class ConnectorError(Exception):
    pass


class MT5Connector:
    """
    Handles a single connection to one MT5 terminal.
    Call connect() before any operation, shutdown() after.
    """

    def __init__(self, config: AccountConfig):
        self.config = config
        self._connected = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, timeout_ms: int = 10_000) -> bool:
        """Initialize a connection to the MT5 terminal."""
        if not MT5_AVAILABLE:
            logger.error("MetaTrader5 Python package is not installed. Run: pip install MetaTrader5")
            return False

        ok = mt5.initialize(
            path=self.config.terminal_path,
            login=self.config.login,
            password=self.config.password,
            server=self.config.server,
            timeout=timeout_ms,
        )

        if not ok:
            err = mt5.last_error()
            logger.error(
                f"[{self.config.name}] Connection FAILED – code {err[0]}: {err[1]}"
            )
            return False

        info = mt5.account_info()
        if info is None:
            logger.error(f"[{self.config.name}] Connected but could not fetch account info.")
            mt5.shutdown()
            return False

        self._connected = True
        logger.debug(
            f"[{self.config.name}] Connected. "
            f"Balance: {info.balance:.2f} {info.currency}  "
            f"Leverage: 1:{info.leverage}"
        )
        return True

    def shutdown(self):
        """Close the connection to the current terminal."""
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
        self._connected = False

    def __enter__(self):
        if not self.connect():
            raise ConnectorError(f"Could not connect to [{self.config.name}]")
        return self

    def __exit__(self, *_):
        self.shutdown()

    # ------------------------------------------------------------------
    # Reading state
    # ------------------------------------------------------------------

    def get_positions(self) -> List[Position]:
        """Return all currently open market positions."""
        if not self._connected:
            return []

        raw_positions = mt5.positions_get()
        if raw_positions is None:
            return []

        result: List[Position] = []
        for p in raw_positions:
            order_type = _MT5_POS_TYPE.get(p.type)
            if order_type is None:
                continue  # unknown type, skip
            result.append(Position(
                ticket=p.ticket,
                symbol=p.symbol,
                order_type=order_type,
                volume=p.volume,
                open_price=p.price_open,
                stop_loss=p.sl,
                take_profit=p.tp,
                comment=p.comment,
                magic=p.magic,
                profit=p.profit,
                swap=p.swap,
                open_time=p.time,
            ))
        return result

    def get_copied_positions(self) -> Dict[int, Position]:
        """
        Return slave positions that were copied by this tool,
        keyed by their master ticket (extracted from comment 'MC-<ticket>').
        """
        result: Dict[int, Position] = {}
        for pos in self.get_positions():
            if pos.comment.startswith("MC-"):
                try:
                    master_ticket = int(pos.comment[3:])
                    result[master_ticket] = pos
                except ValueError:
                    pass
        return result

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------

    def open_position(
        self,
        signal: CopySignal,
        slave_lot: float,
        copy_sl: bool = True,
        copy_tp: bool = True,
        max_retries: int = 3,
        retry_delay_ms: int = 200,
    ) -> Optional[int]:
        """
        Open a market position on this account.
        Returns the ticket number on success, None on failure.
        """
        if not self._connected:
            return None

        symbol = signal.symbol

        # Ensure symbol is visible in Market Watch
        if not mt5.symbol_select(symbol, True):
            logger.warning(f"[{self.config.name}] Could not select symbol {symbol}")

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"[{self.config.name}] No tick data for {symbol}")
            return None

        is_buy = signal.order_type.is_buy
        price = tick.ask if is_buy else tick.bid
        order_type_mt5 = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

        sl = signal.stop_loss if copy_sl else 0.0
        tp = signal.take_profit if copy_tp else 0.0

        for attempt in range(max_retries):
            for filling in _FILLING_MODES:
                request = {
                    "action":        mt5.TRADE_ACTION_DEAL,
                    "symbol":        symbol,
                    "volume":        slave_lot,
                    "type":          order_type_mt5,
                    "price":         price,
                    "sl":            sl,
                    "tp":            tp,
                    "comment":       signal.slave_comment,
                    "type_time":     mt5.ORDER_TIME_GTC,
                    "type_filling":  filling,
                }

                result = mt5.order_send(request)

                if result is None:
                    continue

                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    return result.order

                # Refresh price and retry on stale price error
                if result.retcode == mt5.TRADE_RETCODE_REQUOTE:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        price = tick.ask if is_buy else tick.bid
                    continue

                # Invalid fill mode → try next filling mode
                if result.retcode in (
                    mt5.TRADE_RETCODE_INVALID_FILL,
                    mt5.TRADE_RETCODE_UNSUPPORTED,
                ):
                    break

                # Other error — log and retry after delay
                logger.warning(
                    f"[{self.config.name}] open_position attempt {attempt + 1} "
                    f"retcode={result.retcode} comment={result.comment}"
                )
                break  # break filling loop, retry attempt

            time.sleep(retry_delay_ms / 1000)

        logger.error(
            f"[{self.config.name}] Failed to open {symbol} after {max_retries} attempts"
        )
        return None

    def close_position(
        self,
        slave_position: Position,
        max_retries: int = 3,
        retry_delay_ms: int = 200,
    ) -> bool:
        """Close an existing position by ticket. Returns True on success."""
        if not self._connected:
            return False

        symbol = slave_position.symbol
        ticket = slave_position.ticket

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"[{self.config.name}] No tick data for {symbol} while closing")
            return False

        is_buy = slave_position.order_type == OrderType.BUY
        # To close a BUY we SELL, and vice versa
        close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask

        for attempt in range(max_retries):
            for filling in _FILLING_MODES:
                request = {
                    "action":        mt5.TRADE_ACTION_DEAL,
                    "position":      ticket,
                    "symbol":        symbol,
                    "volume":        slave_position.volume,
                    "type":          close_type,
                    "price":         price,
                    "comment":       "MC-close",
                    "type_time":     mt5.ORDER_TIME_GTC,
                    "type_filling":  filling,
                }

                result = mt5.order_send(request)

                if result is None:
                    continue

                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    return True

                if result.retcode == mt5.TRADE_RETCODE_REQUOTE:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        price = tick.bid if is_buy else tick.ask
                    continue

                if result.retcode in (
                    mt5.TRADE_RETCODE_INVALID_FILL,
                    mt5.TRADE_RETCODE_UNSUPPORTED,
                ):
                    break

                logger.warning(
                    f"[{self.config.name}] close_position attempt {attempt + 1} "
                    f"retcode={result.retcode} comment={result.comment}"
                )
                break

            time.sleep(retry_delay_ms / 1000)

        logger.error(f"[{self.config.name}] Failed to close ticket {ticket}")
        return False

    def modify_position(
        self,
        slave_ticket: int,
        stop_loss: float,
        take_profit: float,
    ) -> bool:
        """Modify the SL/TP of an existing position. Returns True on success."""
        if not self._connected:
            return False

        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": slave_ticket,
            "sl":       stop_loss,
            "tp":       take_profit,
        }

        result = mt5.order_send(request)

        if result is None:
            logger.error(f"[{self.config.name}] modify_position got None result")
            return False

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return True

        logger.warning(
            f"[{self.config.name}] modify_position retcode={result.retcode} "
            f"comment={result.comment}"
        )
        return False
