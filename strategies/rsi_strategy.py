import numpy as np
import talib

class RSIStrategy:
    def __init__(self, timeperiod=14, low_thresh=30, high_thresh=70):
        self.timeperiod = timeperiod
        self.low_thresh = low_thresh
        self.high_thresh = high_thresh

    def evaluate(self, prices):
        prices = np.array(prices, dtype='float64')
        if len(prices) < self.timeperiod:
            return "HOLD"

        rsi = talib.RSI(prices, timeperiod=self.timeperiod)
        latest_rsi = rsi[-1]

        if np.isnan(latest_rsi):
            return "HOLD"

        if latest_rsi < self.low_thresh:
            return "BUY"
        elif latest_rsi > self.high_thresh:
            return "SELL"
        else:
            return "HOLD"
        
