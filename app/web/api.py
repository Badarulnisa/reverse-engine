import os
import shutil
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, FileResponse
from app.core.har_parser import parse_har
from app.core.report_builder import build_excel_report

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


@app.get("/", response_class=HTMLResponse)
async def main_dashboard():
    return HTML_TEMPLATE


@app.post("/upload")
async def upload_har(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    parsed_calls = parse_har(temp_path)
    output_report = "report_output.xlsx"
    build_excel_report(parsed_calls, output_report)

    os.remove(temp_path)
    return FileResponse(
        output_report,
        filename="report_output.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )