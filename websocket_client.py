import upstox_client
from datetime import datetime
from urllib import parse, request
import pandas as pd
import numpy as np

from config import (
    analytics_token,
    app_token,
    instr,
    mode,
    strategy_period,
    strategy_mult,
    show_signal_data,
    show_websocket_dataframe,
    print_rows,
    option_ws_interval_seconds,
    option_target_points,
    option_stop_points,
    telegram_bot_token,
    telegram_chat_id,
)
from historical_buffer import result_df as historical_result_df
from get_expiry import choose_option_contract_for_signal
from option_trade_websocket import OptionTradeManager


# Change these if needed
ACCESS_TOKEN = analytics_token or app_token
INSTRUMENT_KEYS = [x.strip() for x in instr.split(",") if x.strip()]
MODE = mode  # ltpc / full / full_d30 / option_greeks
FIVE_MIN_MS = 5 * 60 * 1000
BUFFER_COLUMNS = ["timestamp", "open", "high", "low", "close"]
STRATEGY_PERIOD = strategy_period
STRATEGY_MULT = strategy_mult
SHOW_SIGNAL_DATA = show_signal_data
SHOW_WEBSOCKET_DATAFRAME = show_websocket_dataframe
PRINT_ROWS = print_rows
OPTION_WS_INTERVAL_SECONDS = option_ws_interval_seconds
OPTION_TARGET_POINTS = option_target_points
OPTION_STOP_POINTS = option_stop_points
TELEGRAM_BOT_TOKEN = telegram_bot_token
TELEGRAM_CHAT_ID = telegram_chat_id
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

current_5m = {}
has_buffer_columns = isinstance(historical_result_df, pd.DataFrame) and set(BUFFER_COLUMNS).issubset(
    historical_result_df.columns
)
result_df = (
    historical_result_df[BUFFER_COLUMNS].copy()
    if has_buffer_columns
    else pd.DataFrame(columns=BUFFER_COLUMNS)
)
strategy_df = result_df.copy()
last_reported_signal_index = -1


