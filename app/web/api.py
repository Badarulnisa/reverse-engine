"""
Local web interface for reverse-engine. Drop a .HAR file in the browser
to run it through the same pipeline as the CLI (main.py) and download
the resulting Excel report.
"""

import os
import shutil
import uuid

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, FileResponse

from app.collectors.extractor import extract_records
from app.core.bot_defense_scanner import automatable_endpoints
from app.core.bot_defense_scanner import scan_records as scan_bot_defense
from app.core.curl_generator import generate_snippets
from app.core.har_parser import parse_har_file
from app.core.report_builder import build_report
from app.core.system_detector import group_by_system, summarize_systems
from app.core.vuln_scanner import scan_records as scan_vulns
from app.core.vuln_scanner import summarize_findings

app = FastAPI(title="reverse-engine Web Interface")

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>reverse-engine</title>
    <style>
        body { background: #0a0a0a; color: #ededed; font-family: monospace; padding: 2rem; }
        .dropzone { border: 2px dashed #444; padding: 3rem; text-align: center; border-radius: 8px; cursor: pointer; }
        .dropzone:hover { border-color: #888; }
        button { background: #fff; color: #000; border: none; padding: 0.75rem 1.5rem; font-weight: bold; cursor: pointer; margin-top: 1rem; }
    </style>
</head>
<body>
    <h1>reverse-engine // Local Dashboard</h1>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <div class="dropzone" onclick="document.getElementById('fileInput').click();">
            <p>Drag and drop a .HAR file here, or click to select</p>
            <input type="file" id="fileInput" name="file" accept=".har" style="display:none" onchange="this.form.submit()">
        </div>
    </form>
</body>
</html>
"""


def run_pipeline(har_path: str, output_path: str) -> str:
    """
    Same 6-step flow as main.py's run(), factored out so both the CLI
    and the web upload handler call one shared implementation instead
    of two copies that can drift apart.
    """
    records = parse_har_file(har_path)

    grouped = group_by_system(records)
    system_endpoint_data = {host: extract_records(recs) for host, recs in grouped.items()}
    summaries = summarize_systems(grouped)

    findings = scan_vulns(records)
    finding_summary = summarize_findings(findings)

    bot_findings = scan_bot_defense(records)
    automation = automatable_endpoints(records, bot_findings)

    snippets = generate_snippets(records)

    build_report(
        summaries,
        system_endpoint_data,
        output_path,
        findings=findings,
        finding_summary=finding_summary,
        bot_defense_findings=bot_findings,
        automation_status=automation,
        snippets=snippets,
    )
    return output_path


@app.get("/", response_class=HTMLResponse)
async def main_dashboard():
    return HTML_TEMPLATE


@app.post("/upload")
async def upload_har(file: UploadFile = File(...)):
    # unique-ish temp names so concurrent uploads in the same session don't collide
    job_id = uuid.uuid4().hex[:8]
    temp_har_path = f"temp_{job_id}_{file.filename}"
    output_report_path = f"report_{job_id}.xlsx"

    with open(temp_har_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        run_pipeline(temp_har_path, output_report_path)
    finally:
        if os.path.exists(temp_har_path):
            os.remove(temp_har_path)

    return FileResponse(
        output_report_path,
        filename="report.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )