from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from app.database import get_db
from app.services.master_ai import master_chat

router = APIRouter()


@router.post("/chat/ask")
async def chat_ask(message: str = Form(...), sender: str = Form("Gaga"), model: str = Form("auto")):
    """AJAX 发送：返回 AI 回复 JSON，前端无刷新更新对话"""
    result = await master_chat(message, sender, model)
    return JSONResponse({
        "reply": result["reply"],
        "action": result.get("action", "chat"),
        "model": result.get("model", ""),
        "cost": result.get("cost", 0),
    })


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM messages WHERE channel = 'master_ai' ORDER BY created_at ASC LIMIT 100"
        )
        messages = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "chat.html", {"request": request, "messages": messages}
    )


@router.post("/chat/send")
async def chat_send(request: Request, message: str = Form(...), sender: str = Form("我"), model: str = Form("auto")):
    result = await master_chat(message, sender, model)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM messages WHERE channel = 'master_ai' ORDER BY created_at ASC LIMIT 100"
        )
        messages = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    return request.app.state.templates.TemplateResponse(
        request, "chat.html", {"request": request, "messages": messages, "last_result": result}
    )


@router.post("/chat/clear")
async def chat_clear():
    db = await get_db()
    try:
        await db.execute("DELETE FROM messages WHERE channel = 'master_ai'")
        await db.commit()
    finally:
        await db.close()
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/chat", status_code=303)
