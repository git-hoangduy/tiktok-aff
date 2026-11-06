# AI TikTok Affiliate Video Generator

Website chạy local trên Windows: upload ảnh sản phẩm → bấm **TẠO VIDEO** → nhận video TikTok 9:16 (1080×1920 MP4)
có chuyển động, giọng đọc tiếng Việt, phụ đề và nhạc nền.

**Ảnh sản phẩm thật được giữ nguyên.** Hệ thống tách nền ảnh gốc rồi ghép lên bối cảnh AI tạo ra, chỉ phóng to/thu nhỏ
và thêm bóng đổ. Không vẽ lại sản phẩm, không đổi màu, logo hay chữ trên bao bì. AI chỉ được dùng thông tin nhìn thấy
trong ảnh hoặc do bạn cung cấp. Thông tin nào không chắc chắn sẽ được ghi là "chưa xác định".

## Cài đặt (Windows 10/11)

Cần có: **Python 3.10+** và **FFmpeg**. Không cần GPU.

```powershell
# 1. FFmpeg (cài xong thì mở lại terminal)
winget install Gyan.FFmpeg

# 2. Thư viện Python (khoảng 600MB, đặt trong thư mục dự án)
cd D:\Code\python\aff-tiktok
python -m venv .venv
.venv\Scripts\python -m pip install --no-cache-dir -r requirements.txt

# 3. Cấu hình
copy .env.example .env      # rồi mở .env và điền GEMINI_API_KEY (không bắt buộc)

# 4. Chạy
run.bat                      # mở http://127.0.0.1:8000
```

