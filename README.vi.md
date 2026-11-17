# facebook-mcp (Tiếng Việt)

[English](README.md)

MCP server giúp trợ lý AI (Claude Code, Claude Desktop, Cursor, Gemini CLI và mọi MCP client) quản lý **Facebook Page** qua Meta Graph API chính thức.

- Đăng bài chữ, bài kèm link, bài ảnh (file trên máy hoặc URL), đăng ngay hoặc hẹn giờ
- Đăng video dạng **Reels** (Reels API 3 bước) hoặc video Page thường; file lớn tự chia khúc (resumable upload)
- Xem, đổi giờ, hoặc đăng ngay các bài đã hẹn giờ
- Xem thống kê Page và từng bài; metric đã bị Meta khai tử được báo riêng, không làm hỏng cả lệnh
- Xóa bài
- Chế độ chỉ đọc, giới hạn thư mục được phép tải lên, che token trong mọi thông báo lỗi

Chỉ gọi tới `graph.facebook.com` bằng Page token của bạn. Không qua dịch vụ trung gian, không scrape, không điều khiển trình duyệt.

## Danh sách tool

| Tool | Loại | Chức năng | Tham số chính |
|---|---|---|---|
| `fb_get_page_info` | đọc | Tên Page, lượt thích, người theo dõi, link, mô tả, ảnh | `page_id`? |
| `fb_get_recent_posts` | đọc | Bài mới nhất kèm reaction, bình luận, chia sẻ | `limit` (1-100, mặc định 10) |
| `fb_get_scheduled_posts` | đọc | Bài đã hẹn giờ, sắp theo thời gian (tối đa 500) | `limit` |
| `fb_get_page_insights` | đọc | Thống kê Page | `period`, `metrics`?, `since`?, `until`? |
| `fb_get_post_insights` | đọc | Thống kê một bài/video | `post_id` |
| `fb_list_pages` | đọc | Các Page tài khoản quản lý (không trả về Page token) | `user_access_token`? |
| `fb_publish_post` | ghi | Đăng bài chữ/link/ảnh, ngay hoặc hẹn giờ | `message`, `link`?, `photo_path`?, `photo_url`?, `scheduled_publish_time`? |
| `fb_publish_video` | ghi | Đăng Reel hoặc video Page | `video_path`, `description`, `title`?, `as_reel` (mặc định `true`), `scheduled_publish_time`? |
| `fb_reschedule_post` | ghi | Đổi giờ bài đã hẹn | `post_id`, `new_scheduled_time_unix` |
| `fb_publish_scheduled_now` | ghi | Đăng ngay bài đang hẹn giờ | `post_id` |
| `fb_delete_post` | xóa | Xóa bài/video | `post_id` |

Các tool đều nhận thêm `page_id` / `page_access_token` (khi áp dụng) để làm việc với nhiều Page. Khi đặt `FB_MCP_READ_ONLY=1`, các tool ghi/xóa không được đăng ký.

## Yêu cầu

- Python 3.10+
- Một **Facebook Page** (Graph API không cho tự động hóa trang cá nhân)
- Một Meta app và Page access token có quyền `pages_manage_posts`, `pages_read_engagement`, `read_insights` (thêm `pages_show_list` nếu dùng `fb_list_pages` và công cụ lấy token)

Chạy được với `mcp` Python SDK 1.21.2+ (`FastMCP`) và 2.x (`MCPServer`); các bản 1.x cũ hơn lỗi với pydantic hiện tại.

## Cài đặt

