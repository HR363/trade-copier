"""
Data models for the Trade Copier.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class OrderType(Enum):
    BUY = 0
    SELL = 1
    BUY_LIMIT = 2
    SELL_LIMIT = 3
    BUY_STOP = 4
    SELL_STOP = 5
    BUY_STOP_LIMIT = 6
    SELL_STOP_LIMIT = 7

    @property
    def is_market(self) -> bool:
        return self in (OrderType.BUY, OrderType.SELL)

    @property
    def is_buy(self) -> bool:
        return self in (
            OrderType.BUY, OrderType.BUY_LIMIT,
            OrderType.BUY_STOP, OrderType.BUY_STOP_LIMIT
        )


class LotMode(Enum):
    MULTIPLIER = "multiplier"   # slave lot = master lot * lot_value
    FIXED = "fixed"             # slave lot = lot_value (always)


class SignalType(Enum):
    OPEN = "open"
    CLOSE = "close"
    MODIFY = "modify"


class CopyStatus(Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Position:
    """Snapshot of an open market position."""
    ticket: int
    symbol: str
    order_type: OrderType      # BUY or SELL only for positions
    volume: float              # lot size
    open_price: float
    stop_loss: float
    take_profit: float
    comment: str
    magic: int
    profit: float
    swap: float
    open_time: int             # unix timestamp

    def __eq__(self, other: "Position") -> bool:
        """Two positions are equal if all tradeable fields match."""
        if not isinstance(other, Position):
            return False
        return (
            self.ticket == other.ticket
            and self.volume == other.volume
            and abs(self.stop_loss - other.stop_loss) < 0.000001
            and abs(self.take_profit - other.take_profit) < 0.000001
        )

    def has_sl_tp_changed(self, other: "Position") -> bool:
        return (
            abs(self.stop_loss - other.stop_loss) > 0.000001
            or abs(self.take_profit - other.take_profit) > 0.000001
        )


@dataclass
class CopySignal:
    """Instruction to copy a trade action to a slave account."""
    signal_type: SignalType
    master_ticket: int
    symbol: str
    order_type: Optional[OrderType] = None
    volume: float = 0.0
    open_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    comment: str = ""

    @property
    def slave_comment(self) -> str:
        """Comment embedded in slave orders to track origin."""
        return f"MC-{self.master_ticket}"


@dataclass
class AccountConfig:
    """Configuration for a single MT5 account."""
    name: str
    login: int
    password: str
    server: str
    terminal_path: str
    enabled: bool = True
    lot_mode: LotMode = LotMode.MULTIPLIER
    lot_value: float = 1.0
    comment: str = ""

    def calculate_lot(self, master_lot: float) -> float:
        """Calculate the slave lot size based on the master lot."""
        if self.lot_mode == LotMode.FIXED:
            return round(self.lot_value, 2)
        else:  # MULTIPLIER
            return round(master_lot * self.lot_value, 2)


@dataclass
class CopyResult:
    """Result of a copy operation on one slave account."""
    slave_name: str
    slave_login: int
    signal: CopySignal
    status: CopyStatus
    slave_ticket: Optional[int] = None
    error_message: str = ""
