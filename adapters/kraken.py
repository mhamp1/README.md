import aiohttp

async def fetch_recent_prices():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.kraken.com/0/public/OHLC?pair=SOLUSDC&interval=1") as resp:
            data = await resp.json()
            candles = data["result"]["SOLUSDC"]
            closes = [float(candle[4]) for candle in candles]
            return closes[-100:]
