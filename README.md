# TNG Gemba Walk — DEMO Online

Ứng dụng web độc lập chạy trên điện thoại và máy tính. **Không dùng Google Sheets, không kết nối hệ thống TNG**. Giao diện tiếng Việt.

## Những gì app làm được
- Admin thêm/sửa/khóa tài khoản quản lý, phân quyền, nhập CSV UTF-8 từ Excel.
- Người Gemba tạo vấn đề, giải pháp, khu vực và ảnh tùy chọn, deadline và giao nhiều người.
- Người được giao tick **Xử lý xong**; không có cột **Xác nhận**.
- Chỉ người tạo Gemba có thể đánh giá Đạt/Không đạt sau khi tất cả xử lý xong. Không đạt → vòng xử lý lại.
- Nuôi PIG tự ghi khi quá hạn, xử lý sau hạn, hoặc đánh giá Không đạt trước hạn (mỗi lượt/đối tượng/vòng không lặp). Khi có truy cập API, server tự rà soát quá hạn.
- Trang chủ trắng/xanh/vàng; Cá nhân 2 tab Đã nhập/Đã xử lý, hồ sơ chờ chính tác giả đánh giá màu tím.
- Báo cáo theo chi nhánh, xuất CSV mở bằng Excel.
- Dữ liệu chung trong cơ sở dữ liệu SQLite trên máy chủ chạy app (không lưu riêng ở điện thoại).

## Chạy thử trên máy tính (Windows / Mac)
1. Cài Python 3.11+.
2. Mở Terminal trong thư mục này. Chạy `pip install -r requirements.txt`.
3. Đặt biến môi trường **ADMIN_EMAIL** và **ADMIN_PASSWORD** (mật khẩu mạnh tối thiểu 8 ký tự; nên từ 14 ký tự). Ví dụ trong PowerShell:
   - `$env:ADMIN_EMAIL='admin@example.com'`
   - `$env:ADMIN_PASSWORD='CHANGE-ME-STRONG-AND-UNIQUE'`
4. Chạy `uvicorn app.server:app --host 0.0.0.0 --port 8000`.
5. Truy cập `http://localhost:8000`, dùng email/mật khẩu Admin vừa đặt.
6. Admin vào **⚙ Danh sách quản lý**, thêm những người dùng thử, chuyển riêng tài khoản cho họ.

Nếu chạy lần đầu mà chưa đặt ADMIN_PASSWORD, hệ thống tạo mật khẩu ngẫu nhiên và in ra terminal. Không công khai cửa sổ terminal. Nếu cơ sở dữ liệu đã được khởi tạo thì thay đổi ADMIN_PASSWORD trong môi trường không tự thay đổi mật khẩu cũ.

## Triển khai lên web để nhiều quản lý cùng dùng
Bản này là ứng dụng server thực sự, không phải file HTML tĩnh. Có thể chạy Docker trên máy chủ ngoài TNG có URL HTTPS và **ổ đĩa lưu lâu dài** (VD VPS hoặc nền tảng app cloud có persistent disk).

Cấu hình cần có:
- Đường dẫn lưu trữ **DATA_DIR** và **UPLOAD_DIR** đặt trên ổ đĩa lâu dài, giữ qua restart/deploy.
- **ADMIN_EMAIL**, **ADMIN_PASSWORD** (chỉ áp dụng lúc database mới), **COOKIE_SECURE=1** khi chạy qua HTTPS.
- Dùng `Dockerfile` đã kèm, công khai cổng `$PORT` và cấu hình domain HTTPS.
- Sao lưu `data/gemba.sqlite3` và thư mục `uploads` định kỳ nếu dùng lâu.

**Quan trọng:** bản miễn phí của nhiều nền tảng (VD Render Free) thường dùng filesystem tạm. Nếu triển khai lên đó, dữ liệu và hình ảnh có thể mất sau redeploy/restart. Không được quảng bá là "đồng bộ bền vững" nếu không có ổ đĩa lưu lâu dài. Một số nhà cung cấp cũng tính phí hosting hoặc lưu trữ; không có cam kết 100% miễn phí.

## Giới hạn của bản DEMO
- Chưa có gửi email/mật khẩu tự động hoặc tự phục hồi mật khẩu. Admin cấp/đặt lại mật khẩu thủ công.
- Báo cáo xuất CSV (Excel mở được), chưa xuất file .xlsx trực tiếp.
- Ảnh được lưu trên filesystem cùng server; ai có đường dẫn ảnh có thể truy cập ảnh khi app đã được công khai. Chỉ dùng ảnh giả lập, không đưa hình ảnh/tài liệu công ty thật vào app demo cho đến khi triển khai xác thực đọc ảnh.
- PIG rà soát khi có yêu cầu đến API; nếu không ai mở app, bản ghi PIG quá hạn được tạo khi có truy cập tiếp theo. Không có scheduler chạy liên tục.
- Đăng nhập dùng cookie HttpOnly SameSite=Strict, mật khẩu PBKDF2, có phân quyền server. Trước khi dùng thực tế phải bổ sung CSRF/rate-limit, bảo vệ upload và thử bảo mật.
- Phù hợp để thử nghiệm nghiệp vụ, **không phải hệ thống sản xuất chính thức**.

## Chuỗi thao tác thử
1. Admin tạo 2 tài khoản Quản lý A, B.
2. A đăng nhập, tạo Gemba và giao B với hạn xử lý.
3. B đăng nhập điện thoại thứ hai, bấm **Xử lý xong**.
4. A mở lại và đánh giá **Đạt** hoặc **Không đạt**.
5. Vào **Nuôi PIG** để kiểm tra các hồ sơ quá hạn / Không đạt trong hạn.
