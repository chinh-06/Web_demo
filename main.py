import json
import os
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, Query, Response
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, String, DateTime, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# --- CẤU HÌNH DATABASE ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- ĐỊNH NGHĨA CÁC BẢNG (MODELS) ---

# 1. Bảng lưu IP khách truy cập
class VisitorLog(Base):
    __tablename__ = "visitor_logs"
    ip = Column(String, primary_key=True, index=True)
    last_visit = Column(DateTime, default=datetime.now)

# 2. Bảng lưu lượt xem từng video
class VideoView(Base):
    __tablename__ = "video_views"
    id_vd = Column(String, primary_key=True, index=True)
    view_count = Column(Integer, default=12055000) # Mặc định khởi tạo 12 triệu lượt xem

# Tự động tạo bảng trên Database của Render
Base.metadata.create_all(bind=engine)

app = FastAPI(title="XNHAU API Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"]
)

DATA_FILE = "trang_source.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

def get_local_data():
    if not os.path.exists(DATA_FILE): return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# --- ROUTES GIAO DIỆN ---

@app.get("/")
async def read_index():
    return FileResponse("trangchu.html")

@app.get("/video")
async def read_video():
    return FileResponse("trangvideo.html")

# --- API HỆ THỐNG ---

# Lấy danh sách video trang chủ + Ghi log IP khách
@app.get("/api/videos")
async def get_videos(request: Request):
    client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0]
    db = SessionLocal()
    try:
        visitor = db.query(VisitorLog).filter(VisitorLog.ip == client_ip).first()
        if visitor:
            visitor.last_visit = datetime.now()
        else:
            new_visitor = VisitorLog(ip=client_ip, last_visit=datetime.now())
            db.add(new_visitor)
        db.commit()
    except Exception as e:
        print(f"Lỗi Database IP: {e}")
    finally:
        db.close()
    return get_local_data()

# Lấy chi tiết video + Tăng lượt xem (Cộng dồn vào 12.055.000)
@app.get("/api/get-link/{id_vd}")
async def get_link(id_vd: str):
    videos = get_local_data()
    video = next((v for v in videos if str(v["id_vd"]) == str(id_vd)), None)
    if not video: return {"error": "404"}

    db = SessionLocal()
    current_views = 12055000
    try:
        view_rec = db.query(VideoView).filter(VideoView.id_vd == id_vd).first()
        if not view_rec:
            # Nếu lần đầu xem, khởi tạo số lượt xem mặc định + 1
            view_rec = VideoView(id_vd=id_vd, view_count=12055001)
            db.add(view_rec)
        else:
            view_rec.view_count += 1
        db.commit()
        current_views = view_rec.view_count
    except Exception as e:
        print(f"Lỗi Database View: {e}")
    finally:
        db.close()

    return {
        "link_goc": video.get("source_url"), 
        "title": video.get("title"),
        "image": video.get("image_vd"),
        "tags": video.get("hag_tag", []),
        "views": current_views
    }

# API Đề xuất: Lấy Top 5 video xem nhiều nhất (Tie-break: ID nhỏ hiện trước)
@app.get("/api/top-trending")
async def get_top_trending():
    db = SessionLocal()
    # Sắp xếp: lượt xem GIẢM DẦN, sau đó ID TĂNG DẦN
    top_records = db.query(VideoView).order_by(VideoView.view_count.desc(), VideoView.id_vd.asc()).limit(5).all()
    db.close()

    all_v = get_local_data()
    result = []
    for rec in top_records:
        v_meta = next((v for v in all_v if str(v["id_vd"]) == str(rec.id_vd)), None)
        if v_meta:
            v_meta["views"] = rec.view_count
            result.append(v_meta)
    return result

# Kiểm tra log IP (Chế độ Admin)
@app.get("/api/check-ip-logs")
async def check_ip_logs():
    db = SessionLocal()
    logs = db.query(VisitorLog).all()
    db.close()
    return [{"ip": log.ip, "time": log.last_visit} for log in logs]

# --- PROXY CHUYÊN DỤNG ---

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