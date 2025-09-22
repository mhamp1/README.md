import streamlit as st
import numpy as np
from strategies.rsi_strategy import RSIStrategy

# 📊 Live config sliders
st.title("RSI Strategy Dashboard")

timeperiod = st.slider("RSI Timeperiod", min_value=5, max_value=30, value=14)
low_thresh = st.slider("Buy Threshold", min_value=10, max_value=50, value=30)
high_thresh = st.slider("Sell Threshold", min_value=50, max_value=90, value=70)

# 📈 Sample price history (replace with live feed or file input later)
price_history = st.text_input("Enter comma-separated prices", "47,48,47,48,47,48,47,48,47,48")
prices = [float(p.strip()) for p in price_history.split(",") if p.strip()]

# 🧠 Evaluate strategy
strategy = RSIStrategy(timeperiod, low_thresh, high_thresh)
decision = strategy.evaluate(prices)

# 🖥️ Display result
st.write(f"**Latest RSI Decision:** {decision}")

import pandas as pd
import matplotlib.pyplot as plt

# Convert prices to Series
price_series = pd.Series(prices, dtype='float64')
rsi_values = talib.RSI(price_series, timeperiod=timeperiod)

# Plot RSI
fig, ax = plt.subplots()
ax.plot(rsi_values, label='RSI')
ax.axhline(y=low_thresh, color='green', linestyle='--', label='Buy Threshold')
ax.axhline(y=high_thresh, color='red', linestyle='--', label='Sell Threshold')
ax.set_title("RSI Over Time")
ax.legend()

st.pyplot(fig)

uploaded_file = st.file_uploader("Upload price CSV", type=["csv"])
if uploaded_file:
    df = pd.read_csv(uploaded_file)
    prices = df['Close'].tolist()  # Or whatever column holds your price data

    import pandas as pd
import matplotlib.pyplot as plt

# Convert prices to Series
price_series = pd.Series(prices, dtype='float64')
rsi_values = talib.RSI(price_series, timeperiod=timeperiod)

# Plot RSI
fig, ax = plt.subplots()
ax.plot(rsi_values, label='RSI')
ax.axhline(y=low_thresh, color='green', linestyle='--', label='Buy Threshold')
ax.axhline(y=high_thresh, color='red', linestyle='--', label='Sell Threshold')
ax.set_title("RSI Over Time")
ax.legend()

st.pyplot(fig)

uploaded_file = st.file_uploader("Upload CSV with 'Close' prices", type=["csv"])
if uploaded_file:
    df = pd.read_csv(uploaded_file)
    prices = df['Close'].tolist()
