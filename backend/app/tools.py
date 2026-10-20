import logging
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings

from app.cache import cache_get, cache_key, cache_set

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
load_dotenv(BACKEND_DIR / ".env")

APISPACE_BASE_URL = "https://eolink.o.apispace.com/456456"
REQUEST_TIMEOUT_SECONDS = 10
CITY_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CURRENT_WEATHER_CACHE_TTL_SECONDS = 5 * 60
FORECAST_CACHE_TTL_SECONDS = 15 * 60
KNOWLEDGE_CACHE_TTL_SECONDS = int(os.getenv("KNOWLEDGE_CACHE_TTL_SECONDS", "900"))
KNOWLEDGE_CACHE_VERSION = os.getenv("KNOWLEDGE_CACHE_VERSION", "1").strip() or "1"

embedding = OpenAIEmbeddings(
    openai_api_key=os.getenv("ZAI_API_KEY"),
    model="embedding-3",
    openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
    request_timeout=REQUEST_TIMEOUT_SECONDS,
    max_retries=0,
)
vector_db = Chroma(
    persist_directory=str(DATA_DIR / "chroma_db"),
    embedding_function=embedding,
)


def _apispace_headers() -> dict[str, str]:
    token = os.getenv("WEATHER_API_KEY")
    if not token:
        raise RuntimeError("未配置 WEATHER_API_KEY，无法查询天气。")
    return {
        "X-APISpace-Token": token,
        "Authorization-Type": "apikey",
    }


def _city_records(value: Any) -> list[dict[str, Any]]:
    """Extract city records from APISpace's nested response without assuming one wrapper key."""
    records: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            has_code = any(key in item for key in ("areacode", "areaCode", "id"))
            has_name = any(key in item for key in ("name", "location", "city"))
            if has_code and has_name:
                records.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return records


