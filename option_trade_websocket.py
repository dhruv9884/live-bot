import threading
from datetime import datetime
from typing import Callable, Dict, Optional
import upstox_client


class OptionTradeManager:
    def __init__(
        self,
        access_token: str,
        interval_seconds: int = 10,
        target_points: float = 10.0,
        stop_points: float = -6.0,
        mode: str = "full",
        on_trade_closed: Optional[Callable[[Dict[str, object]], None]] = None,
        verbose_ws_logs: bool = False,
    ):
        self.access_token = access_token
        self.interval_ms = int(interval_seconds * 1000)
        self.target_points = float(target_points)
        self.stop_points = float(stop_points)
        self.mode = mode
        self._on_trade_closed = on_trade_closed
        self.verbose_ws_logs = bool(verbose_ws_logs)

        self._lock = threading.Lock()
        self._streamer = None
        self._thread = None
        self._active_trade: Optional[Dict[str, object]] = None
        self._current_candle: Optional[Dict[str, float]] = None

    def has_active_trade(self) -> bool:
        with self._lock:
            return self._active_trade is not None and not bool(self._active_trade.get("closed"))

    def start_trade(
        self,
        option_instrument_key: str,
        trading_symbol: str,
        quantity: int,
        signal: str,
        signal_time: str,
        index_price: float,
        strike_price: float,
        expiry: str,
    ) -> bool:
        with self._lock:
            if self._active_trade is not None and not bool(self._active_trade.get("closed")):
                active_symbol = self._active_trade.get("trading_symbol")
                print(f"Option trade already active on {active_symbol}. New signal ignored.")
                return False

            self._active_trade = {
                "option_instrument_key": option_instrument_key,
                "trading_symbol": trading_symbol,
                "quantity": int(quantity),
                "signal": signal,
                "signal_time": signal_time,
                "index_price": float(index_price),
                "strike_price": float(strike_price),
                "expiry": expiry,
                "entry_price": None,
                "entry_time": None,
                "last_price": None,
                "last_time": None,
                "unrealized_points": 0.0,
                "unrealized_pnl": 0.0,
                "closed": False,
            }
            self._current_candle = None

        print(
            f"Starting option websocket | symbol={trading_symbol} | signal={signal} "
            f"| qty={quantity} | strike={strike_price} | expiry={expiry}"
        )
        target_pnl = self.target_points * int(quantity)
        stop_pnl = self.stop_points * int(quantity)
        print(
            f"Exit thresholds -> points: target {self.target_points:+.2f}, stop {self.stop_points:+.2f} "
            f"| approx pnl: target {target_pnl:+.2f}, stop {stop_pnl:+.2f}"
        )

        self._thread = threading.Thread(
            target=self._run_stream,
            args=(option_instrument_key,),
            daemon=True,
        )
        self._thread.start()
        return True

    def shutdown(self) -> None:
        streamer = None
        with self._lock:
            streamer = self._streamer

        if streamer is not None:
            try:
                streamer.disconnect()
            except Exception as exc:
                print(f"Option websocket shutdown error: {exc}")

    def _run_stream(self, option_instrument_key: str) -> None:
        configuration = upstox_client.Configuration()
        configuration.access_token = self.access_token

        streamer = upstox_client.MarketDataStreamerV3(
            upstox_client.ApiClient(configuration),
        )

        with self._lock:
            self._streamer = streamer

        def on_open():
            print(f"Option websocket connected for {option_instrument_key}")
            streamer.subscribe([option_instrument_key], self.mode)

        def on_message(message):
            feeds = message.get("feeds", {})
            for _, feed in feeds.items():
                ltp, ts_ms = self._read_ltp_and_time(feed)
                if ltp is None:
                    continue
                self._update_10s_candle(ltp, ts_ms)
                self._register_trade_tick(ltp, ts_ms)

        def on_error(error):
            print("Option websocket error:", error)

        def on_close(code, reason):
            print("Option websocket closed:", code, reason)
            with self._lock:
                candle = self._current_candle
                if candle is not None and self.verbose_ws_logs:
                    self._print_candle(candle)
                self._current_candle = None
                self._streamer = None
                if self._active_trade is not None:
                    self._active_trade = None

        streamer.on("open", on_open)
        streamer.on("message", on_message)
        streamer.on("error", on_error)
        streamer.on("close", on_close)
        streamer.connect()

    @staticmethod
    def _read_ltp_and_time(feed):
        full_feed = feed.get("fullFeed", {})
        index_ff = full_feed.get("indexFF", {})
        market_ff = full_feed.get("marketFF", {})
        ltpc = index_ff.get("ltpc") or market_ff.get("ltpc") or {}

        ltp = ltpc.get("ltp")
        ltt = ltpc.get("ltt")
        if ltp is None or ltt is None:
            return None, None
        return float(ltp), int(ltt)

    def _update_10s_candle(self, price: float, ts_ms: int) -> None:
        bucket_start = ts_ms - (ts_ms % self.interval_ms)
        bucket_end = bucket_start + self.interval_ms

        with self._lock:
            candle = self._current_candle

            if candle is None:
                self._current_candle = {
                    "start": bucket_start,
                    "end": bucket_end,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
                return

            if ts_ms >= candle["end"] and candle["end"] <= bucket_start:
                if self.verbose_ws_logs:
                    self._print_candle(candle)
                self._current_candle = {
                    "start": bucket_start,
                    "end": bucket_end,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
                return

            candle["high"] = max(candle["high"], price)
            candle["low"] = min(candle["low"], price)
            candle["close"] = price

    def _register_trade_tick(self, ltp: float, ts_ms: int) -> None:
        close_reason = None
        closed_trade = None
        with self._lock:
            trade = self._active_trade
            if trade is None or bool(trade.get("closed")):
                return

            tick_time = datetime.fromtimestamp(ts_ms / 1000).strftime("%d-%m-%Y %H:%M:%S")
            if trade["entry_price"] is None:
                trade["entry_price"] = float(ltp)
                trade["entry_time"] = tick_time
                print(
                    f"Option entry taken at {tick_time} | symbol={trade['trading_symbol']} "
                    f"| entry={trade['entry_price']:.2f} | qty={trade['quantity']}"
                )

            trade["last_price"] = float(ltp)
            trade["last_time"] = tick_time
            trade["unrealized_points"] = trade["last_price"] - float(trade["entry_price"])
            trade["unrealized_pnl"] = float(trade["unrealized_points"]) * int(trade["quantity"])

            points = float(trade["unrealized_points"])
            pnl = float(trade["unrealized_pnl"])
            if self.verbose_ws_logs:
                print(
                    f"Option tick | {trade['trading_symbol']} | ltp={trade['last_price']:.2f} "
                    f"| points={points:+.2f} | pnl={pnl:+.2f}"
                )

            if points >= self.target_points:
                close_reason = "TARGET_HIT"
            elif points <= self.stop_points:
                close_reason = "STOP_HIT"

            if close_reason is not None:
                trade["closed"] = True
                trade["exit_reason"] = close_reason
                trade["exit_price"] = trade["last_price"]
                trade["exit_time"] = trade["last_time"]
                trade["realized_points"] = points
                trade["realized_pnl"] = pnl
                print(
                    f"Option exit | reason={close_reason} | symbol={trade['trading_symbol']} "
                    f"| entry={trade['entry_price']:.2f} | exit={trade['exit_price']:.2f} "
                    f"| points={points:+.2f} | pnl={pnl:+.2f}"
                )
                closed_trade = dict(trade)

            streamer = self._streamer

        if closed_trade is not None and self._on_trade_closed is not None:
            try:
                self._on_trade_closed(closed_trade)
            except Exception as exc:
                print(f"Trade close callback failed: {exc}")

        if close_reason is not None and streamer is not None:
            try:
                streamer.disconnect()
            except Exception as exc:
                print(f"Error disconnecting option websocket: {exc}")

    @staticmethod
    def _print_candle(candle: Dict[str, float]) -> None:
        start_time = datetime.fromtimestamp(candle["start"] / 1000)
        end_time = datetime.fromtimestamp(candle["end"] / 1000)
        print(
            f"Option 10s [{start_time:%H:%M:%S} -> {end_time:%H:%M:%S}] "
            f"O:{candle['open']} H:{candle['high']} L:{candle['low']} C:{candle['close']}"
        )
