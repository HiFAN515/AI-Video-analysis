"""Tiny OpenAI-compatible mock server for local UI testing (no API key required).

Usage:  .venv/bin/python scripts/mock_llm.py
Then set Base URL to http://127.0.0.1:8001/v1, model to mock, any API Key.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="VideoMind Mock LLM")

ANALYSIS = """## 核心主张
视频围绕“竹子开花”展开：先澄清“六十年一开花”的民间说法，再解释不同竹种花期差异，最后说明毛竹“开花即死”的生存策略。整体是科普叙事，观点清晰，但部分表述把研究结论与民间经验混在一起。

## 事实核查
- **陈述**：民间传说竹子六十年一开花，源于古籍“竹六十年一易根，生花必结实”的记载。[00:00] **判断**：可信（作为文献线索）。**理由**：古籍确有类似记述，但“六十年”是经验概括，不是所有竹种的规律。
- **陈述**：现代研究显示毛竹、箭竹开花约在四十至六十年。[00:35] **判断**：大体可信。**理由**：与主流竹类学研究一致，但具体周期受竹种、气候与经营方式影响。
- **陈述**：全球约一千五百种竹子，花期千差万别。[00:35] **判断**：可信。**理由**：竹亚科种类数量级正确，且确实存在几到上百年不等的花期。
- **陈述**：开花后竹子一定死亡。[00:48] **判断**：可能错误（绝对化）。**理由**：视频自己也提到池竹、花竹等开花后仍可存活，说明“开花即死”只适用于部分竹种。
- **陈述**：大面积同步开花是为了让后代在饥荒中生存。[01:19] **判断**：存疑（目的论表述）。**理由**：同步开花可用进化机制解释，但“为了让后代生存”是拟人化目的论，缺少直接证据。

## 逻辑与论证
1. **以偏概全**：用毛竹的“开花即死”概括所有竹子，随后又用反例修正，前后口径不一致。
2. **目的论语言**：“为了让后代在饥荒中生存”“启动智慧功能”把自然选择的结果写成有意图的设计，容易误导。
3. **证据强弱不分**：古籍记载、现代研究、个人观察被并列陈述，没有标注证据等级。
4. **概念混淆**：“开花周期”与“同步开花机制”是两个问题，视频在后半段才区分。

## 补充视角
- **生态学**：竹子大面积同步开花通常与“捕食者饱和”假说有关，即一次产生大量种子，让种子捕食者吃不完，从而提高幼苗存活率。
- **农业影响**：毛竹开花死亡会改变林分结构，对竹材产业和以大熊猫为代表的竹食动物都有影响，值得单独展开。
- **气候因素**：干旱、火灾等胁迫可能触发或提前开花，视频只提到“绝境”，但未给出机制。
- **争议点**：开花后是否全部死亡，不同竹种差异很大，学界对“一次结实即死”的普适性仍有讨论。

## 值得继续追问
- 毛竹的开花周期是否存在可预测的遗传机制？
- 大面积同步开花后，竹林恢复需要多长时间？
- 视频中的“六十年”说法，最早可追溯到哪部古籍？
- 如果气候变暖，竹子花期会提前吗？
"""
CHAT = """根据转录文稿，视频在 **[00:00]** 先提出“竹子六十年一开花”的民间说法，随后在 **[00:35]** 补充了不同竹种花期的差异。

我整理一下：

1. **[00:00]**：作者把“六十年一开花”追溯到古籍记载，并说明这是经验性说法，不是所有竹子的规律。
2. **[00:35]**：视频提到毛竹、箭竹大约四十至六十年开花，全球约 1500 种竹子花期差异很大。
3. **[00:48]**：作者说“开花即死”并非绝对，池竹、花竹等开花后仍能存活；但毛竹开花后通常死亡。
4. **[01:19]**：关于大面积同步开花，视频用“让后代在饥荒中生存”来解释，这更接近“捕食者饱和”假说的通俗表述，但“为了”一词带有目的论色彩。

需要提醒的是：文稿由 ASR 生成，像“毛竹”“箭竹”这类专有名词可能被识别成同音字；如果你要引用具体竹种，建议对照原视频 [00:35] 附近的画面确认。
"""


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    last_user = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            last_user = str(message.get("content") or "")
            break
    system = str(messages[0].get("content") or "") if messages else ""
    is_test = "只回复两个字" in last_user or "连通性" in last_user
    is_analysis = "内容审校专家" in system or "严格根据用户给出的转录文稿" in system or "擅长把长视频" in system
    content = "正常" if is_test else (ANALYSIS if is_analysis else CHAT)
    model = body.get("model") or "mock"

    if not body.get("stream"):
        return JSONResponse(
            {
                "id": "mock-1",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
        )

    async def generate():
        step = 24
        for index in range(0, len(content), step):
            chunk = content[index : index + step]
            payload = {
                "id": "mock-1",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.01)
        yield 'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
