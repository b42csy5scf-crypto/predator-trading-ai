from fastapi.testclient import TestClient

from predator_trading_ai import admin_api


class DummyRunner:
    def __init__(self, settings) -> None:
        self.settings = settings

    async def build_and_send(self):
        class Result:
            report = "Predator report"
            sent = True

        return Result()


def test_admin_report_endpoint_runs_and_sends(monkeypatch) -> None:
    monkeypatch.setattr(admin_api, "PerformanceReportRunner", DummyRunner)
    client = TestClient(admin_api.app)
    response = client.post("/admin/report")
    assert response.status_code == 200
    assert response.json()["telegram_sent"] is True
    assert response.json()["report_chars"] == len("Predator report")