```bash
# Cách A: chạy bằng uvx, không cần cài (khuyên dùng cho MCP client)
uvx --from git+https://github.com/tannht/facebook-mcp facebook-mcp --version

# Cách B: pip
pip install git+https://github.com/tannht/facebook-mcp

# Cách C: từ bản clone
git clone https://github.com/tannht/facebook-mcp.git && cd facebook-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Sau khi cài có 3 lệnh: `facebook-mcp` (server), `facebook-mcp-check` (kiểm tra token, chỉ đọc), `facebook-mcp-token` (đổi user token sang Page token và lưu vào `.env`).

## Lấy Page access token

1. **Tạo app** tại [developers.facebook.com/apps](https://developers.facebook.com/apps) (loại có quyền quản lý Page, ví dụ *Business*). Ghi lại **App ID** và **App Secret**.
2. **Lấy user token** trong [Graph API Explorer](https://developers.facebook.com/tools/explorer/): chọn app → *User Token* → thêm quyền `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `read_insights` → **Generate Access Token** → nhớ **tick chọn Page** trong hộp thoại.
3. **Đổi sang Page token vĩnh viễn:**

   ```bash
   facebook-mcp-token --user-token "EAAB..." --app-id 123 --app-secret abc --auto-save
   ```

   Lệnh này gia hạn user token lên ~60 ngày (cần App ID + Secret; nếu thiếu, Page token sẽ hết hạn sau khoảng 1 giờ và lệnh sẽ cảnh báo), liệt kê các Page kèm hạn token và quyền, rồi ghi `FB_PAGE_ID`, `FB_PAGE_ACCESS_TOKEN` vào `.env` (quyền file `600`). Dùng `--page-id` nếu quản lý nhiều Page, `--print-token` nếu chỉ muốn in ra.

   Mẹo: truyền secret qua tham số sẽ lưu lại trong lịch sử shell; có thể điền `FB_USER_ACCESS_TOKEN`, `FB_APP_ID`, `FB_APP_SECRET` vào `.env` rồi chạy `facebook-mcp-token --auto-save`.

   Làm tay: dán user token vào [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/) → **Extend Access Token** → gọi `GET /me/accounts` trong Graph API Explorer → lấy `id` và `access_token` của Page.
4. **Kiểm tra:** `facebook-mcp-check` (hiển thị tên Page, loại token, hạn dùng, từng quyền và vài bài gần nhất).

## Cấu hình

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `FB_PAGE_ID` | có | Page mặc định |
| `FB_PAGE_ACCESS_TOKEN` | có | Page access token |
| `FB_USER_ACCESS_TOKEN` | không | User token cho `fb_list_pages` và `facebook-mcp-token` |
| `FB_APP_ID`, `FB_APP_SECRET` | không | Để `facebook-mcp-token` gia hạn user token |
| `META_GRAPH_API_VERSION` | không | Phiên bản Graph API, mặc định `v24.0` |
| `FB_MCP_READ_ONLY` | không | `1` = ẩn mọi tool đăng/hẹn giờ/xóa |
| `FB_MCP_ALLOWED_DIRS` | không | Các thư mục (ngăn bằng `:`, Windows dùng `;`) được phép tải ảnh/video lên |
| `FB_MCP_ENV_FILE` | không | Đường dẫn file `.env` cụ thể |
| `FB_MCP_SKIP_DOTENV` | không | `1` = bỏ qua mọi file `.env` |
| `FB_MCP_LOG_LEVEL` | không | Mức log (ghi ra stderr) |

Thứ tự ưu tiên: biến môi trường của tiến trình (khối `env` trong cấu hình MCP client) → `FB_MCP_ENV_FILE` → `.env` ở thư mục đang chạy → `.env` ở gốc bản clone. File `.env` không bao giờ ghi đè biến đã có.

## Kết nối MCP client

**Claude Code:**

```bash
claude mcp add facebook \
  -e FB_PAGE_ID=ID_PAGE -e FB_PAGE_ACCESS_TOKEN=TOKEN_PAGE \
  -- uvx --from git+https://github.com/tannht/facebook-mcp facebook-mcp
```

