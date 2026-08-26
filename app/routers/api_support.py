"""AI Support Assistant proxy and embeddable widget."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.services.ai_assistant_service import check_support_bot_health, query_support_bot

router = APIRouter(tags=["AI Support Assistant"])


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


@router.post("/api/ask")
async def ask_assistant(payload: AskRequest, request: Request):
    """Query sovereign support assistant with leak protection."""
    return await query_support_bot(payload.query, request=request)


@router.get("/api/ask/health")
async def assistant_health():
    """Check support bot upstream health."""
    return await check_support_bot_health()


@router.get("/support-widget.js", response_class=PlainTextResponse)
async def support_widget():
    """Embeddable support assistant widget script without innerHTML sinks."""
    js = r"""
(function(){
  var container = document.createElement('div');
  container.className = 'au-widget';
  container.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;font-family:system-ui;';

  var btn = document.createElement('button');
  btn.textContent = "💬 I\'m AU — Ask Assistant";
  btn.style.cssText = 'background:#0f766e;color:#fff;border:0;border-radius:24px;padding:10px 18px;font-weight:700;cursor:pointer;box-shadow:0 4px 14px rgba(15,118,110,.35);';

  btn.onclick = function() {
    var q = prompt("I\'m AU. Ask about Hardonia products, security, or deployment:");
    if (q) {
      btn.textContent = 'Thinking…';
      fetch('/api/ask', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query: q})
      }).then(function(r){ return r.json(); })
      .then(function(res){
        btn.textContent = "💬 I\'m AU — Ask Assistant";
        alert(res.answer || res.escalation || 'No answer available.');
      }).catch(function(){
        btn.textContent = "💬 I\'m AU — Ask Assistant";
        alert('Assistant temporarily unavailable.');
      });
    }
  };
  container.appendChild(btn);
  document.body.appendChild(container);
})();
"""
    return Response(js, media_type="application/javascript")


@router.get("/support", response_class=HTMLResponse)
async def support_page():
    """Support portal."""
    html = """<!doctype html><html><body style='font-family:sans-serif;padding:3rem;background:#f5f1e8'>
<h1>Sovereign Support Portal</h1>
<p>For urgent operator assistance, hardware procurement, or SLA support:</p>
<p>Email: <a href='mailto:support@aiautomatedsystems.ca'>support@aiautomatedsystems.ca</a></p>
<p><a href='/'>← Return to Storefront</a></p>
</body></html>"""
    return HTMLResponse(html)
