from fastapi import FastAPI, Header, HTTPException

from predator_trading_ai.config import get_settings
from predator_trading_ai.reports.report_runner import PerformanceReportRunner


app = FastAPI(title="Predator Trading AI Admin")


@app.post("/admin/report")
async def run_report(x_admin_token: str | None = Header(default=None)) -> dict:
    settings = get_settings()
    expected = getattr(settings, "admin_report_token", None)
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    result = await PerformanceReportRunner(settings).build_and_send()
    return {
        "ok": True,
        "telegram_sent": result.sent,
        "report_chars": len(result.report),
    }