**Claude Desktop / Cursor / Gemini CLI** (`claude_desktop_config.json`, `.cursor/mcp.json`, `~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "facebook": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/tannht/facebook-mcp", "facebook-mcp"],
      "env": { "FB_PAGE_ID": "ID_PAGE", "FB_PAGE_ACCESS_TOKEN": "TOKEN_PAGE" }
    }
  }
}
```

Chạy từ bản clone: `"command": "/duong-dan/facebook-mcp/.venv/bin/python"`, `"args": ["/duong-dan/facebook-mcp/server.py"]`; server tự đọc `/duong-dan/facebook-mcp/.env`.

## Ví dụ câu lệnh

- "Cho xem thông tin Page và 5 bài gần nhất kèm số reaction, bình luận, chia sẻ."
- "Bài nào trong 20 bài gần nhất tương tác tốt nhất? So với thống kê Page tuần qua."
- "Đăng Reel `/videos/teaser.mp4` với caption 'Teaser #launch', hẹn 19:00 ngày mai giờ Việt Nam."
- "Liệt kê bài đã hẹn giờ và dời bài 21:00 sang 20:00."

## Hành vi cần biết

- **Hẹn giờ:** UNIX timestamp tính bằng **giây**, cách hiện tại ít nhất 10 phút; giá trị mili-giây bị từ chối.
- **Reels:** video phải đạt [chuẩn Reels của Meta](https://developers.facebook.com/docs/video-api/guides/reels-publishing) (dọc 9:16, thời lượng ngắn, độ phân giải tối thiểu); `title` chỉ dùng cho video Page thường, caption của Reel là `description`. Nếu Page không dùng được Reels API, bước *start* bị từ chối và server đăng thành video Page thường (kết quả có trường `note`). Việc chuyển hướng này chỉ xảy ra trước khi gửi byte nào, nên một lệnh không bao giờ đăng trùng video; lỗi token, mạng, giới hạn tần suất hay 5xx không kích hoạt chuyển hướng.
- **Video > 50 MB** (khi `as_reel=false`) tải theo khúc 25 MB, mỗi khúc tự thử lại khi lỗi mạng; nếu server ngừng nhận, lệnh báo lỗi rõ ràng thay vì "hoàn tất" giả.
- **Insights:** Meta thường khai tử metric (ví dụ `page_impressions`, `post_impressions` đã bị bỏ). Mặc định dùng các metric còn hoạt động; metric sai được liệt kê trong `metric_errors`.
- **Độ ổn định:** mọi request có timeout; lệnh đọc tự thử lại khi lỗi mạng/5xx/giới hạn tần suất; lệnh ghi không tự thử lại để tránh đăng trùng; stdout chỉ dành cho giao thức MCP; server vẫn khởi động khi chưa có token.

## Bảo mật

- Không commit `.env` (đã có trong `.gitignore`).
- Token không bao giờ tới model: `fb_list_pages` bỏ Page token, mọi thông báo lỗi được che token/secret.
- Đặt `FB_MCP_ALLOWED_DIRS` để model không thể "tải lên" file nhạy cảm trên máy.
- Dùng `FB_MCP_READ_ONLY=1` nếu chỉ cần xem thống kê.
- Đăng và xóa là công khai, không hoàn tác: giữ bật chế độ hỏi xác nhận tool trong client.
- Nếu lộ token: gỡ app trong *Cài đặt Facebook → Tiện ích tích hợp doanh nghiệp* (hoặc đổi mật khẩu), rồi tạo token mới.

## Xử lý sự cố

| Hiện tượng | Nguyên nhân và cách sửa |
|---|---|
| `Missing page_id` / `Missing access token` | Chưa cấu hình biến. Chạy `facebook-mcp-check`, kiểm tra khối `env` hoặc vị trí `.env`. |
| `Graph API error 190` | Token hết hạn/bị thu hồi/sai. Tạo token mới. |
| `Graph API error 10` / `200` | Thiếu quyền hoặc vai trò trên Page. Tạo lại token đủ 4 quyền và tick Page. |
| `Graph API error 100` | Tham số sai hoặc metric đã bị bỏ; xem `metric_errors`. |
| `Graph API error 4`, `17`, `32`, `613` | Bị giới hạn tần suất. Lệnh đọc tự thử lại; nếu không, đợi rồi thử lại. |
| `Graph API error 368` | Bị hệ thống chống spam chặn. Giảm tần suất, xem lại nội dung. |
| Client báo server lỗi / không có tool | Chạy `facebook-mcp --version` trong cùng môi trường và xem log MCP của client. |

## Phát triển

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

Bộ test giả lập HTTP cho Graph client và khởi chạy server thật qua stdio để kiểm tra bắt tay MCP; không gọi tới Facebook. CI chạy Python 3.10/3.12/3.13 với `mcp==1.21.2` (tối thiểu), `mcp` 1.x mới nhất và `mcp>=2`.

## Giấy phép

[MIT](LICENSE). Dự án không liên kết hay được Meta bảo trợ.
