"""
Core Trade Copier engine.
Polls the master account for position changes and replicates them to all
enabled slave accounts.
"""

import time
from typing import List, Optional

from src.models import (
    AccountConfig, CopySignal, CopyResult, CopyStatus,
    SignalType,
)
from src.mt5_connector import MT5Connector, ConnectorError
from src.tracker import PositionTracker
from src.logger import get_logger

logger = get_logger()


class TradeCopier:
    """
    The main copy engine.

    Workflow per cycle:
      1. Connect to master  → get positions → disconnect
      2. Diff against previous snapshot → produce signals
      3. For each slave:
           connect → handle signals → disconnect
    """

    def __init__(
        self,
        master_config: AccountConfig,
        slave_configs: List[AccountConfig],
        settings: dict,
    ):
        self.master_config  = master_config
        self.slave_configs  = [s for s in slave_configs if s.enabled]
        self.settings       = settings

        self.poll_interval  = settings.get("poll_interval_ms", 500) / 1000
        self.copy_sl        = settings.get("copy_stop_loss", True)
        self.copy_tp        = settings.get("copy_take_profit", True)
        self.max_retries    = settings.get("max_retries", 3)
        self.retry_delay_ms = settings.get("retry_delay_ms", 200)

        self._tracker       = PositionTracker()
        self._running       = False

        # Stats
        self.total_opens    = 0
        self.total_closes   = 0
        self.total_modifies = 0
        self.total_errors   = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the polling loop (blocking). Press Ctrl+C to stop."""
        self._running = True
        logger.info(
            f"Trade Copier started. "
            f"Master: {self.master_config.name}  |  "
            f"Slaves: {', '.join(s.name for s in self.slave_configs)}  |  "
            f"Poll: {self.poll_interval * 1000:.0f}ms"
        )
        self._print_separator()

        try:
            while self._running:
                cycle_start = time.monotonic()
                self._run_cycle()
                elapsed = time.monotonic() - cycle_start
                sleep_time = max(0.0, self.poll_interval - elapsed)
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            logger.info("Shutting down Trade Copier…")
        finally:
            self._running = False
            self._print_summary()

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Internal cycle
    # ------------------------------------------------------------------

    def _run_cycle(self):
        """One full poll+copy cycle."""
        # Step 1: Poll master
        master_positions = self._poll_master()
        if master_positions is None:
            return  # connection error, skip this cycle

        # Step 2: Compute what changed
        signals = self._tracker.compute_signals(
            master_positions,
            copy_sl=self.copy_sl,
            copy_tp=self.copy_tp,
        )

        if not signals:
            return  # nothing to do

        # Step 3: Propagate to each slave
        for signal in signals:
            self._log_signal(signal)
            for slave_config in self.slave_configs:
                result = self._apply_signal_to_slave(signal, slave_config)
                self._log_result(result)

    # ------------------------------------------------------------------
    # Master polling
    # ------------------------------------------------------------------

    def _poll_master(self):
        """Connect to master, grab positions, disconnect. Returns None on error."""
        connector = MT5Connector(self.master_config)
        try:
            if not connector.connect():
                logger.error(f"Could not connect to master '{self.master_config.name}'")
                return None
            positions = connector.get_positions()
            return positions
        except Exception as e:
            logger.error(f"Unexpected error reading master: {e}")
            return None
        finally:
            connector.shutdown()

    # ------------------------------------------------------------------
    # Signal application
    # ------------------------------------------------------------------

    def _apply_signal_to_slave(
        self,
        signal: CopySignal,
        slave_config: AccountConfig,
    ) -> CopyResult:
        """Connect to a slave account and execute the signal."""
        connector = MT5Connector(slave_config)
        try:
            if not connector.connect():
                return CopyResult(
                    slave_name=slave_config.name,
                    slave_login=slave_config.login,
                    signal=signal,
                    status=CopyStatus.FAILED,
                    error_message="Could not connect to slave terminal",
                )

            if signal.signal_type == SignalType.OPEN:
                return self._handle_open(signal, slave_config, connector)

            elif signal.signal_type == SignalType.CLOSE:
                return self._handle_close(signal, slave_config, connector)

            elif signal.signal_type == SignalType.MODIFY:
                return self._handle_modify(signal, slave_config, connector)

        except Exception as e:
            logger.error(f"[{slave_config.name}] Unexpected error: {e}")
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.FAILED,
                error_message=str(e),
            )
        finally:
            connector.shutdown()

    def _handle_open(
        self,
        signal: CopySignal,
        slave_config: AccountConfig,
        connector: MT5Connector,
    ) -> CopyResult:
        """Open a new position on the slave."""
        slave_lot = slave_config.calculate_lot(signal.volume)
        if slave_lot <= 0:
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.SKIPPED,
                error_message=f"Calculated lot size is {slave_lot} (≤ 0), skipping",
            )

        ticket = connector.open_position(
            signal=signal,
            slave_lot=slave_lot,
            copy_sl=self.copy_sl,
            copy_tp=self.copy_tp,
            max_retries=self.max_retries,
            retry_delay_ms=self.retry_delay_ms,
        )

        if ticket is not None:
            self.total_opens += 1
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.SUCCESS,
                slave_ticket=ticket,
            )
        else:
            self.total_errors += 1
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.FAILED,
                error_message="open_position returned None",
            )

    def _handle_close(
        self,
        signal: CopySignal,
        slave_config: AccountConfig,
        connector: MT5Connector,
    ) -> CopyResult:
        """Close the corresponding slave position."""
        copied_positions = connector.get_copied_positions()
        slave_pos = copied_positions.get(signal.master_ticket)

        if slave_pos is None:
            # Position not found — might have been closed manually
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.SKIPPED,
                error_message=f"No copied position found for master ticket {signal.master_ticket}",
            )

        ok = connector.close_position(
            slave_position=slave_pos,
            max_retries=self.max_retries,
            retry_delay_ms=self.retry_delay_ms,
        )

        if ok:
            self.total_closes += 1
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.SUCCESS,
                slave_ticket=slave_pos.ticket,
            )
        else:
            self.total_errors += 1
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.FAILED,
                error_message="close_position returned False",
            )

    def _handle_modify(
        self,
        signal: CopySignal,
        slave_config: AccountConfig,
        connector: MT5Connector,
    ) -> CopyResult:
        """Modify SL/TP on the corresponding slave position."""
        copied_positions = connector.get_copied_positions()
        slave_pos = copied_positions.get(signal.master_ticket)

        if slave_pos is None:
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.SKIPPED,
                error_message=f"No copied position found for master ticket {signal.master_ticket}",
            )

        ok = connector.modify_position(
            slave_ticket=slave_pos.ticket,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )

        if ok:
            self.total_modifies += 1
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.SUCCESS,
                slave_ticket=slave_pos.ticket,
            )
        else:
            self.total_errors += 1
            return CopyResult(
                slave_name=slave_config.name,
                slave_login=slave_config.login,
                signal=signal,
                status=CopyStatus.FAILED,
                error_message="modify_position returned False",
            )

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log_signal(self, signal: CopySignal):
        action = signal.signal_type.value.upper()
        if signal.signal_type == SignalType.OPEN:
            logger.info(
                f"[MASTER] {action} {signal.symbol}  "
                f"{signal.order_type.name}  "
                f"lot={signal.volume:.2f}  "
                f"sl={signal.stop_loss}  tp={signal.take_profit}  "
                f"ticket={signal.master_ticket}"
            )
        elif signal.signal_type == SignalType.CLOSE:
            logger.info(
                f"[MASTER] {action} {signal.symbol}  ticket={signal.master_ticket}"
            )
        elif signal.signal_type == SignalType.MODIFY:
            logger.info(
                f"[MASTER] {action} {signal.symbol}  "
                f"sl={signal.stop_loss}  tp={signal.take_profit}  "
                f"ticket={signal.master_ticket}"
            )

    def _log_result(self, result: CopyResult):
        status = result.status.value.upper()
        base = (
            f"  → [{result.slave_name}] {result.signal.signal_type.value.upper()} "
            f"{result.signal.symbol}  {status}"
        )
        if result.slave_ticket:
            base += f"  slave_ticket={result.slave_ticket}"
        if result.error_message:
            base += f"  ({result.error_message})"
        logger.info(base)

    def _print_separator(self):
        logger.info("─" * 70)

    def _print_summary(self):
        self._print_separator()
        logger.info(
            f"Session summary — "
            f"Opens: {self.total_opens}  "
            f"Closes: {self.total_closes}  "
            f"Modifies: {self.total_modifies}  "
            f"Errors: {self.total_errors}"
        )