Lần đầu tách nền, hệ thống tự tải model rembg (khoảng 180MB) vào `data\models`. Toàn bộ dữ liệu (ảnh, video, database)
nằm trong `data\` của dự án, không ghi vào ổ C:.

## Tính năng nào cần API key

| Tính năng | Dịch vụ | Cần key? | Khi không có key / hết hạn mức |
|---|---|---|---|
| Phân tích ảnh sản phẩm, viết 3 kịch bản | Gemini (Free Tier) | **Có** `GEMINI_API_KEY` | Dùng kịch bản mẫu offline, chỉ dựa trên thông tin bạn nhập và màu đo từ ảnh |
| Ảnh nền bối cảnh | Pollinations.ai | Không (token là tùy chọn) | Tự tạo nền local (gradient, bokeh, blur) từ màu sản phẩm |
| Ảnh nền bằng Gemini image | Gemini | Có, và phải đặt `GEMINI_IMAGE_MODEL` | Mặc định tắt (thường không nằm trong gói free) |
| Giọng đọc nam/nữ | Edge TTS | Không (cần Internet) | Tự thử lại nhiều lần. Nếu vẫn lỗi thì dùng giọng Google (gTTS, chỉ có giọng nữ) cho cả video |
| Tách nền, ghép ảnh, chuyển động, phụ đề, xuất MP4 | rembg, Pillow, FFmpeg (local) | Không | Luôn chạy |

### Cách lấy Gemini API key (miễn phí)
1. Vào https://aistudio.google.com/apikey và đăng nhập bằng tài khoản Google.
2. Bấm **Create API key**, rồi copy key.
3. Dán vào `.env`: `GEMINI_API_KEY=...`, sau đó khởi động lại `run.bat`.

Hạn mức của gói free do Google quy định và có thể thay đổi. Ứng dụng **không giả định** con số nào cả.
Nếu muốn tự giới hạn số lượt gọi mỗi ngày, đặt `GEMINI_DAILY_LIMIT` trong `.env`. Khi hết hạn mức (lỗi 429),
ứng dụng tự chuyển sang phương án offline và ghi rõ trên giao diện. Số lượt gọi API được thống kê ở Dashboard.

Nếu model `gemini-flash-latest` không còn, hãy đổi `GEMINI_MODEL` sang model mới
(danh sách tại https://ai.google.dev/gemini-api/docs/models). Khi model chính quá tải (lỗi 503), app tự thử các model trong
`GEMINI_FALLBACK_MODELS`. Kịch bản bị mất dấu tiếng Việt sẽ bị loại và được viết lại bằng model khác.

**Quan trọng:** hãy tạo key trong một project **chưa gắn thanh toán**. Nếu project đã bật billing trả trước mà hết tiền,
Google trả lỗi 402 cho mọi model và không dùng hạn mức free nữa. Model tạo ảnh của Gemini không có trong gói free (lỗi 429),
nên `GEMINI_IMAGE_MODEL` mặc định để trống.

Pollinations (không cần đăng nhập) có giới hạn lượt và đôi khi trả lỗi 402. Khi đó các cảnh còn lại của video đó tự dùng
nền local, không phải chờ thêm.

## Ảnh đầu vào

- Đọc được các định dạng **JPG, JFIF, PNG, WEBP, AVIF, HEIC/HEIF (iPhone), GIF, BMP, TIFF**. Định dạng được nhận diện
  theo nội dung file chứ không theo đuôi file. Hệ thống tự xoay ảnh theo EXIF, xử lý được ảnh CMYK, ảnh 16-bit,
  ảnh động (lấy khung hình đầu) và ảnh bị tải thiếu. Ảnh trùng nhau hoặc quá nhỏ sẽ bị bỏ qua và có thông báo.
- **Ảnh lấy từ Shopee/TikTok** (có nền, chữ quảng cáo, logo shop, tay cầm) đều dùng được:
  1. Gemini đánh giá **từng ảnh**: khung sản phẩm chính, có tay hay không, có chữ chèn thêm hay không, vai trò của ảnh
     (mặt trước, chi tiết, đang dùng, bảng size...) và chọn ảnh rõ nhất làm ảnh chính.
     Chữ quảng cáo, giá, "Freeship", watermark không được coi là thông tin sản phẩm.
  2. Hệ thống cắt theo khung sản phẩm rồi tách nền. Ảnh có tay cầm thì dùng SAM để tách sản phẩm khỏi tay
     (lần đầu tải model khoảng 100MB). Nếu tách không sạch, ảnh gốc sẽ được hiển thị dạng thẻ chứ không bị tách lỗi.
  3. Bảng size và ảnh chỉ toàn chữ bị loại. Ảnh sản phẩm đang được dùng thực tế sẽ được giữ nguyên toàn khung
     trong cảnh "bối cảnh".
- Kết quả xử lý từng ảnh hiện ở trang Sản phẩm. Nếu Gemini chọn sai khung, sửa `product_box` trong phần
  "Sửa phân tích (JSON)" rồi lưu. Hệ thống sẽ tách lại theo khung mới.

## Ảnh sản phẩm trong video: AI tái tạo (mặc định) hoặc ghép ảnh thật

- **AI tái tạo:** AI (FLUX.1 Kontext, chạy free trên Hugging Face) xem ảnh gốc rồi **tạo ảnh mới** đặt sản phẩm vào bối cảnh
  và góc chụp mới (chính diện, cận cảnh, nghiêng 3/4, đang dùng), đồng thời bỏ tay cầm, sticker, watermark.
  Mỗi ảnh mất khoảng 30–60 giây. Mỗi video tạo tối đa `AI_IMAGES_PER_VIDEO` ảnh (mặc định 3); các cảnh còn lại dùng lại
  những ảnh đó với chuyển động máy quay khác.
  - Không đăng nhập, quota GPU free rất nhỏ (thường chỉ đủ 1 ảnh). **Nên tạo tài khoản Hugging Face miễn phí** và lấy token
    tại https://huggingface.co/settings/tokens (quyền "Read"), dán vào `.env` ở dòng `HF_TOKEN=`.
  - Khi hết quota, cảnh đó dùng **ảnh gốc đóng khung** (không bị sứt mẻ như ảnh tách nền).
  - Lưu ý: AI có thể làm méo nhẹ chữ nhỏ hoặc chi tiết in trên sản phẩm. Hãy xem lại trước khi đăng.
- **Ghép ảnh thật:** tách nền ảnh gốc rồi ghép lên nền mới. Sản phẩm giữ đúng 100% nhưng mép có thể bị sứt.

## Giọng đọc
Mặc định dùng **giọng Google** (gTTS, giống các app làm video YouTube; không cần key, chỉ có giọng nữ).
Giọng nam dùng Edge TTS (NamMinh), vì Google không có giọng nam tiếng Việt miễn phí. Đổi thứ tự trong `.env`: `TTS_PROVIDERS`.

## Nghiên cứu xu hướng (tự động, miễn phí)

Trước khi viết kịch bản, module `TrendResearcher` sẽ:
1. Tìm bài viết và báo cáo xu hướng TikTok mới nhất của ngành hàng (DuckDuckGo, trong tháng gần nhất, không có thì mở rộng ra 1 năm).
2. Tìm video hot cùng loại sản phẩm, kèm lượt xem (chủ yếu là YouTube và TikTok mà công cụ tìm kiếm thu thập được).
3. Đọc caption và tác giả của các **link TikTok bạn dán vào** (qua TikTok oEmbed, là API công khai).
4. Nhờ Gemini tóm tắt thành: format đang hot, kiểu hook, nhịp dựng, hashtag, video nên so sánh, gợi ý cho sản phẩm.
   Mỗi ý đều ghi số thứ tự nguồn để bạn kiểm tra lại.

Mỗi kịch bản ghi rõ đang **áp dụng xu hướng nào** và **khác gì so với video top**, kèm caption và hashtag để đăng.
Kết quả nghiên cứu được lưu 24 giờ cho mỗi sản phẩm. Muốn làm mới thì bấm "Tìm xu hướng mới".

Giới hạn: hệ thống chỉ đọc tiêu đề, caption, lượt xem và nội dung bài viết, không xem được nội dung bên trong video.
TikTok không có API xu hướng miễn phí, nên cách chính xác nhất là bạn dán vài link video hot mà bạn đã thấy trên TikTok.
Tìm kiếm Google tích hợp trong Gemini (Search grounding) không có trong gói free, nên hệ thống dùng DuckDuckGo thay thế.

## Tiết kiệm token Gemini

Một sản phẩm tốn tối đa 3 lượt gọi Gemini: phân tích (có ảnh) → tóm tắt xu hướng → viết 3 kịch bản cùng lúc.
Mỗi video sau đó không tốn thêm lượt nào, vì tách nền, dựng hình, giọng đọc và phụ đề đều chạy local.

- Kết quả phân tích được lưu lại và dùng cho mọi video. Chỉ phân tích lại khi bạn bấm nút "Phân tích lại".
- Báo cáo xu hướng được lưu 24 giờ và **dùng chung cho các sản phẩm cùng ngành hàng**, trừ khi bạn dán link tham khảo riêng.
- Prompt viết bằng tiếng Anh ngắn gọn, schema JSON dạng nén, và mỗi bước chỉ gửi dữ liệu cần dùng.
- Ảnh gửi đi thu nhỏ còn 768px ở độ phân giải medium. Chế độ "thinking" được tắt hoặc đặt mức thấp.
  Mỗi loại tác vụ có giới hạn số token đầu ra.
- Lượt gọi lỗi do quá tải (503) không tốn token. App chỉ chuyển sang model khác khi kết quả hỏng (mất dấu, JSON sai).
- Dashboard hiển thị số **token vào / ra** của mỗi dịch vụ theo ngày.

## Cách dùng

1. **Tạo video**: upload 1–10 ảnh (ảnh đầu tiên là ảnh chính), nhập tên, giá, công dụng, chọn thời lượng,
   giọng đọc, phong cách, số phiên bản và nhạc nền, rồi bấm **TẠO VIDEO**.
   - Hệ thống tự chạy hết: phân tích, viết 3 kịch bản, dựng 1–3 video.
   - Nếu tick "Cho tôi duyệt kịch bản trước", hệ thống chỉ phân tích và viết kịch bản. Bạn sửa xong thì bấm **Dựng video**.
2. **Sản phẩm**: xem hoặc sửa kết quả phân tích (kết quả được lưu lại để dùng cho các lần sau), sửa từng cảnh của kịch bản.
3. **Video**: xem trước, tải MP4 và file phụ đề `.srt`. Muốn **tạo lại từng cảnh** thì sửa lời, đổi ảnh nền, đổi ảnh sản phẩm
   hoặc kiểu chuyển động. Chỉ cảnh đó được dựng lại, nên nhanh và không tốn thêm lượt API của các cảnh khác.
4. **Nhạc nền**: chép file mp3 vào `assets\music\`. Nên dùng nhạc miễn phí bản quyền.
5. **Chỉ xử lý local**: tick ô này thì không gọi API tạo ảnh nào cả.

## Kiểm tra

```powershell
.venv\Scripts\python -m tests.smoke_test            # offline: template + nền local (cần mạng cho giọng đọc)
.venv\Scripts\python -m tests.smoke_test --online   # dùng Gemini/Pollinations theo .env
```

Bài test chạy lần lượt: tách nền → phân tích → 3 kịch bản → phụ đề → dựng video → kiểm tra 1080×1920 H.264 + AAC
→ tạo lại 1 cảnh.

## Cấu trúc dự án

```
app/
  main.py              Web (FastAPI) + các trang
  pipeline.py          Điều phối quy trình, hàng đợi chạy nền (mỗi lần 1 việc cho đỡ tốn RAM)
  config.py            Đọc .env, đường dẫn, tự tìm FFmpeg và font
  prompts.py           Prompt cho AI (cấm bịa thông tin)
  db.py, usage.py      SQLite, log và hạn mức API
  providers/           Lớp Provider Interface, có thể thay nhà cung cấp AI
    base.py            LLMProvider, ImageProvider, TTSProvider
    gemini.py          Gemini (phân tích, kịch bản, ảnh tùy chọn)
    template_llm.py    Kịch bản offline dự phòng
    images.py          Pollinations + nền procedural local
    tts.py             Edge TTS
  modules/
    product_analyzer.py   ProductAnalyzer
    script_generator.py   ScriptGenerator
    scene_planner.py      ScenePlanner (bố cục, chuyển động, chuyển cảnh)
    image_generator.py    ImageGenerator (lần lượt thử từng provider)
    background_remover.py BackgroundRemover (rembg)
    product_compositor.py ProductCompositor (ghép ảnh thật + bóng + chữ)
    video_generator.py    VideoGenerator (zoom/pan/push-in/ánh sáng bằng FFmpeg)
    voice_generator.py    VoiceGenerator (mỗi cảnh 1 file giọng, cảnh dài đúng bằng lời đọc)
    subtitle_generator.py SubtitleGenerator (SRT + ASS)
    video_composer.py     VideoComposer (chuyển cảnh, phụ đề, giảm nhạc khi có giọng)
  templates/, static/  Giao diện
