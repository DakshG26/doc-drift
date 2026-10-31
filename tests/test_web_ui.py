import json
import threading
import urllib.request
import pytest
from doc_drift.web_ui import start_server, SAMPLE_DIFF, SAMPLE_DOC

@pytest.fixture(scope="module")
def web_server():
    server = start_server(host="127.0.0.1", port=9876)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:9876"
    server.shutdown()
    server.server_close()

def test_web_ui_html(web_server):
    req = urllib.request.urlopen(f"{web_server}/")
    assert req.status == 200
    html = req.read().decode("utf-8")
    assert "Doc Drift Detector" in html

def test_web_ui_samples_api(web_server):
    req = urllib.request.urlopen(f"{web_server}/api/samples")
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert "diff" in data
    assert "doc" in data

def test_web_ui_benchmark_api(web_server):
    req = urllib.request.urlopen(f"{web_server}/api/benchmark")
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert "accuracy" in data
    assert data["total_cases"] >= 5

def test_web_ui_analyze_api(web_server):
    payload = json.dumps({
        "diff": SAMPLE_DIFF,
        "doc": SAMPLE_DOC,
        "doc_name": "cli_reference.md"
    }).encode("utf-8")
    
    req = urllib.request.Request(
        f"{web_server}/api/analyze",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    res = urllib.request.urlopen(req)
    assert res.status == 200
    data = json.loads(res.read().decode("utf-8"))
    assert data["drift_detected"] is True
    assert data["total_issues"] >= 1
    assert len(data["suggested_fixes"]) >= 1
