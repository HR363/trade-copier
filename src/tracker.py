"""
Position change tracker.
Compares the current snapshot of master positions against the previous one
and emits CopySignals for any differences (new trades, closed trades, SL/TP changes).
"""

from typing import Dict, List, Tuple
from src.models import Position, CopySignal, SignalType, OrderType


class PositionTracker:
    """
    Maintains the last known state of master positions and detects changes.
    """

    def __init__(self):
        # ticket → Position
        self._previous: Dict[int, Position] = {}

    def compute_signals(
        self,
        current_positions: List[Position],
        copy_sl: bool = True,
        copy_tp: bool = True,
    ) -> List[CopySignal]:
        """
        Compare current positions against the previous snapshot.
        Returns a list of signals describing what changed.
        """
        current_map: Dict[int, Position] = {p.ticket: p for p in current_positions}
        signals: List[CopySignal] = []

        # --- Detect NEW positions (opened since last poll) ---
        for ticket, pos in current_map.items():
            if ticket not in self._previous:
                signals.append(CopySignal(
                    signal_type=SignalType.OPEN,
                    master_ticket=ticket,
                    symbol=pos.symbol,
                    order_type=pos.order_type,
                    volume=pos.volume,
                    open_price=pos.open_price,
                    stop_loss=pos.stop_loss if copy_sl else 0.0,
                    take_profit=pos.take_profit if copy_tp else 0.0,
                    comment=pos.comment,
                ))

        # --- Detect CLOSED positions (gone since last poll) ---
        for ticket, pos in self._previous.items():
            if ticket not in current_map:
                signals.append(CopySignal(
                    signal_type=SignalType.CLOSE,
                    master_ticket=ticket,
                    symbol=pos.symbol,
                ))

        # --- Detect MODIFIED positions (SL or TP changed) ---
        for ticket, pos in current_map.items():
            if ticket in self._previous:
                prev = self._previous[ticket]
                sl_changed = copy_sl and abs(pos.stop_loss - prev.stop_loss) > 0.000001
                tp_changed = copy_tp and abs(pos.take_profit - prev.take_profit) > 0.000001

                if sl_changed or tp_changed:
                    signals.append(CopySignal(
                        signal_type=SignalType.MODIFY,
                        master_ticket=ticket,
                        symbol=pos.symbol,
                        stop_loss=pos.stop_loss,
                        take_profit=pos.take_profit,
                    ))

        # --- Update the snapshot ---
        self._previous = current_map

        return signals

    def reset(self):
        """Clear the snapshot (useful on reconnect)."""
        self._previous = {}

    @property
    def known_tickets(self) -> List[int]:
        return list(self._previous.keys())

    @property
    def position_count(self) -> int:
        return len(self._previous)
