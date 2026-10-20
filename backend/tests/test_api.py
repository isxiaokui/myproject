import asyncio
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from langchain_core.messages import AIMessage
from PIL import Image

from app import main


class FakeMessageChunk:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def __add__(self, other):
        return FakeMessageChunk(
            self.content + other.content,
            self.tool_calls + other.tool_calls,
        )

def fake_stream(*chunks):
    async def stream(_messages, _deadline):
        for chunk in chunks:
            yield chunk

    return stream(None, None)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"AUTH_SECRET_KEY": "test-secret-" + "x" * 40})
        self.env.start()
        self.db_startup = patch.object(main, "init_history_db")
        self.db_startup.start()
        self.user = {"id": "user-1", "username": "gardener"}
        self.token = main.create_access_token(self.user["id"])
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.db_startup.stop()
        self.env.stop()

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_auth_required_for_conversation_list(self):
        with patch.object(main, "init_history_db"):
            response = self.client.get("/conversations")
        self.assertEqual(response.status_code, 401)

    def test_register_creates_user_and_returns_token(self):
        with patch.object(main, "init_history_db"), patch.object(
            main, "create_user", return_value=self.user
        ):
            response = self.client.post(
                "/auth/register", json={"username": "Gardener", "password": "password123"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"], self.user)
        self.assertEqual(main.decode_access_token(response.json()["token"]), "user-1")

    def test_login_is_rate_limited(self):
        with patch.dict(
            os.environ,
            {"AUTH_LOGIN_RATE_LIMIT": "1", "AUTH_RATE_WINDOW_SECONDS": "60"},
        ), patch("app.rate_limit.cache_increment", return_value=None), patch.object(
            main, "get_user_by_username", return_value=None
        ):
            first = self.client.post(
                "/auth/login", json={"username": "gardener", "password": "password123"}
            )
            second = self.client.post(
                "/auth/login", json={"username": "gardener", "password": "password123"}
            )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second.headers)

    def test_image_request_body_limit_rejects_oversized_content_length(self):
        response = self.client.post(
            "/identify-flower",
            headers={"Content-Length": str(main.MAX_IMAGE_UPLOAD_BYTES + 64 * 1024 + 1)},
        )
        self.assertEqual(response.status_code, 413)

    def test_conversation_list_is_scoped_to_authenticated_user(self):
        with patch.object(main, "init_history_db"), patch.object(
            main, "list_conversations", return_value=[{"id": "c-1", "title": "月季"}]
        ) as list_conversations:
            response = self.client.get("/conversations", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], "c-1")
        list_conversations.assert_called_once_with("user-1")

    def test_chat_saves_user_and_assistant_messages(self):
        saved = []
        with patch.object(main, "init_history_db"), patch.object(
            main, "create_conversation", return_value={"id": "c-2"}
        ), patch.object(main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)), patch.object(
            main, "_invoke_chat_model", return_value=AIMessage(content="月季喜欢充足光照。")
        ):
            response = self.client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"msg": [{"role": "user", "content": "月季怎么养？"}]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["message"], "月季喜欢充足光照。")
        self.assertEqual([entry[1] for entry in saved], ["user", "assistant"])

    def test_chat_timeout_is_returned_as_gateway_error_and_saved(self):
        saved = []
        with patch.object(main, "create_conversation", return_value={"id": "c-timeout"}), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(main, "_invoke_chat_model", side_effect=TimeoutError("deadline exceeded")), patch.object(
            main.logger, "exception"
        ):
            response = self.client.post(
                "/chat",
                headers=self.auth_headers(),
                json={"msg": [{"role": "user", "content": "月季怎么养？"}]},
            )
        self.assertEqual(response.status_code, 502)
        self.assertTrue(saved[-1][3]["isError"])

    def test_chat_routes_authorized_location_tool_call(self):
        saved = []
        tool_response = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_weather",
                    "args": {"use_current_location": True, "days": 3},
                    "id": "weather-call-1",
                    "type": "tool_call",
                }
            ],
        )
        with patch.object(main, "create_conversation", return_value={"id": "c-weather"}), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(main, "_invoke_chat_model", side_effect=[tool_response, AIMessage(content="未来天气晴。")]), patch.object(
            main, "get_weather_by_coordinates", return_value="未来三天晴"
        ) as weather:
            response = self.client.post(
                "/chat",
                headers=self.auth_headers(),
                json={
                    "msg": [{"role": "user", "content": "我这里未来几天天气如何？"}],
                    "location": {"latitude": 31.2, "longitude": 121.4},
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["message"], "未来天气晴。")
        weather.assert_called_once_with(latitude=31.2, longitude=121.4, days=3)
        self.assertEqual(saved[-1][2], "未来天气晴。")

    def test_chat_stream_sends_tokens_and_done_sentinel(self):
        saved = []
        stream = fake_stream(FakeMessageChunk("月季"), FakeMessageChunk("喜欢充足光照。"))
        with patch.object(main, "create_conversation", return_value={"id": "c-stream"}), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(main, "_stream_chat_model", side_effect=[stream]):
            response = self.client.post(
                "/chat/stream",
                headers=self.auth_headers(),
                json={"msg": [{"role": "user", "content": "月季怎么养？"}]},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"type": "token"', response.text)
        self.assertIn('"text": "月季"', response.text)
        self.assertIn('"type": "done"', response.text)
        self.assertTrue(response.text.rstrip().endswith("data: [DONE]"))
        self.assertEqual(saved[-1][2], "月季喜欢充足光照。")

    def test_chat_stream_sends_only_recent_messages_to_model(self):
        saved = []
        captured = []

        async def capture_context(messages, _deadline):
            captured.extend(messages)
            yield FakeMessageChunk("回答")

        request_messages = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"历史消息{index}"}
            for index in range(24)
        ]
        with patch.object(main, "create_conversation", return_value={"id": "c-context"}), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(main, "_stream_chat_model", new=capture_context):
            response = self.client.post(
                "/chat/stream",
                headers=self.auth_headers(),
                json={"msg": request_messages},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(captured), 21)
        self.assertEqual(captured[1].content, "历史消息4")
        self.assertEqual(captured[-1].content, "历史消息23")

    def test_chat_stream_does_not_expose_tool_call_metadata(self):
        saved = []
        tool_chunk = FakeMessageChunk(
            tool_calls=[
                {
                    "name": "search_flower_knowledge",
                    "args": {"query": "月季内部参数"},
                    "id": "tool-call-secret",
                }
            ]
        )
        first_stream = fake_stream(tool_chunk)
        second_stream = fake_stream(FakeMessageChunk("月季喜光。"))
        mock_tool = SimpleNamespace(invoke=Mock(return_value="月季养护资料"))
        with patch.object(main, "create_conversation", return_value={"id": "c-tool"}), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(
            main, "_stream_chat_model", side_effect=[first_stream, second_stream]
        ), patch.dict(main.tools_by_name, {"search_flower_knowledge": mock_tool}):
            response = self.client.post(
                "/chat/stream",
                headers=self.auth_headers(),
                json={"msg": [{"role": "user", "content": "月季怎么养？"}]},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("search_flower_knowledge", response.text)
        self.assertNotIn("月季内部参数", response.text)
        self.assertNotIn("tool-call-secret", response.text)
        self.assertTrue(response.text.rstrip().endswith("data: [DONE]"))
        self.assertEqual(saved[-1][2], "月季喜光。")
        mock_tool.invoke.assert_called_once_with({"query": "月季内部参数"})

    def test_chat_stream_reports_model_error_and_terminates(self):
        saved = []

        async def failed_stream(_messages, _deadline):
            raise RuntimeError("mock provider failure")
            yield

        with patch.object(main, "create_conversation", return_value={"id": "c-error"}), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(main, "_stream_chat_model", side_effect=[failed_stream(None, None)]), patch.object(
            main.logger, "exception"
        ):
            response = self.client.post(
                "/chat/stream",
                headers=self.auth_headers(),
                json={"msg": [{"role": "user", "content": "月季怎么养？"}]},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"type": "error"', response.text)
        self.assertTrue(response.text.rstrip().endswith("data: [DONE]"))
        self.assertTrue(saved[-1][3]["isError"])

    def test_chat_stream_saves_partial_answer_on_client_disconnect(self):
        saved = []

        async def cancelled_stream(_messages, _deadline):
            yield FakeMessageChunk("已经生成的部分回答")
            raise asyncio.CancelledError()

        with patch.object(main, "_resolve_conversation", return_value="c-partial"), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(main, "_stream_chat_model", side_effect=[cancelled_stream(None, None)]):
            async def consume_stream():
                response = await main.chat_stream(
                    main.ChatRequest(msg=[main.ChatMessage(role="user", content="请回答")]),
                    Request(
                        {
                            "type": "http",
                            "method": "POST",
                            "path": "/chat/stream",
                            "headers": [],
                            "client": ("testclient", 1234),
                            "server": ("testserver", 80),
                            "scheme": "http",
                            "query_string": b"",
                        }
                    ),
                    "user-1",
                )
                iterator = response.body_iterator
                await iterator.__anext__()
                await iterator.__anext__()
                with self.assertRaises(asyncio.CancelledError):
                    await iterator.__anext__()

            asyncio.run(consume_stream())

        self.assertEqual(saved[-1][2], "已经生成的部分回答")
        self.assertTrue(saved[-1][3]["isPartial"])

    def test_chat_stream_persists_partial_answer_when_model_fails(self):
        saved = []

        async def failed_after_partial(_messages, _deadline):
            yield FakeMessageChunk("已经生成的部分回答")
            raise RuntimeError("mock provider failure")

        with patch.object(main, "create_conversation", return_value={"id": "c-partial-error"}), patch.object(
            main, "add_message", side_effect=lambda *args, **kwargs: saved.append(args)
        ), patch.object(
            main, "_stream_chat_model", side_effect=[failed_after_partial(None, None)]
        ), patch.object(main.logger, "exception"):
            response = self.client.post(
                "/chat/stream",
                headers=self.auth_headers(),
                json={"msg": [{"role": "user", "content": "月季怎么养？"}]},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("已经生成的部分回答", response.text)
        self.assertEqual(saved[-1][2], "已经生成的部分回答\n\n对话服务暂时不可用，请稍后重试。")
        self.assertTrue(saved[-1][3]["isPartial"])
        self.assertTrue(saved[-1][3]["isError"])

    def test_conversation_owned_by_another_user_is_not_accessible(self):
        with patch.object(main, "init_history_db"), patch.object(
            main, "get_conversation", return_value=None
        ) as get_conversation:
            response = self.client.get("/conversations/private", headers=self.auth_headers())
        self.assertEqual(response.status_code, 404)
        get_conversation.assert_called_once_with("user-1", "private")

    def test_image_validation_converts_supported_image_to_jpeg_data_url(self):
        image_bytes = io.BytesIO()
        Image.new("RGBA", (16, 12), (255, 0, 0, 128)).save(image_bytes, format="PNG")
        data_url = main._prepare_flower_image(image_bytes.getvalue())
        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))

    def test_image_validation_rejects_non_image_data(self):
        with self.assertRaises(main.HTTPException) as raised:
            main._prepare_flower_image(b"not an image")
        self.assertEqual(raised.exception.status_code, 400)

    def test_identification_parser_limits_candidates_and_marks_uncertain(self):
        parsed = main._parse_identification(
            '{"summary":"可能是月季","uncertain":false,"candidates":['
            '{"name":"月季","confidence":"较高","evidence":"重瓣"},'
            '{"name":"蔷薇","confidence":"中等"},'
            '{"name":"牡丹","confidence":"较低"},'
            '{"name":"多余","confidence":"较高"}]}'
        )
        self.assertTrue(parsed["uncertain"])
        self.assertEqual(len(parsed["candidates"]), 3)


if __name__ == "__main__":
    unittest.main()
