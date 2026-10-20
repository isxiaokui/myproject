import os
import unittest
from unittest.mock import Mock, patch

import requests

from app import tools


class WeatherToolTests(unittest.TestCase):
    def test_city_lookup_selects_exact_match_from_nested_payload(self):
        response = Mock()
        response.json.return_value = {
            "result": {"data": [{"name": "杭州市", "areacode": "330100"}]}
        }
        with patch.dict(os.environ, {"WEATHER_API_KEY": "test-key"}), patch.object(
            tools.requests, "get", return_value=response
        ) as get:
            code, name = tools._find_city_code("杭州")

        self.assertEqual((code, name), ("330100", "杭州市"))
        self.assertEqual(get.call_args.kwargs["timeout"], tools.REQUEST_TIMEOUT_SECONDS)

    def test_city_lookup_rejects_ambiguous_matches(self):
        response = Mock()
        response.json.return_value = {
            "data": [
                {"name": "朝阳区", "areacode": "110105", "path": "北京市/朝阳区"},
                {"name": "朝阳区", "areacode": "210104", "path": "辽宁省/沈阳市/朝阳区"},
            ]
        }
        with patch.dict(os.environ, {"WEATHER_API_KEY": "test-key"}), patch.object(
            tools.requests, "get", return_value=response
        ), self.assertRaisesRegex(ValueError, "多个地区"):
            tools._find_city_code("朝阳区")

    def test_forecast_formats_records_and_limits_number_of_days(self):
        payload = {
            "result": {
                "location": {"name": "杭州"},
                "daily": [
                    {"date": "2026-10-16", "text_day": "晴", "text_night": "多云", "low": 17, "high": 25},
                    {"fxDate": "2026-10-17", "textDay": "小雨", "tempMin": 16, "tempMax": 22},
                ],
            }
        }
        formatted = tools._format_forecast_response(payload, "未知", days=1)
        self.assertIn("杭州未来 1 天天气预报", formatted)
        self.assertIn("晴，夜间多云", formatted)
        self.assertIn("17–25℃", formatted)
        self.assertNotIn("2026-10-17", formatted)

    def test_weather_timeout_returns_user_facing_message(self):
        with patch.dict(os.environ, {"WEATHER_API_KEY": "test-key"}), patch.object(
            tools.requests, "get", side_effect=requests.Timeout
        ):
            result = tools.get_weather_by_coordinates(31.2, 121.4)
        self.assertIn("请求超时", result)

    def test_current_weather_missing_temperature_is_handled(self):
        result = tools._format_weather_response(
            {"result": {"location": {"name": "杭州"}, "realtime": {"text": "晴"}}}, "未知"
        )
        self.assertIn("暂无温度数据", result)


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_knowledge_results_are_cached_and_reused(self):
        document = type("Document", (), {"page_content": "月季需要充足光照。"})()
        with patch.object(tools, "cache_get", side_effect=[None, [document.page_content]]), patch.object(
            tools, "cache_set"
        ) as cache_set, patch.object(
            tools.vector_db, "similarity_search", return_value=[document]
        ) as search:
            first = tools.retrieve_flower_knowledge("  月季   光照 ")
            second = tools.retrieve_flower_knowledge("月季 光照")

        self.assertEqual(first, [document.page_content])
        self.assertEqual(second, first)
        search.assert_called_once_with("  月季   光照 ", k=3)
        cache_set.assert_called_once()
        self.assertEqual(cache_set.call_args.args[2], tools.KNOWLEDGE_CACHE_TTL_SECONDS)

    def test_knowledge_vector_store_failure_returns_empty_results(self):
        with patch.object(tools, "cache_get", return_value=None), patch.object(
            tools.vector_db, "similarity_search", side_effect=RuntimeError("vector store unavailable")
        ), patch.object(tools.logger, "exception"):
            self.assertEqual(tools.retrieve_flower_knowledge("月季"), [])


if __name__ == "__main__":
    unittest.main()
