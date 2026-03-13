# iFarm Skill

Control physical iPhones connected via USB to this Mac. Use iFarm to rotate IP
addresses (cellular proxy), intercept SMS 2FA codes, scrape iOS app content with
a local VLM, and inject GPS/camera data.

## Prerequisites

Before calling any iFarm endpoint, verify the server is running and check which
capabilities are available:

```
GET http://127.0.0.1:7420/health
```

If that fails, start the server:
```
ifarm serve --port 7420
```

Then check capability status:
```
GET http://127.0.0.1:7420/api/status
```

Look at `overall` in the response:
- `"fully_ready"` — all features available
- `"automation_ready"` — network + automation available
- `"network_ready"` — only network (IP rotation and SMS) available
- `"not_ready"` — run `ifarm doctor` to fix prerequisites

Each item in `missing[]` has a `"fix"` field with the exact shell command needed.

---

## Device IDs

Get the UDID of connected iPhones:
```bash
idevice_id -l
```

Use the returned UDID string as the `udid` field in all device-specific requests.

---

## Endpoints

### Rotate IP address (get a fresh cellular IP)

```
POST http://127.0.0.1:7420/proxy/rotate
Content-Type: application/json

{"udid": "DEVICE-UDID"}
```

Response: `{"new_ip": "104.28.x.x"}`

Use this when an account gets rate-limited or IP-blocked.

### Establish cellular route

```
POST http://127.0.0.1:7420/proxy/establish
Content-Type: application/json

{"udid": "DEVICE-UDID"}
```

Response: `{"success": true}`

Call this once per device before the first proxy/rotate call.

### Get 2FA / OTP code from SMS

```
POST http://127.0.0.1:7420/sms/2fa
Content-Type: application/json

{
  "keyword": "code",
  "since_seconds": 60
}
```

Response: `{"code": "483921"}` or `{"code": null}`

- `keyword` filters messages to those containing that word (case-insensitive).
  Common values: `"code"`, `"verify"`, `"otp"`, `"pin"`. Use `""` to skip filtering.
- `since_seconds` is how far back to look (default 60).

### Scrape an iOS app feed

```
POST http://127.0.0.1:7420/scrape/feed
Content-Type: application/json

{
  "udid": "DEVICE-UDID",
  "bundle_id": "com.example.app",
  "swipes": 5,
  "extraction_prompt": null
}
```

Response:
```json
{
  "items": [
    {"username": "alice", "content": "post text", "like_count": 42}
  ],
  "count": 1
}
```

- `bundle_id` is the iOS app's bundle identifier (e.g. `"com.instagram.Instagram"`).
- `swipes` is how many times to scroll down (default 5).
- `extraction_prompt` overrides the default VLM extraction prompt. Leave null
  for the built-in social-feed extractor.

### Tap a UI element by text

```
POST http://127.0.0.1:7420/scrape/tap
Content-Type: application/json

{
  "udid": "DEVICE-UDID",
  "target_text": "Sign In"
}
```

Response: `{"tapped": true}` or `{"tapped": false}` if the element is not visible.

---

## Error Handling

All errors return HTTP 500 with a structured body:

```json
{
  "detail": {
    "error_code": "SMSError",
    "detail": "Cannot open chat.db: ...",
    "retryable": false
  }
}
```

HTTP 501 means the hardware feature is not yet implemented.

Common fixes:
- `DeviceNotFoundError` → check USB cable, run `idevice_id -l`
- `SMSError` → Terminal needs Full Disk Access in System Settings
- `VisionError` → ensure Ollama is running (`ollama serve`) and model is pulled

---

## Example Workflow — Account Creation with 2FA

```
1. POST /proxy/establish  {"udid": "MY-IPHONE-UDID"}
2. POST /proxy/rotate     {"udid": "MY-IPHONE-UDID"}   → get fresh IP
3. [create account on target site using the new IP]
4. [site sends SMS verification code to iPhone]
5. POST /sms/2fa          {"keyword": "code", "since_seconds": 120}
6. [submit the code to complete registration]
```
