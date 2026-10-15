import os

from dotenv import load_dotenv
import requests

from langchain_core.tools import tool

load_dotenv()
    
@tool
def get_weather(city: str) -> str:
    """
    实时获取指定城市的天气信息
    """
    url = "https://eolink.o.apispace.com/456456/weather/v001/now"

    payload = {"areacode" : "101010100","lonlat" : "116.407526,39.904030"}

    headers = {
        "X-Apispace-Token": os.getenv("WEATHER_API_KEY")
    }

    response=requests.request("GET", url, params=payload, headers=headers)

    res = response.json()

    loc = res["result"]["location"]
    rt = res["result"]["realtime"]
    # 拼接返回字符串，给LLM读取
    return f"城市:{loc['name']},天气:{rt['text']},温度:{rt['temp']}℃"

# print(get_weather.invoke({"city": "北京"}))