def send_telegram_message(message: str) -> None:
    if not TELEGRAM_ENABLED or not message:
        return

    try:
        payload = parse.urlencode(
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            }
        ).encode("utf-8")
        req = request.Request(
            url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=payload,
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with request.urlopen(req, timeout=15):
            pass
    except Exception as exc:
        print(f"Telegram send failed: {exc}")


def notify_trade_closed(trade: dict) -> None:
    realized_pnl = float(trade.get("realized_pnl", 0.0))
    realized_points = float(trade.get("realized_points", 0.0))
    status = "PROFIT" if realized_pnl >= 0 else "LOSS"
    message = (
        "Trade Report\n"
        f"Result: {status}\n"
        f"Signal: {trade.get('signal')} at {trade.get('signal_time')}\n"
        f"Symbol: {trade.get('trading_symbol')} | Qty: {trade.get('quantity')}\n"
        f"Entry: {trade.get('entry_price')} ({trade.get('entry_time')})\n"
        f"Exit: {trade.get('exit_price')} ({trade.get('exit_time')})\n"
        f"Points: {realized_points:+.2f} | PnL: {realized_pnl:+.2f}\n"
        f"Reason: {trade.get('exit_reason')}"
    )
    send_telegram_message(message)


option_trade_manager = OptionTradeManager(
    access_token=ACCESS_TOKEN,
    interval_seconds=OPTION_WS_INTERVAL_SECONDS,
    target_points=OPTION_TARGET_POINTS,
    stop_points=OPTION_STOP_POINTS,
    on_trade_closed=notify_trade_closed,
    verbose_ws_logs=SHOW_WEBSOCKET_DATAFRAME,
)


def print_candle(instrument, candle):
    if not SHOW_WEBSOCKET_DATAFRAME:
        return
    start_time = datetime.fromtimestamp(candle["start"] / 1000)
    end_time = datetime.fromtimestamp(candle["end"] / 1000)
    print(
        f'{instrument} 5m [{start_time:%H:%M} -> {end_time:%H:%M}] '
        f'O:{candle["open"]} H:{candle["high"]} L:{candle["low"]} C:{candle["close"]}'
    )


def super_bollinger_trend(
    df: pd.DataFrame,
    period: int = STRATEGY_PERIOD,
    mult: float = STRATEGY_MULT,
) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        df["bb_up"] = pd.Series(dtype="float64")
        df["bb_dn"] = pd.Series(dtype="float64")
        df["SBT"] = pd.Series(dtype="float64")
        df["Signal"] = pd.Series(dtype="object")
        return df

    if {"High", "Low", "Close"}.issubset(df.columns):
        high_col, low_col, close_col = "High", "Low", "Close"
    elif {"high", "low", "close"}.issubset(df.columns):
        high_col, low_col, close_col = "high", "low", "close"
    else:
        raise KeyError("DataFrame must contain High/Low/Close or high/low/close columns.")

    df[high_col] = pd.to_numeric(df[high_col], errors="coerce")
    df[low_col] = pd.to_numeric(df[low_col], errors="coerce")
    df[close_col] = pd.to_numeric(df[close_col], errors="coerce")

    df["bb_up"] = df[high_col].rolling(period).mean() + df[high_col].rolling(period).std(ddof=0) * mult
    df["bb_dn"] = df[low_col].rolling(period).mean() - df[low_col].rolling(period).std(ddof=0) * mult

    sbt = np.zeros(len(df))
    signal = [None] * len(df)
    first_close = df[close_col].iloc[0]
    sbt[0] = float(first_close) if pd.notna(first_close) else 0.0

    for idx in range(1, len(df)):
        close = df[close_col].iloc[idx]
        prev_close = df[close_col].iloc[idx - 1]
        prev_sbt = sbt[idx - 1]
        bb_up = df["bb_up"].iloc[idx]
        bb_dn = df["bb_dn"].iloc[idx]

        if pd.isna(close) or pd.isna(prev_close) or pd.isna(bb_up) or pd.isna(bb_dn):
            sbt[idx] = prev_sbt
            continue

        close = float(close)
        prev_close = float(prev_close)

        if close > prev_sbt:
            current_sbt = max(prev_sbt, float(bb_dn))
        else:
            current_sbt = min(prev_sbt, float(bb_up))

        if close > prev_sbt and prev_close <= prev_sbt:
            signal[idx] = "LONG"
            current_sbt = float(bb_dn)
        elif close < prev_sbt and prev_close >= prev_sbt:
            signal[idx] = "SHORT"
            current_sbt = float(bb_up)

        sbt[idx] = current_sbt

    df["SBT"] = sbt
    df["Signal"] = signal
    return df


def get_signal_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "Signal" not in df.columns:
        return pd.DataFrame(columns=df.columns)
    return df[df["Signal"].isin(["LONG", "SHORT"])]


def print_signal_summary(df: pd.DataFrame, label: str) -> None:
    signal_rows = get_signal_rows(df)
    if signal_rows.empty:
        print(f"{label}: no LONG/SHORT signals yet.")
        return

    print(f"{label}: total signals = {len(signal_rows)}")
    cols = [c for c in ["timestamp", "close", "SBT", "Signal"] if c in signal_rows.columns]
    print(signal_rows[cols].tail())


def print_websocket_dataframe(df: pd.DataFrame) -> None:
    if not SHOW_WEBSOCKET_DATAFRAME:
        return
    print(f"\nConcatenated buffer + live data with strategy (last {PRINT_ROWS} rows):")
    print(df.tail(PRINT_ROWS))


def build_strategy_with_live_candle(candle=None) -> pd.DataFrame:
    if candle is None:
        return super_bollinger_trend(result_df)

    candle_time = datetime.fromtimestamp(candle["end"] / 1000).strftime("%d-%m-%Y %H:%M:%S")
    live_row = pd.DataFrame(
        [
            {
                "timestamp": candle_time,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }
        ]
    )
    combined_df = pd.concat([result_df, live_row], ignore_index=True)
    return super_bollinger_trend(combined_df)


def append_to_buffer(candle):
    global result_df, strategy_df, last_reported_signal_index

    # Use candle close boundary time (e.g., 13:55:00) for stored timestamp.
    candle_time = datetime.fromtimestamp(candle["end"] / 1000).strftime("%d-%m-%Y %H:%M:%S")
    live_row = pd.DataFrame(
        [
            {
                "timestamp": candle_time,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }
        ]
    )
    result_df = pd.concat([result_df, live_row], ignore_index=True)
    strategy_df = super_bollinger_trend(result_df)

    signal_rows = get_signal_rows(strategy_df)
    if signal_rows.empty:
        return

    latest_signal_idx = int(signal_rows.index[-1])

    if latest_signal_idx > last_reported_signal_index:
        last_reported_signal_index = latest_signal_idx
        latest_signal = signal_rows.loc[latest_signal_idx]
        send_telegram_message(
            "Signal Generated\n"
            f"Signal: {latest_signal.get('Signal')}\n"
            f"Time: {latest_signal.get('timestamp')}\n"
            f"Close: {latest_signal.get('close')} | SBT: {latest_signal.get('SBT')}"
        )
        trigger_option_trade_from_signal(latest_signal)
        if SHOW_SIGNAL_DATA:
            print(
                f"New signal generated -> {latest_signal['Signal']} at "
                f"{latest_signal['timestamp']} close={latest_signal['close']} SBT={latest_signal['SBT']}"
            )
            print_signal_summary(strategy_df, "Recent signals")


def trigger_option_trade_from_signal(signal_row: pd.Series) -> None:
    signal_value = str(signal_row.get("Signal", "")).upper()
    if signal_value not in {"LONG", "SHORT"}:
        return

    signal_time = str(signal_row.get("timestamp"))
    if option_trade_manager.has_active_trade():
        print("Signal generated but option trade is already active. Skipping new entry.")
        send_telegram_message(
            "Trade Skipped\n"
            f"From Signal: {signal_value} at {signal_time}\n"
            "Reason: Active trade is already running."
        )
        return

    try:
        spot_price = float(signal_row.get("close"))
    except Exception:
        print("Signal generated but close price is invalid. Option trade skipped.")
        send_telegram_message(
            "Trade Skipped\n"
            f"From Signal: {signal_value} at {signal_time}\n"
            "Reason: Invalid close price for signal candle."
        )
        return

    # Strategy currently runs on a single underlying from UPSTOX_INSTRUMENT.
    underlying_key = INSTRUMENT_KEYS[0]

    try:
        option_contract = choose_option_contract_for_signal(
            instrument_key=underlying_key,
            spot_price=spot_price,
            signal=signal_value,
            access_token=ACCESS_TOKEN,
        )
    except Exception as exc:
        print(f"Failed to select option contract for signal {signal_value}: {exc}")
        send_telegram_message(
            "Trade Skipped\n"
            f"From Signal: {signal_value} at {signal_time}\n"
            f"Reason: Option contract selection failed ({exc})."
        )
        return

    print(
        f"Signal -> option mapping | signal={signal_value} | time={signal_time} | "
        f"spot={spot_price} | expiry={option_contract['expiry']} | "
        f"strike={option_contract['strike_price']} | symbol={option_contract['trading_symbol']}"
    )

    is_started = option_trade_manager.start_trade(
        option_instrument_key=option_contract["instrument_key"],
        trading_symbol=option_contract["trading_symbol"],
        quantity=int(option_contract["lot_size"]),
        signal=signal_value,
        signal_time=signal_time,
        index_price=spot_price,
        strike_price=float(option_contract["strike_price"]),
        expiry=str(option_contract["expiry"]),
    )
    if is_started:
        send_telegram_message(
            "Trade Placed\n"
            f"From Signal: {signal_value} at {signal_time}\n"
            f"Option: {option_contract['trading_symbol']} ({option_contract['option_type']})\n"
            f"Expiry: {option_contract['expiry']} | Strike: {option_contract['strike_price']}\n"
            f"Qty: {int(option_contract['lot_size'])} | Spot: {spot_price}"
        )


def update_5m_candle(instrument, price, ts_ms):
    bucket_start = ts_ms - (ts_ms % FIVE_MIN_MS)
    bucket_end = bucket_start + FIVE_MIN_MS
    candle = current_5m.get(instrument)

    if candle is None:
        # First candle waits for the next clock boundary (:00/:05/:10/:15...)
        current_5m[instrument] = {
            "start": bucket_start,
            "end": bucket_end,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
        }
        live_strategy_df = build_strategy_with_live_candle(current_5m[instrument])
        print_websocket_dataframe(live_strategy_df)
        return

    if ts_ms >= candle["end"] and candle["end"] <= bucket_start:
        print_candle(instrument, candle)
        append_to_buffer(candle)
        current_5m[instrument] = {
            "start": bucket_start,
            "end": bucket_end,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
        }
        live_strategy_df = build_strategy_with_live_candle(current_5m[instrument])
        print_websocket_dataframe(live_strategy_df)
        return

    candle["high"] = max(candle["high"], price)
    candle["low"] = min(candle["low"], price)
    candle["close"] = price
    live_strategy_df = build_strategy_with_live_candle(candle)
    print_websocket_dataframe(live_strategy_df)


def read_ltp_and_time(feed):
    full_feed = feed.get("fullFeed", {})
    index_ff = full_feed.get("indexFF", {})
    market_ff = full_feed.get("marketFF", {})
    ltpc = index_ff.get("ltpc") or market_ff.get("ltpc") or {}

    ltp = ltpc.get("ltp")
    ltt = ltpc.get("ltt")

    if ltp is None or ltt is None:
        return None, None

    return float(ltp), int(ltt)


def run_ws():
    global strategy_df, last_reported_signal_index
    if not ACCESS_TOKEN:
        raise ValueError("Missing access token. Set UPSTOX_ANALYTICS_TOKEN or UPSTOX_APP_TOKEN.")
    if not INSTRUMENT_KEYS:
        raise ValueError("No instruments configured. Set UPSTOX_INSTRUMENT.")

    configuration = upstox_client.Configuration()
    configuration.access_token = ACCESS_TOKEN

    streamer = upstox_client.MarketDataStreamerV3(
        upstox_client.ApiClient(configuration),
    )

    def on_open():
        print("Connected to Upstox websocket")
        print(f"Starting buffer rows: {len(result_df)}")
        if not TELEGRAM_ENABLED:
            print("Telegram alerts disabled. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable.")
        strategy_df = super_bollinger_trend(result_df)
        historical_signals = get_signal_rows(strategy_df)
        if not historical_signals.empty:
            last_reported_signal_index = int(historical_signals.index[-1])
        if SHOW_SIGNAL_DATA:
            print_signal_summary(strategy_df, "Historical signal check")
        streamer.subscribe(INSTRUMENT_KEYS, MODE)

    def on_message(message):
        feeds = message.get("feeds", {})

        for instrument, feed in feeds.items():
            ltp, ts_ms = read_ltp_and_time(feed)
            if ltp is None:
                continue
            update_5m_candle(instrument, ltp, ts_ms)

    def on_error(error):
        print("Error:", error)

    def on_close(code, reason):
        print("Closed:", code, reason)
        option_trade_manager.shutdown()
        for instrument, candle in current_5m.items():
            print_candle(instrument, candle)
        print(f"Final buffer rows: {len(result_df)}")

    streamer.on("open", on_open)
    streamer.on("message", on_message)
    streamer.on("error", on_error)
    streamer.on("close", on_close)
    streamer.connect()


if __name__ == "__main__":
    run_ws()
