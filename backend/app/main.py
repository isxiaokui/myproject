import asyncio
import base64
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from app.cache import close_cache, init_cache
from app.tools import (
    get_weather,
    get_weather_by_coordinates,
    get_watering_fertilizing_plan,
    retrieve_flower_knowledge,
    search_flower_knowledge,
)
from app.history import (
    add_message,
    close_history_pool,
    conversation_exists,
    create_conversation,
    create_user,
    delete_conversation,
    get_conversation,
    get_user_by_id,
    get_user_by_username,
    init_history_db,
    list_conversations,
)
from app.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    validate_auth_secret,
    validate_username,
    verify_password,
)
from app.rate_limit import enforce_auth_rate_limit, enforce_resource_rate_limit

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logger = logging.getLogger(__name__)
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "60"))
CHAT_TOTAL_TIMEOUT_SECONDS = float(os.getenv("CHAT_TOTAL_TIMEOUT_SECONDS", "150"))
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "glm-4.5v")
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

@asynccontextmanager
async def lifespan(_app):
    try:
        validate_auth_secret()
        init_history_db()
        init_cache()
        yield
    finally:
        close_cache()
        close_history_pool()


app = FastAPI(lifespan=lifespan)


class ImageRequestBodyLimitMiddleware:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in {
            "/identify-flower",
            "/diagnose-plant",
        }:
            await self.application(scope, receive, send)
            return

        maximum = MAX_IMAGE_UPLOAD_BYTES + 64 * 1024
        content_length = dict(scope.get("headers", [])).get(b"content-length")
        if content_length:
            try:
                if int(content_length) > maximum:
                    await JSONResponse(
                        {"detail": "图片请求体过大，单张图片不能超过 8 MB。"},
                        status_code=413,
                    )(scope, receive, send)
                    return
            except ValueError:
                pass

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > maximum:
                await JSONResponse(
                    {"detail": "图片请求体过大，单张图片不能超过 8 MB。"},
                    status_code=413,
                )(scope, receive, send)
                return
            more_body = message.get("more_body", False)

        sent = False

        async def replay_body():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.application(scope, replay_body, send)


app.add_middleware(ImageRequestBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

llm = ChatOpenAI(
    temperature=0.6,
    model=MULTIMODAL_MODEL,
    timeout=MODEL_TIMEOUT_SECONDS,
    max_retries=0,
    openai_api_key=os.getenv("ZAI_API_KEY"),
    openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
)
identify_llm = llm.bind(temperature=0.1)
tools = [get_weather, search_flower_knowledge, get_watering_fertilizing_plan]
tools_by_name = {registered_tool.name: registered_tool for registered_tool in tools}
MAX_TOOL_ROUNDS = 5
MAX_CONTEXT_MESSAGES = 20
MAX_REQUEST_MESSAGES = 100
MAX_MESSAGE_CHARACTERS = 12_000
MAX_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 2048


def _invoke_chat_model(messages, deadline: float):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("聊天处理超过总时限。")

    request_llm = ChatOpenAI(
        temperature=0.6,
        model=MULTIMODAL_MODEL,
        timeout=min(MODEL_TIMEOUT_SECONDS, remaining),
        max_retries=0,
        openai_api_key=os.getenv("ZAI_API_KEY"),
        openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
    )
    return request_llm.bind_tools(tools).invoke(messages)


async def _stream_chat_model(messages, deadline: float):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("聊天处理超过总时限。")

    request_llm = ChatOpenAI(
        temperature=0.6,
        model=MULTIMODAL_MODEL,
        timeout=min(MODEL_TIMEOUT_SECONDS, remaining),
        max_retries=0,
        openai_api_key=os.getenv("ZAI_API_KEY"),
        openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
    ).bind_tools(tools)
    async with asyncio.timeout(remaining):
        async for chunk in request_llm.astream(messages):
            yield chunk


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARACTERS)


