import os


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw.strip())


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


app_token = _get_str("UPSTOX_APP_TOKEN")
analytics_token = _get_str("UPSTOX_ANALYTICS_TOKEN")

# Underlying + history config
instr = _get_str("UPSTOX_INSTRUMENT", "NSE_INDEX|Nifty 50")
time_frame = _get_str("UPSTOX_TIME_FRAME", "5")
historical_buffer_days = _get_int("UPSTOX_HISTORICAL_BUFFER_DAYS", 4)

# Main websocket strategy config
mode = _get_str("UPSTOX_MODE", "full")
strategy_period = _get_int("UPSTOX_STRATEGY_PERIOD", 20)
strategy_mult = _get_float("UPSTOX_STRATEGY_MULT", 2.0)
show_signal_data = _get_bool("UPSTOX_SHOW_SIGNAL_DATA", False)
show_websocket_dataframe = _get_bool("UPSTOX_SHOW_WEBSOCKET_DATAFRAME", False)
print_rows = _get_int("UPSTOX_PRINT_ROWS", 5)

# Option trade tracking config
option_ws_interval_seconds = _get_int("UPSTOX_OPTION_WS_INTERVAL_SECONDS", 10)
option_target_points = _get_float("UPSTOX_OPTION_TARGET_POINTS", 10.0)
option_stop_points = _get_float("UPSTOX_OPTION_STOP_POINTS", -6.0)

# Telegram notifications
telegram_bot_token = _get_str("TELEGRAM_BOT_TOKEN")
telegram_chat_id = _get_str("TELEGRAM_CHAT_ID")
