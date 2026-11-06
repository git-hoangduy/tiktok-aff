# Kế hoạch nâng cấp (lưu ngày 06/11/2026)

> **Trạng thái 06/11/2026:** đã cài A1, A3, B (cổng duyệt phí), C. Chưa chạy thử với key `sk_` thật
> (chưa có POLLINATIONS_TOKEN) và chưa dựng video mới. A2 / upscale chỉ bật khi cấu hình trong `.env`.
> Nhất quán giữa các cảnh: ảnh cảnh 1 = ảnh gốc của video, các cảnh sau làm từ nó (cùng tay, máy, nền, ánh sáng).

## A. Chất lượng & độ chính xác ảnh sản phẩm

### A1. Miễn phí, chỉnh trong app hiện tại
- [x] Chọn ảnh tham chiếu tốt nhất cho AI (chính diện, rõ nét, ít che) thay vì ảnh có tay/chữ chèn
      (dùng `images[].role/quality/has_hand` mà Gemini đã trả về).
- [x] Tăng ảnh tham chiếu lên ~1280px và số bước FLUX Kontext (24 → 28–30); đo thời gian + quota.
- [x] (đã làm kiểu tiết kiệm: tạo 1 ảnh, Gemini chấm, dưới AI_VERIFY_MIN mới tạo lại 1 lần) Tạo 2 ảnh / cảnh → Gemini so với ảnh gốc (màu, hình in, chữ, hình dáng) → chọn ảnh giống nhất,
      loại ảnh lệch (~1–2k token Gemini/ảnh, free).
- [~] (có sẵn, bật bằng REALESRGAN_PATH) Upscale AI (Real-ESRGAN, chạy local CPU/GTX 1050 Ti) thay cho resize thường (768×1344 → 1080×1920).
- [x] Prompt chặt hơn: chỉ đổi góc nhẹ 15–30°, không xoay ra mặt sau.

### A2. Chính xác 100%: ảnh thật + AI chỉ làm bối cảnh
- [~] (đổi REMBG_MODEL trong .env) Tách nền bằng BiRefNet (`REMBG_MODEL=birefnet-general` hoặc `-lite`, free, local, tải ~200–900MB).
- [ ] (chưa làm) Ghép lên nền AI, sau đó "hòa ánh sáng" nhẹ (relight/harmonize) — không vẽ lại sản phẩm.

### A3. API xịn hơn qua Pollinations (trừ pollen, cần key `sk_...` tại https://enter.pollinations.ai/keys)
Giá theo https://gen.pollinations.ai/image/models (xem ngày 05/11/2026):

| Model (alias) | Giá/ảnh | Ghi chú |
|---|---|---|
| FLUX.1 Kontext Pro (`kontext`) | 0.03 pollen | bản Pro của model đang dùng, không phụ thuộc quota GPU |
| Nano Banana 2 (`nanobanana2`, Gemini 3.1 Flash Image) | theo token (~0.05–0.08 ước tính) | tới 14 ảnh tham chiếu → chính xác nhất khi đổi góc |
| Seedream 4.0 / 4.5 (`seedream`, `seedream-pro`) | 0.03 / 0.042 | nhiều ảnh tham chiếu, giữ chữ khá tốt |
| GPT Image 1.5 (`gptimage-large`) | theo token, đắt hơn | chữ bao bì chuẩn nhất |

- Ảnh tham chiếu phải là URL công khai → upload qua `POST https://media.pollinations.ai/upload` (Bearer sk_, hết hạn sau 30 ngày).
- Endpoint mới: `https://gen.pollinations.ai/image/{prompt}` + header `Authorization: Bearer sk_...`
  (tên tham số width/height/seed/image cần thử 1 lượt để xác nhận).
- Dashboard: hiện số dư qua `GET https://gen.pollinations.ai/account/balance`.

### Thứ tự đề xuất
1. Nano Banana 2 / Seedream (nhiều ảnh tham chiếu) — chính xác nhất
2. FLUX Kontext free trên Hugging Face (như hiện tại)
3. Gemini kiểm tra lệch → nếu lệch dùng BiRefNet + ảnh thật (đúng 100%)
Ước tính: ~0.1–0.25 pollen / video (3 ảnh AI).

## B. Video chuyển động thật (nền chuyển động, tay xoay sản phẩm)
Máy GTX 1050 Ti 4GB không chạy local được → dùng API image-to-video, chỉ cho 2–3 cảnh chính (5s/cảnh),
các cảnh còn lại vẫn dùng FFmpeg. Quy trình: ảnh AI (A) → image-to-video → ghép.

| Thứ tự | Dịch vụ / model | Giá (pollen/giây) | 1 clip 5s |
|---|---|---|---|
| 1 (free) | Hugging Face Spaces: Wan 2.2 / LTX-Video (dùng HF_TOKEN, chung quota với ảnh) | 0 | 0 |
| 2 | Pollinations `wan-2.2-fast` | 0.01 | 0.05 |
| 3 | Pollinations `seedance-1-pro-fast` (đáng tiền nhất) | 0.021 | 0.105 |
| 4 | Pollinations `p-video` | 0.02 | 0.10 |
| 5 (tay/xoay đẹp nhất) | `kling-v3.0-std` / `veo-3.1-fast` | 0.084 / 0.08 | ~0.4 |

- Ước tính / video 15s (3 clip AI): wan-2.2-fast ~0.15 · seedance ~0.3 · kling ~1.3 pollen.
- BẮT BUỘC: hiện chi phí ước tính và chờ người dùng duyệt trước khi gọi API video có phí.
  Hết quota / pollen → tự quay về chuyển động FFmpeg.
- Rủi ro: xoay mạnh làm AI bịa mặt sau / hình in → chỉ xoay nhẹ 15–30°; tay đôi khi lỗi ngón với model rẻ.
- Credit free trên web (Kling, Hailuo, Vidu…) chỉ dùng tay, không có API free.

## C. Việc nhỏ đã ghi nhận
- [x] Phụ đề đang đè lên nửa dưới sản phẩm ở ảnh AI → hạ phụ đề hoặc thu nhỏ sản phẩm.
- [x] Dùng key `sk_` cho ảnh nền Pollinations (hiện gọi ẩn danh → thường lỗi 402, không trừ pollen).
