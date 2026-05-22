from datetime import date, datetime
from typing import Dict, List, Optional

from upstox_client import ApiClient, Configuration
from upstox_client.api import OptionsApi

from config import analytics_token, app_token


def _build_options_api(access_token: Optional[str] = None) -> OptionsApi:
    configuration = Configuration()
    configuration.access_token = access_token or app_token or analytics_token
    return OptionsApi(ApiClient(configuration))


def _to_date(expiry_value) -> date:
    if isinstance(expiry_value, datetime):
        return expiry_value.date()
    if isinstance(expiry_value, date):
        return expiry_value
    return datetime.strptime(str(expiry_value).split(" ")[0], "%Y-%m-%d").date()


def get_option_contracts(instrument_key: str, access_token: Optional[str] = None):
    api = _build_options_api(access_token=access_token)
    response = api.get_option_contracts(instrument_key=instrument_key)
    return response.data or []


def get_nearest_expiry(instrument_key: str, access_token: Optional[str] = None) -> str:
    contracts = get_option_contracts(instrument_key=instrument_key, access_token=access_token)
    if not contracts:
        raise ValueError(f"No option contracts returned for {instrument_key}.")

    today = date.today()
    expiries = sorted({_to_date(contract.expiry) for contract in contracts if contract.expiry})
    future_expiries = [expiry for expiry in expiries if expiry >= today]
    nearest_expiry = future_expiries[0] if future_expiries else expiries[0]
    return nearest_expiry.strftime("%Y-%m-%d")


def _strike_step(strikes: List[float]) -> float:
    if len(strikes) < 2:
        return 50.0
    diffs = sorted({round(strikes[i] - strikes[i - 1], 2) for i in range(1, len(strikes)) if strikes[i] > strikes[i - 1]})
    return diffs[0] if diffs else 50.0


def _round_to_nearest_step(price: float, step: float) -> float:
    if step <= 0:
        return float(price)
    return float(step * int((price / step) + 0.5))


def choose_option_contract_for_signal(
    instrument_key: str,
    spot_price: float,
    signal: str,
    access_token: Optional[str] = None,
) -> Dict[str, object]:
    signal_side = (signal or "").upper()
    if signal_side not in {"LONG", "SHORT"}:
        raise ValueError(f"Signal must be LONG/SHORT. Received: {signal}")

    option_type = "CE" if signal_side == "LONG" else "PE"
    contracts = get_option_contracts(instrument_key=instrument_key, access_token=access_token)
    if not contracts:
        raise ValueError(f"No option contracts returned for {instrument_key}.")

    today = date.today()
    prepared = []
    for contract in contracts:
        expiry_value = getattr(contract, "expiry", None)
        strike_value = getattr(contract, "strike_price", None)
        if expiry_value is None or strike_value is None:
            continue

        try:
            expiry_date = _to_date(expiry_value)
            strike_price = float(strike_value)
        except Exception:
            continue

        prepared.append((contract, expiry_date, strike_price))

    if not prepared:
        raise ValueError("No valid contracts with expiry and strike price were found.")

    expiries = sorted({expiry for _, expiry, _ in prepared})
    future_expiries = [expiry for expiry in expiries if expiry >= today]
    nearest_expiry = future_expiries[0] if future_expiries else expiries[0]

    eligible = [
        (contract, strike_price)
        for contract, expiry_date, strike_price in prepared
        if expiry_date == nearest_expiry and str(getattr(contract, "instrument_type", "")).upper() == option_type
    ]

    if not eligible:
        raise ValueError(f"No {option_type} contracts for expiry {nearest_expiry:%Y-%m-%d}.")

    strikes = sorted({strike for _, strike in eligible})
    step = _strike_step(strikes)
    target_strike = _round_to_nearest_step(float(spot_price), float(step))

    selected_contract, selected_strike = min(
        eligible,
        key=lambda item: (abs(item[1] - target_strike), abs(item[1] - spot_price)),
    )

    return {
        "expiry": nearest_expiry.strftime("%Y-%m-%d"),
        "signal": signal_side,
        "option_type": option_type,
        "spot_price": float(spot_price),
        "target_strike": float(target_strike),
        "strike_price": float(selected_strike),
        "instrument_key": selected_contract.instrument_key,
        "trading_symbol": selected_contract.trading_symbol,
        "lot_size": int(float(selected_contract.lot_size or 1)),
    }


if __name__ == "__main__":
    from basic_variables import instr

    nearest = get_nearest_expiry(instr)
    print(f"Nearest expiry: {nearest}")