class ChatLocation(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ChatRequest(BaseModel):
    msg: list[ChatMessage] = Field(min_length=1, max_length=MAX_REQUEST_MESSAGES)
    conversation_id: str | None = None
    location: ChatLocation | None = None
    location_unavailable: bool = False


class ConversationCreate(BaseModel):
    title: str = "新对话"


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


def get_current_user_id(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录。")
    user_id = decode_access_token(authorization[7:].strip())
    if not user_id:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录。")
    return user_id


def _resolve_conversation(owner_id: str, conversation_id: str | None, title: str) -> str:
    if conversation_id:
        if not conversation_exists(owner_id, conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在或已删除。")
        return conversation_id
    return create_conversation(owner_id, title)["id"]


def _identification_history_text(identification: dict) -> str:
    lines = [identification["summary"]]
    lines.extend(
        f"{item['name']}（{item['confidence']}把握）：{item['evidence']}"
        for item in identification["candidates"]
    )
    lines.extend(
        f"{item['flower']}养护资料：{passage}"
        for item in identification["knowledge"]
        for passage in item["passages"]
    )
    if identification["clarifying_question"]:
        lines.append(identification["clarifying_question"])
    return "\n".join(lines)


def _prepare_flower_image(raw: bytes) -> str:
    """Validate, orient, resize, and encode an uploaded image as a JPEG data URL."""
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(
                    status_code=415,
                    detail="仅支持 JPEG、PNG 或 WebP 图片。",
                )
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="图片分辨率过高，请上传较小的图片。")

            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")

            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            output = BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="无法读取图片，请选择有效的 JPEG、PNG 或 WebP 文件。") from exc


def _response_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _parse_identification(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回有效的识别结果。")
    result = json.loads(content[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("模型返回的识别结果格式不正确。")
    raw_candidates = result.get("candidates", [])
    candidates = []
    if isinstance(raw_candidates, list):
        for item in raw_candidates[:3]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            confidence = str(item.get("confidence") or "不确定")[:20]
            if confidence not in {"较高", "中等", "较低"}:
                confidence = "不确定"
            candidates.append(
                {
                    "name": str(item["name"])[:40],
                    "confidence": confidence,
                    "evidence": str(item.get("evidence") or "")[:500],
                }
            )
    uncertain = result.get("uncertain")
    if not isinstance(uncertain, bool) or not candidates:
        uncertain = len(candidates) != 1
    uncertain = uncertain or len(candidates) != 1 or candidates[0]["confidence"] != "较高"
    return {
        "summary": str(result.get("summary") or "识别完成。")[:500],
        "uncertain": uncertain,
        "candidates": candidates,
        "clarifying_question": str(result.get("clarifying_question") or "")[:300],
    }


def _parse_disease_diagnosis(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回有效的诊断结果。")
    result = json.loads(content[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("模型返回的诊断结果格式不正确。")

    raw_issues = result.get("issues", [])
    issues = []
    if isinstance(raw_issues, list):
        for item in raw_issues[:3]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            confidence = str(item.get("confidence") or "不确定")[:20]
            if confidence not in {"较高", "中等", "较低"}:
                confidence = "不确定"
            issues.append(
                {
                    "name": str(item["name"])[:80],
                    "confidence": confidence,
                    "evidence": str(item.get("evidence") or "")[:500],
                }
            )

    raw_recommendations = result.get("recommendations", [])
    recommendations = (
        [str(item)[:300] for item in raw_recommendations[:6] if item]
        if isinstance(raw_recommendations, list)
        else []
    )
    uncertain = result.get("uncertain")
    if not isinstance(uncertain, bool):
        uncertain = True
    uncertain = uncertain or any(item["confidence"] != "较高" for item in issues)
    return {
        "summary": str(result.get("summary") or "诊断完成。")[:500],
        "uncertain": uncertain,
        "issues": issues,
        "recommendations": recommendations,
        "clarifying_question": str(result.get("clarifying_question") or "")[:300],
    }


def _disease_history_text(diagnosis: dict) -> str:
    lines = [diagnosis["summary"]]
    lines.extend(
        f"{item['name']}（{item['confidence']}把握）：{item['evidence']}"
        for item in diagnosis["issues"]
    )
    lines.extend(f"建议：{recommendation}" for recommendation in diagnosis["recommendations"])
    if diagnosis["clarifying_question"]:
        lines.append(diagnosis["clarifying_question"])
    return "\n".join(lines)


@app.get("/")
def health_check():
    return {"message": "Flower assistant API is running"}


@app.post("/auth/register")
def register(request: Credentials, http_request: Request):
    enforce_auth_rate_limit(http_request, "register")
    try:
        username = validate_username(request.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user = create_user(username, hash_password(request.password))
    if user is None:
        raise HTTPException(status_code=409, detail="这个用户名已被注册。")
    return {"token": create_access_token(user["id"]), "user": user}


@app.post("/auth/login")
def login(request: Credentials, http_request: Request):
    enforce_auth_rate_limit(http_request, "login")
    try:
        username = validate_username(request.username)
    except ValueError:
        raise HTTPException(status_code=401, detail="用户名或密码不正确。")
    user = get_user_by_username(username)
    if user is None or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码不正确。")
    return {
        "token": create_access_token(user["id"]),
        "user": {"id": user["id"], "username": user["username"]},
    }


@app.get("/auth/me")
def current_user(user_id: str = Depends(get_current_user_id)):
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在，请重新注册。")
    return user


@app.post("/conversations")
def new_conversation(request: ConversationCreate, user_id: str = Depends(get_current_user_id)):
    return create_conversation(user_id, request.title)


@app.get("/conversations")
def conversations(user_id: str = Depends(get_current_user_id)):
    return {"items": list_conversations(user_id)}


@app.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, user_id: str = Depends(get_current_user_id)):
    result = get_conversation(user_id, conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在或已删除。")
    return result


@app.delete("/conversations/{conversation_id}")
def remove_conversation(conversation_id: str, user_id: str = Depends(get_current_user_id)):
    if not delete_conversation(user_id, conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在或已删除。")
    return {"deleted": True}


@app.post("/identify-flower")
def identify_flower(
    http_request: Request,
    image: UploadFile = File(...),
    question: str = Form(default=""),
    conversation_id: str | None = Form(default=None),
    user_id: str = Depends(get_current_user_id),
):
    enforce_resource_rate_limit(http_request, "image", user_id)
    raw_image = image.file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    image.file.close()
    if not raw_image:
        raise HTTPException(status_code=400, detail="请选择一张图片。")
    if len(raw_image) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 8 MB。")

    image_data_url = _prepare_flower_image(raw_image)
    resolved_conversation_id = _resolve_conversation(
        user_id,
        conversation_id,
        question.strip() or "图片识花",
    )
    user_content = f"请识别这张图片：{question.strip()}" if question.strip() else "请识别这张花卉图片。"
    add_message(resolved_conversation_id, "user", user_content)
    prompt = (
        "请识别图片中最明显的一种花卉，优先判断常见类别或物种。仔细观察花朵、叶片、花序和整体形态。"
        "除非图片有足够独特且明确的证据，不要断言具体栽培品种；不要把常见类别判断包装成精确品种结论。"
        "如果证据不足、图片模糊或画面里没有花卉，不要猜测；可以返回 0 到 3 个候选。"
        "confidence 只能填写‘较高’、‘中等’或‘较低’，它是定性判断，不是统计概率。"
        "只返回合法 JSON，不要使用 Markdown 代码块，格式为："
        '{"summary":"简短结论","uncertain":true,'
        '"candidates":[{"name":"花名","confidence":"中等","evidence":"可见特征"}],'
        '"clarifying_question":"如需要，询问一项有助于区分品种的问题；否则留空"}。'
        f"用户补充说明：{question.strip()[:500] or '无'}"
    )

    try:
        response = identify_llm.invoke(
            [
                SystemMessage(content="你是谨慎的花卉图像识别助手。不能仅凭图片外观编造精确品种。"),
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ]
                ),
            ]
        )
        identification = _parse_identification(_response_text(response.content))
    except (ValueError, json.JSONDecodeError) as exc:
        add_message(
            resolved_conversation_id,
            "assistant",
            "识别模型未能返回可解析的结果，请重试。",
            {"isError": True},
        )
        raise HTTPException(status_code=502, detail="识别模型未能返回可解析的结果，请重试。") from exc
    except Exception as exc:
        logger.exception("Flower vision model request failed")
        add_message(
            resolved_conversation_id,
            "assistant",
            "图片识别服务调用失败，请检查视觉模型配置和 API Key。",
            {"isError": True},
        )
        raise HTTPException(
            status_code=502,
            detail="图片识别服务调用失败，请检查视觉模型配置和 API Key。",
        ) from exc

    knowledge = []
    candidates = identification["candidates"]
    if (
        not identification["uncertain"]
        and len(candidates) == 1
        and candidates[0]["confidence"] == "较高"
    ):
        try:
            passages = retrieve_flower_knowledge(f"{candidates[0]['name']} 花卉养护", k=2)
        except Exception:
            logger.exception("Flower knowledge lookup failed after image identification")
            passages = []
        if passages:
            knowledge.append({"flower": candidates[0]["name"], "passages": passages})

    result = {**identification, "knowledge": knowledge}
    add_message(
        resolved_conversation_id,
        "assistant",
        _identification_history_text(result),
        {"identification": result},
    )
    return {**result, "conversation_id": resolved_conversation_id}


@app.post("/diagnose-plant")
def diagnose_plant(
    http_request: Request,
    image: UploadFile = File(...),
    question: str = Form(default=""),
    conversation_id: str | None = Form(default=None),
    user_id: str = Depends(get_current_user_id),
):
    enforce_resource_rate_limit(http_request, "image", user_id)
    raw_image = image.file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    image.file.close()
    if not raw_image:
        raise HTTPException(status_code=400, detail="请选择一张图片。")
    if len(raw_image) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 8 MB。")

    image_data_url = _prepare_flower_image(raw_image)
    resolved_conversation_id = _resolve_conversation(
        user_id,
        conversation_id,
        question.strip() or "病虫害诊断",
    )
    user_content = f"请诊断这张植物图片：{question.strip()}" if question.strip() else "请检查这张植物图片的病虫害。"
    add_message(resolved_conversation_id, "user", user_content)
    prompt = (
        "检查图片中的植物是否有病害、虫害或明显生理问题。只根据清晰可见的症状判断，"
        "区分观察到的证据与推测；图片不足以判断时明确说明，并可给出最多 3 个候选问题。"
        "不要把单张照片的推测说成确诊，不要建议混用农药或给出未经核实的药剂剂量。"
        "若看不出明显问题，应明确说未见明显异常，不要臆造病虫害。"
        "只返回合法 JSON，不要使用 Markdown 代码块，格式为："
        '{"summary":"简短判断","uncertain":true,'
        '"issues":[{"name":"问题名称","confidence":"中等","evidence":"图片中的可见症状"}],'
        '"recommendations":["安全、可执行的观察或处理建议"],'
        '"clarifying_question":"如需补充信息，提出一个问题；否则留空"}。'
        f"用户补充说明：{question.strip()[:500] or '无'}"
    )

    try:
        response = identify_llm.invoke(
            [
                SystemMessage(content="你是谨慎的植物病虫害初步诊断助手。图片诊断不能替代现场检验。"),
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ]
                ),
            ]
        )
        diagnosis = _parse_disease_diagnosis(_response_text(response.content))
    except (ValueError, json.JSONDecodeError) as exc:
        add_message(
            resolved_conversation_id,
            "assistant",
            "诊断模型未能返回可解析的结果，请重试。",
            {"isError": True},
        )
        raise HTTPException(status_code=502, detail="诊断模型未能返回可解析的结果，请重试。") from exc
    except Exception as exc:
        logger.exception("Plant disease vision model request failed")
        add_message(
            resolved_conversation_id,
            "assistant",
            "病虫害诊断服务调用失败，请检查视觉模型配置和 API Key。",
            {"isError": True},
        )
        raise HTTPException(
            status_code=502,
            detail="病虫害诊断服务调用失败，请检查视觉模型配置和 API Key。",
        ) from exc

    add_message(
        resolved_conversation_id,
        "assistant",
        _disease_history_text(diagnosis),
        {"diagnosis": diagnosis},
    )
    return {**diagnosis, "conversation_id": resolved_conversation_id}


@app.post("/chat")
def chat(
    request: ChatRequest,
    http_request: Request,
    user_id: str = Depends(get_current_user_id),
):
    enforce_resource_rate_limit(http_request, "chat", user_id)
    latest_user_message = next(
        (message for message in reversed(request.msg) if message.role == "user"),
        None,
    )
    if latest_user_message is None:
        raise HTTPException(status_code=400, detail="消息列表中需要包含用户消息。")
    resolved_conversation_id = _resolve_conversation(
        user_id,
        request.conversation_id,
        latest_user_message.content,
    )
    add_message(resolved_conversation_id, "user", latest_user_message.content)

    location_context = (
        "本次请求有用户已授权的当前位置，可在用户询问本地天气且没有指定城市时调用天气工具，并设置 use_current_location=true。"
        "不要向用户透露经纬度。"
        if request.location
        else (
            "浏览器没有提供用户位置（可能是权限未授权或无法定位）；用户询问本地天气且没有指定城市时，"
            "请礼貌说明无法读取位置并询问城市名称，不要猜测位置。"
            if request.location_unavailable
            else "本次请求没有可用的用户位置；用户询问本地天气且没有指定城市时，请礼貌询问城市名称，不要猜测位置。"
        )
    )
    messages = [
        SystemMessage(
            content=(
                "你是专业养花助手，只回答花卉养护相关问题，也可以查询用户指定城市的天气。"
                "回答简洁明了；需要知识库、浇水施肥计划或天气信息时必须调用相应工具。"
                "制定计划时优先使用知识库资料，结合用户给出的季节、生长阶段和环境；资料不足时说明不确定，"
                "不要编造固定浇水频率、施肥剂量或无条件适用的日程。天气工具的 days=1 是实时天气，用户问未来几天时用 days=3。"
                "拒绝回答无关话题。"
                "用户明确指定城市时按该城市查询；仅在用户询问本地天气、没有指定城市时使用当前位置。"
                + location_context
            )
        )
    ]
    messages.extend(
        HumanMessage(content=message.content)
        if message.role == "user"
        else AIMessage(content=message.content)
        for message in request.msg[-MAX_CONTEXT_MESSAGES:]
    )

    deadline = time.monotonic() + CHAT_TOTAL_TIMEOUT_SECONDS
    try:
        response = _invoke_chat_model(messages, deadline)
        for _ in range(MAX_TOOL_ROUNDS):
            if not response.tool_calls:
                answer = _response_text(response.content)
                add_message(resolved_conversation_id, "assistant", answer)
                return {"message": answer, "conversation_id": resolved_conversation_id}

            messages.append(response)
            for tool_call in response.tool_calls:
                tool_name = tool_call.get("name")
                registered_tool = tools_by_name.get(tool_name)
                if registered_tool is None:
                    result = f"无法执行未注册的工具：{tool_name}"
                elif tool_name == "get_weather" and tool_call.get("args", {}).get("use_current_location"):
                    if request.location is None:
                        result = "没有获得用户位置，请询问用户要查询的城市。"
                    else:
                        result = get_weather_by_coordinates(
                            latitude=request.location.latitude,
                            longitude=request.location.longitude,
                            days=int(tool_call.get("args", {}).get("days", 1)),
                        )
                else:
                    try:
                        result = registered_tool.invoke(tool_call.get("args", {}))
                    except Exception as exc:
                        # Return tool failures to the model so it can explain them or recover.
                        result = f"工具 {tool_name} 执行失败：{exc}"
                messages.append(
                    ToolMessage(
                        content=str(result),
                        tool_call_id=tool_call["id"],
                    )
                )
            response = _invoke_chat_model(messages, deadline)
    except Exception as exc:
        logger.exception("Chat model request failed")
        add_message(
            resolved_conversation_id,
            "assistant",
            "对话服务暂时不可用，请稍后重试。",
            {"isError": True},
        )
        raise HTTPException(status_code=502, detail="对话服务暂时不可用，请稍后重试。") from exc

    add_message(
        resolved_conversation_id,
        "assistant",
        "模型连续请求工具次数过多，无法完成本次回答，请稍后重试。",
        {"isError": True},
    )
    raise HTTPException(
        status_code=502,
        detail="模型连续请求工具次数过多，无法完成本次回答，请稍后重试。",
    )


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    user_id: str = Depends(get_current_user_id),
):
    enforce_resource_rate_limit(http_request, "chat", user_id)
    latest_user_message = next(
        (message for message in reversed(request.msg) if message.role == "user"),
        None,
    )
    if latest_user_message is None:
        raise HTTPException(status_code=400, detail="消息列表中需要包含用户消息。")

    try:
        resolved_conversation_id = await asyncio.to_thread(
            _resolve_conversation,
            user_id,
            request.conversation_id,
            latest_user_message.content,
        )
        await asyncio.to_thread(
            add_message,
            resolved_conversation_id,
            "user",
            latest_user_message.content,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not prepare streaming chat history")
        raise HTTPException(status_code=503, detail="聊天历史暂时不可用，请稍后重试。") from exc

    location_context = (
        "本次请求有用户已授权的当前位置，可在用户询问本地天气且没有指定城市时调用天气工具，并设置 use_current_location=true。"
        "不要向用户透露经纬度。"
        if request.location
        else (
            "浏览器没有提供用户位置（可能是权限未授权或无法定位）；用户询问本地天气且没有指定城市时，"
            "请礼貌说明无法读取位置并询问城市名称，不要猜测位置。"
            if request.location_unavailable
            else "本次请求没有可用的用户位置；用户询问本地天气且没有指定城市时，请礼貌询问城市名称，不要猜测位置。"
        )
    )
    messages = [
        SystemMessage(
            content=(
                "你是专业养花助手，只回答花卉养护相关问题，也可以查询用户指定城市的天气。"
                "回答简洁明了；需要知识库、浇水施肥计划或天气信息时必须调用相应工具。"
                "制定计划时优先使用知识库资料，结合用户给出的季节、生长阶段和环境；资料不足时说明不确定，"
                "不要编造固定浇水频率、施肥剂量或无条件适用的日程。天气工具的 days=1 是实时天气，用户问未来几天时用 days=3。"
                "拒绝回答无关话题。"
                "用户明确指定城市时按该城市查询；仅在用户询问本地天气、没有指定城市时使用当前位置。"
                + location_context
            )
        )
    ]
    messages.extend(
        HumanMessage(content=message.content)
        if message.role == "user"
        else AIMessage(content=message.content)
        for message in request.msg[-MAX_CONTEXT_MESSAGES:]
    )

    async def event_stream():
        deadline = time.monotonic() + CHAT_TOTAL_TIMEOUT_SECONDS
        answer_parts = []
        tool_rounds = 0
        try:
            yield _sse_event({"type": "start", "conversation_id": resolved_conversation_id})
            while True:
                combined = None
                async for chunk in _stream_chat_model(messages, deadline):
                    combined = chunk if combined is None else combined + chunk
                    text = _response_text(chunk.content)
                    if text:
                        answer_parts.append(text)
                        yield _sse_event({"type": "token", "text": text})

                if combined is None:
                    raise RuntimeError("模型没有返回内容。")

                tool_calls = combined.tool_calls
                if not tool_calls:
                    answer = "".join(answer_parts)
                    await asyncio.to_thread(
                        add_message,
                        resolved_conversation_id,
                        "assistant",
                        answer,
                    )
                    yield _sse_event({"type": "done", "conversation_id": resolved_conversation_id})
                    yield "data: [DONE]\n\n"
                    return

                if tool_rounds >= MAX_TOOL_ROUNDS:
                    error_message = "模型连续请求工具次数过多，无法完成本次回答，请稍后重试。"
                    await asyncio.to_thread(
                        add_message,
                        resolved_conversation_id,
                        "assistant",
                        error_message,
                        {"isError": True},
                    )
                    yield _sse_event({"type": "error", "message": error_message})
                    yield "data: [DONE]\n\n"
                    return

                messages.append(AIMessage(content=combined.content, tool_calls=tool_calls))
                tool_rounds += 1
                for tool_call in tool_calls:
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("args")
                    if not isinstance(tool_args, dict):
                        tool_args = {}
                    try:
                        registered_tool = tools_by_name.get(tool_name)
                        if registered_tool is None:
                            result = f"无法执行未注册的工具：{tool_name}"
                        elif tool_name == "get_weather" and tool_args.get("use_current_location"):
                            if request.location is None:
                                result = "没有获得用户位置，请询问用户要查询的城市。"
                            else:
                                result = await asyncio.to_thread(
                                    get_weather_by_coordinates,
                                    latitude=request.location.latitude,
                                    longitude=request.location.longitude,
                                    days=int(tool_args.get("days", 1)),
                                )
                        else:
                            result = await asyncio.to_thread(registered_tool.invoke, tool_args)
                    except Exception:
                        logger.exception("Chat tool execution failed: %s", tool_name)
                        result = f"工具 {tool_name} 暂时不可用，请根据已有信息回答或提示用户稍后重试。"
                    messages.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call["id"],
                        )
                    )
        except asyncio.CancelledError:
            logger.info("Streaming chat client disconnected")
            partial_answer = "".join(answer_parts)
            if partial_answer:
                try:
                    save_task = asyncio.create_task(
                        asyncio.to_thread(
                            add_message,
                            resolved_conversation_id,
                            "assistant",
                            partial_answer,
                            {"isPartial": True},
                        )
                    )
                    await asyncio.shield(save_task)
                except Exception:
                    logger.exception("Could not save partial streaming answer")
            raise
        except Exception as exc:
            logger.exception("Streaming chat model request failed")
            error_message = "对话服务暂时不可用，请稍后重试。"
            partial_answer = "".join(answer_parts)
            stored_answer = f"{partial_answer}\n\n{error_message}" if partial_answer else error_message
            try:
                await asyncio.to_thread(
                    add_message,
                    resolved_conversation_id,
                    "assistant",
                    stored_answer,
                    {"isError": True, "isPartial": bool(partial_answer)},
                )
            except Exception:
                logger.exception("Could not save streaming chat error")
            yield _sse_event({"type": "error", "message": error_message})
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
