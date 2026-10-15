from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from tools import get_weather
load_dotenv()
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], 
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
llm = ChatOpenAI(
    temperature=0.6,
    model="glm-5.3",
    openai_api_key=os.getenv("ZAI_API_KEY"),
    openai_api_base="https://open.bigmodel.cn/api/paas/v4/"
)
llm_with_tools = llm.bind_tools([get_weather])
class requestBody(BaseModel):
    msg: list[dict]
@app.get("/")
def fun():
    return {"message": "Hello World"}
@app.post("/chat")
def chat(request: requestBody):
    messages = [
        SystemMessage(content="你是专业养花助手，只回答花卉养护相关问题也可以查询天气并且简洁明了，拒绝回答无关话题。")
        # HumanMessage(content=request.messages)
    ]+ request.msg
    response = llm_with_tools.invoke(messages)
    print(response)
    if response.tool_calls:
        messages.append(response)
        tool_response = response.tool_calls[0]
        result = get_weather.invoke({"city": tool_response["args"]["city"]})
        messages.append(ToolMessage(content=result, tool_call_id=tool_response["id"])) 
        response = llm_with_tools.invoke(messages)
    return {"message": response.content}