data/                  Database, ảnh upload, video xuất ra, model (tự tạo)
assets/music/          Nhạc nền của bạn
tests/smoke_test.py
```

Muốn thêm nhà cung cấp mới (ví dụ Image-to-Video ở Phase 2), viết một class kế thừa interface trong `providers/base.py`
và đăng ký trong `providers/__init__.py`.

## Lộ trình

- **Phase 1 (đã làm)**: upload, phân tích AI, 3 kịch bản có thể sửa, ảnh nền, ghép sản phẩm gốc, chuyển động FFmpeg,
  TTS tiếng Việt, phụ đề, nhạc nền, xuất MP4, tạo lại từng cảnh, 4 phong cách, 1–3 phiên bản, dashboard, lịch sử, log API.
- **Phase 2**: Image-to-Video AI. Chỉ gọi khi bạn bấm duyệt, vì API của Kling và các dịch vụ tương tự là trả phí,
  còn credit miễn phí thường chỉ dùng được trên web. Ngoài ra: thư viện sản phẩm nâng cao, preset theo từng ngành hàng.
- **Phase 3**: người mẫu AI, video UGC, tạo hàng loạt.

## Lưu ý

- Edge TTS dùng endpoint không chính thức của Microsoft, có thể ngừng hoạt động bất cứ lúc nào.
  Lớp `TTSProvider` cho phép thay sang dịch vụ khác.
- Nếu tách nền không tốt (ví dụ sản phẩm trong suốt, phản quang), hệ thống tự hiển thị ảnh gốc dạng thẻ thay vì
  ảnh bị lỗi. Bạn cũng có thể upload sẵn ảnh PNG đã tách nền.
- TikTok yêu cầu gắn nhãn nội dung do AI tạo khi đăng. Hãy kiểm tra lại thông tin sản phẩm trước khi đăng.
