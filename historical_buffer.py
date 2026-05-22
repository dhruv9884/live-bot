import upstox_client
import datetime as dt
import pandas as pd

from config import analytics_token, app_token, historical_buffer_days, instr, time_frame

configuration = upstox_client.Configuration()
configuration.access_token = app_token if app_token else analytics_token

apiInstance = upstox_client.HistoryV3Api(upstox_client.ApiClient(configuration))
result_df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])

try:
    def candles_to_dataframe(response):
        candle = response.data.candles
        df = pd.DataFrame(candle, columns=['timestamp', 'open', 'high','low','close','volume','OI'])
        df.drop(columns=['volume','OI'], inplace=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['timestamp'] = df['timestamp'].dt.strftime('%d-%m-%Y %H:%M:%S')
        return df



    today = dt.date.today()
    buffer_start = str(today - dt.timedelta(days=historical_buffer_days))
    today = str(today)
    response = apiInstance.get_historical_candle_data1(instr, "minutes", time_frame, today, buffer_start)
    df = candles_to_dataframe(response)
    # print(df)
except Exception as e:
    print("Exception when calling HistoryV3Api->get_historical_candle_data1: %s\n" % e)
    df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])


try:
    response = apiInstance.get_intra_day_candle_data(instr, "minutes", time_frame)
    df_intra = candles_to_dataframe(response)
    result_df = pd.concat([df_intra, df], ignore_index=True)
    result_df = result_df.iloc[::-1].reset_index(drop=True)
except Exception as e:
    print("Exception when calling HistoryV3Api->get_intra_day_candle_data: %s\n" % e)


if __name__ == "__main__":
    print(result_df)