def _find_city_code(city: str) -> tuple[str, str]:
    query = city.strip().replace(" ", "")
    cached = cache_get(cache_key("weather-city", query.casefold()))
    if isinstance(cached, dict) and cached.get("code") and cached.get("name"):
        return str(cached["code"]), str(cached["name"])

    response = requests.get(
        f"{APISPACE_BASE_URL}/function/v001/city",
        params={
            "location": city,
            "items": 20,
            "area": "china",
            "language": "CHN",
            "withTz": "false",
            "withPoi": "false",
        },
        headers=_apispace_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    records = _city_records(payload)
    if not records:
        raise ValueError(f"没有找到“{city}”对应的城市，请检查城市名称。")

    def name_of(record: dict[str, Any]) -> str:
        raw_name = record.get("name") or record.get("location") or record.get("city") or ""
        return str(raw_name).strip()

    def normalized(value: str) -> str:
        return value.replace(" ", "").removesuffix("市").removesuffix("地区")

    exact = [record for record in records if normalized(name_of(record)) == normalized(query)]
    matches = exact or [
        record
        for record in records
        if normalized(query) in normalized(name_of(record))
        or normalized(name_of(record)) in normalized(query)
    ]

    # Deduplicate equivalent records and avoid silently choosing between same-named cities.
    unique: dict[str, dict[str, Any]] = {}
    for record in matches:
        code = record.get("areacode") or record.get("areaCode") or record.get("id")
        if code:
            unique[str(code)] = record
    if not unique:
        raise ValueError(f"没有找到“{city}”对应的城市，请检查城市名称。")
    if len(unique) > 1:
        choices = [
            str(record.get("path") or name_of(record))
            for record in list(unique.values())[:5]
        ]
        raise ValueError(
            f"“{city}”匹配到多个地区（{'、'.join(choices)}），请补充省份或更具体的城市名称。"
        )

    code, record = next(iter(unique.items()))
    resolved_city = name_of(record) or city
    cache_set(
        cache_key("weather-city", query.casefold()),
        {"code": code, "name": resolved_city},
        CITY_CACHE_TTL_SECONDS,
    )
    return code, resolved_city


def _format_weather_response(payload: dict[str, Any], fallback_city: str) -> str:
    result = payload.get("result") or {}
    location = result.get("location") or {}
    realtime = result.get("realtime") or {}
    if not realtime:
        message = payload.get("msg") or payload.get("message") or "天气服务没有返回实时天气数据。"
        return str(message)

    actual_city = location.get("name") or fallback_city
    weather = realtime.get("text", "天气情况未知")
    temperature = realtime.get("temp")
    temp_text = f"{temperature}℃" if temperature is not None else "暂无温度数据"
    updated_at = result.get("last_update")
    suffix = f"，更新时间:{updated_at}" if updated_at else ""
    return f"城市:{actual_city},天气:{weather},温度:{temp_text}{suffix}"


def _query_weather(params: dict[str, str], fallback_city: str) -> str:
    key = cache_key("weather-current", {"params": params, "city": fallback_city})
    cached = cache_get(key)
    if isinstance(cached, str):
        return cached

    response = requests.get(
        f"{APISPACE_BASE_URL}/weather/v001/now",
        params=params,
        headers=_apispace_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    formatted = _format_weather_response(payload, fallback_city)
    result = payload.get("result") or {}
    if result.get("realtime"):
        cache_set(key, formatted, CURRENT_WEATHER_CACHE_TTL_SECONDS)
    return formatted


def _forecast_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            has_date = any(key in item for key in ("date", "fxDate", "day"))
            has_condition = any(key in item for key in ("text_day", "textDay", "weather", "text"))
            if has_date and has_condition:
                records.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return records


def _format_forecast_response(payload: Any, fallback_city: str, days: int) -> str:
    if isinstance(payload, dict):
        result = payload.get("result") or payload.get("data") or payload
    else:
        result = payload
    location = result.get("location") if isinstance(result, dict) else None
    actual_city = location.get("name") if isinstance(location, dict) else None
    records = _forecast_records(result)[:days]
    if not records:
        message = (
            payload.get("msg") or payload.get("message") or "天气服务没有返回预报数据。"
            if isinstance(payload, dict)
            else "天气服务没有返回预报数据。"
        )
        return str(message)

    lines = [f"{actual_city or fallback_city}未来 {len(records)} 天天气预报："]
    for record in records:
        date = record.get("date") or record.get("fxDate") or record.get("day") or "日期未知"
        condition = (
            record.get("text_day") or record.get("textDay") or record.get("weather") or record.get("text") or "天气未知"
        )
        low = next((record[key] for key in ("low", "temp_min", "tempMin") if record.get(key) is not None), None)
        high = next((record[key] for key in ("high", "temp_max", "tempMax") if record.get(key) is not None), None)
        if low is None and high is None:
            temperature = "气温暂无数据"
        else:
            temperature = f"{low if low is not None else '?'}–{high if high is not None else '?'}℃"
        night = record.get("text_night") or record.get("textNight")
        condition_text = f"{condition}，夜间{night}" if night else str(condition)
        lines.append(f"{date}：{condition_text}，{temperature}")
    return "\n".join(lines)


def _query_weather_forecast(params: dict[str, str], fallback_city: str, days: int) -> str:
    key = cache_key(
        "weather-forecast",
        {"params": params, "city": fallback_city, "days": days},
    )
    cached = cache_get(key)
    if isinstance(cached, str):
        return cached

    response = requests.get(
        f"{APISPACE_BASE_URL}/weather/v001/day",
        params={**params, "days": days},
        headers=_apispace_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    formatted = _format_forecast_response(payload, fallback_city, days)
    if _forecast_records(payload):
        cache_set(key, formatted, FORECAST_CACHE_TTL_SECONDS)
    return formatted


def get_weather_by_coordinates(latitude: float, longitude: float, days: int = 1) -> str:
    """Query weather for browser-provided coordinates. This helper is called by the API layer."""
    try:
        params = {"lonlat": f"{longitude},{latitude}"}
        if days > 1:
            return _query_weather_forecast(params, "当前位置", min(max(days, 1), 15))
        return _query_weather(params, "当前位置")
    except requests.Timeout:
        return "天气服务请求超时，请稍后重试。"
    except requests.RequestException as exc:
        return f"天气服务暂时不可用：{exc}"
    except (ValueError, RuntimeError) as exc:
        return str(exc)


@tool
def get_weather(city: str = "", use_current_location: bool = False, days: int = 1) -> str:
    """查询指定城市天气或用户授权的当前位置天气。days=1 查询实时天气；用户问未来几天时用 3，最大 15。"""
    city = city.strip()
    days = min(max(days, 1), 15)
    if use_current_location:
        return "请由聊天服务使用已授权的位置查询天气。"
    if not city:
        return "请提供要查询天气的城市名称。"

    try:
        city_code, resolved_city = _find_city_code(city)
        params = {"areacode": city_code}
        if days > 1:
            return _query_weather_forecast(params, resolved_city, days)
        return _query_weather(params, resolved_city)
    except requests.Timeout:
        return "天气服务请求超时，请稍后重试。"
    except requests.RequestException as exc:
        return f"天气服务暂时不可用：{exc}"
    except (ValueError, RuntimeError) as exc:
        return str(exc)


@tool
def search_flower_knowledge(query: str) -> str:
    """在花卉知识库中搜索相关信息。"""
    passages = retrieve_flower_knowledge(query, k=3)
    if not passages:
        return "知识库中暂时没有找到相关资料。"
    return "\n".join(passages)


@tool
def get_watering_fertilizing_plan(
    flower: str,
    season: str = "",
    growth_stage: str = "",
    environment: str = "",
) -> str:
    """根据花名和养护背景查询浇水、施肥知识。花名必填；季节、生长阶段和环境可选。"""
    query = " ".join(
        part for part in (flower, season, growth_stage, environment, "浇水 施肥") if part.strip()
    )
    passages = retrieve_flower_knowledge(query, k=3)
    if not passages:
        return f"知识库中没有找到{flower}的具体养护资料。请先询问用户环境和生长状态，并避免编造固定浇水或施肥频率。"

    context = "\n".join(passages)
    details = [f"花卉：{flower}"]
    if season:
        details.append(f"季节：{season}")
    if growth_stage:
        details.append(f"生长阶段：{growth_stage}")
    if environment:
        details.append(f"环境：{environment}")
    return "\n".join(details) + f"\n知识库资料：\n{context}"


def retrieve_flower_knowledge(query: str, k: int = 3) -> list[str]:
    """Return relevant knowledge passages for chat or image identification results."""
    normalized_query = " ".join(query.split()).casefold()
    if not normalized_query:
        return []
    key = cache_key(
        "flower-knowledge",
        {"query": normalized_query, "k": k, "version": KNOWLEDGE_CACHE_VERSION},
    )
    cached = cache_get(key)
    if isinstance(cached, list) and all(isinstance(item, str) for item in cached):
        return cached

    try:
        results = vector_db.similarity_search(query, k=k)
        passages = [result.page_content for result in results if getattr(result, "page_content", None)]
        cache_set(key, passages, KNOWLEDGE_CACHE_TTL_SECONDS)
        return passages
    except Exception:
        logger.exception("Flower knowledge retrieval failed")
        return []
