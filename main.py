import json
import os
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, Query, Response
from fastapi.responses import StreamingResponse, FileResponse # Thêm FileResponse để hiện HTML
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="XNHAU API Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"]
)

DATA_FILE = "trang_source.json"
IP_LOG_FILE = "ip_pl.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

# --- PHẦN THÊM ĐỂ HIỆN GIAO DIỆN (KHÔNG SỬA LOGIC CŨ) ---
@app.get("/")
async def read_index():
    return FileResponse("trangchu.html")

@app.get("/video")
async def read_video():
    return FileResponse("trangvideo.html")
# -------------------------------------------------------

def get_local_data():
    if not os.path.exists(DATA_FILE): return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# API Lấy danh sách video + Logic lọc IP trùng
@app.get("/api/videos")
async def get_videos(request: Request):
    # 1. Lấy IP người dùng
    client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0]
    
    # 2. Kiểm tra xem IP đã tồn tại trong file chưa
    is_exists = False
    if os.path.exists(IP_LOG_FILE):
        with open(IP_LOG_FILE, "r", encoding="utf-8") as f:
            log_content = f.read()
            if f"IP: {client_ip}" in log_content:
                is_exists = True

    # 3. Nếu chưa tồn tại thì mới ghi
    if not is_exists:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(IP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now}] IP: {client_ip} | New User\n")
        
    return get_local_data()

@app.get("/api/get-link/{id_vd}")
async def get_link(id_vd: str):
    videos = get_local_data()
    video = next((v for v in videos if str(v["id_vd"]) == str(id_vd)), None)
    if not video: return {"error": "404"}
    return {
        "link_goc": video.get("source_url"), 
        "title": video.get("title"),
        "image": video.get("image_vd"),
        "tags": video.get("hag_tag", [])
    }

@app.get("/proxy")
async def proxy_video(request: Request, url: str = Query(...)):
    send_headers = {"User-Agent": USER_AGENT, "Referer": "https://xnhau.fan/", "Accept": "*/*"}
    range_header = request.headers.get("range")
    if range_header: send_headers["Range"] = range_header
    client = httpx.AsyncClient(verify=False, follow_redirects=True, timeout=60.0)
    try:
        rp_req = client.build_request("GET", url, headers=send_headers)
        rp_resp = await client.send(rp_req, stream=True)
        async def iterate():
            try:
                async for chunk in rp_resp.aiter_bytes(chunk_size=1024*512): yield chunk
            finally:
                await rp_resp.aclose()
                await client.aclose()
        headers = {"Accept-Ranges": "bytes", "Content-Type": rp_resp.headers.get("Content-Type", "video/mp4"), "Access-Control-Allow-Origin": "*"}
        for k in ["Content-Range", "Content-Length"]:
            if k in rp_resp.headers: headers[k] = rp_resp.headers[k]
        return StreamingResponse(iterate(), status_code=rp_resp.status_code, headers=headers)
    except:
        await client.aclose()
        return Response(status_code=500)

@app.get("/proxy-img")
async def proxy_image(url: str = Query(...)):
    async with httpx.AsyncClient(verify=False, timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
            return Response(content=resp.content, media_type=resp.headers.get("Content-Type", "image/png"))
        except: return Response(status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))