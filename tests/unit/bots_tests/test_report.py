import asyncio

from seleric_swarm.services.bots import eda, report


def _steps(bands):
    return [{"kind": "tool_return", "tool": eda.TOOL, "bands": {"ad_spend": {"today_so_far": 1.0, "p10": 9.0, "p50": 9.5, "p90": 10.0}}},
            {"kind": "tool_return", "tool": eda.TOOL, "bands": bands}]


def test_code_checks_use_latest_eda_and_read_band():
    checks = report.code_checks(_steps({
        "ad_spend": {"today_so_far": 19242.0, "p10": 15568.0, "p50": 20000.0, "p90": 27755.0},
        "ctr_link": {"today_so_far": 0.02, "p10": 0.005, "p50": 0.008, "p90": 0.01},
        "link_clicks": {"today_so_far": None, "p10": 1.0, "p50": 2.0, "p90": 3.0},
    }))
    assert [(c["metric"], c["read"]) for c in checks] == [("ad_spend", "Normal"), ("ctr_link", "High")]
    assert checks[0]["unit"] == "inr" and checks[0]["p50"] == 20000.0


def test_read_of_edges_are_normal():
    assert report.read_of(10, 10, 20) == "Normal" and report.read_of(20, 10, 20) == "Normal"
    assert report.read_of(9.9, 10, 20) == "Low"


def test_build_skips_chat_and_keeps_code_checks_when_extraction_fails():
    class RT:
        settings = None
    assert asyncio.run(report.build(RT(), {"trigger_kind": "chat"}, "hi", [])) is None
    report_ = asyncio.run(report.build(RT(), {"trigger_kind": "cron"}, "**x**", []))
    assert report_ == {"version": 1, "eda_checks": [], "extract_failed": True}
