# Proxy Configuration for YouTube Services

If you're experiencing IP blocking issues when running on cloud providers, you can configure proxies to route requests through different IP addresses. The same proxy configuration works for both transcript retrieval and video downloads.

## Setup

1. Create a `.env` file in the project root (or add to your existing `.env` file):

```bash
# HTTP Proxy (optional - if only one is set, it will be used for both)
YOUTUBE_PROXY_HTTP=http://username:password@proxy.example.com:8080

# HTTPS Proxy (optional - if only one is set, it will be used for both)
YOUTUBE_PROXY_HTTPS=https://username:password@proxy.example.com:8080
```

## Proxy URL Format

The proxy URL should follow this format:
- `http://proxy.example.com:8080` (no authentication)
- `http://user:pass@proxy.example.com:8080` (with authentication)
- `https://user:pass@proxy.example.com:8080` (HTTPS proxy)

## Examples

### Using a single proxy for both HTTP and HTTPS:
```bash
YOUTUBE_PROXY_HTTP=http://proxy.example.com:8080
```

### Using different proxies for HTTP and HTTPS:
```bash
YOUTUBE_PROXY_HTTP=http://proxy.example.com:8080
YOUTUBE_PROXY_HTTPS=https://proxy.example.com:8080
```

### Using authenticated proxy:
```bash
YOUTUBE_PROXY_HTTP=http://myuser:mypass@proxy.example.com:8080
```

## Where to Get Proxies

You can use various proxy services:

### Residential Proxies (Recommended)
- **Bright Data** (formerly Luminati) - https://brightdata.com
- **Smartproxy** - https://smartproxy.com
- **Oxylabs** - https://oxylabs.io
- **IPRoyal** - https://iproyal.com

Residential proxies use real home IP addresses, making them less likely to be blocked by YouTube.

### Datacenter Proxies
- **ProxyMesh** - https://proxymesh.com
- **MyPrivateProxy** - https://myprivateproxy.net
- **ProxyRack** - https://www.proxyrack.com

Datacenter proxies are faster but more likely to be blocked. They're cheaper but may require rotation.

### Free Proxies
- Generally not recommended for production use
- Often unreliable and may be blocked
- Security concerns with untrusted proxies

## Testing

After configuring proxies, restart your server:
```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

You should see messages in the logs when proxies are being used:

### For Transcript Retrieval:
```
🔒 Using proxy: HTTP=http://proxy.example.com:8080, HTTPS=https://proxy.example.com:8080
```

### For Video Downloads:
```
🔒 Using proxy for video download: ...@proxy.example.com:8080
🔒 Using proxy for pytube: ...@proxy.example.com:8080
```

## What Uses Proxies

The proxy configuration is used for:
1. **YouTube Transcript API** - When fetching video transcripts/subtitles
2. **Video Downloads (yt-dlp)** - When downloading videos via yt-dlp
3. **Video Downloads (pytube)** - Fallback method when yt-dlp fails

## Retry Logic

Both transcript retrieval and video downloads include automatic retry logic:
- **Default retries**: 3 attempts
- **Backoff strategy**: Exponential (5s, 10s, 20s delays)
- **Retries on**: IP blocking, network errors, temporary failures
- **No retry on**: Invalid URLs, missing videos, authentication errors

## Notes

- If proxy configuration fails, the system will continue without a proxy and log a warning
- The same proxy configuration works for both transcript retrieval and video downloads
- Make sure your proxy provider allows connections to YouTube's servers
- Residential proxies are recommended for better success rates
- Proxies with authentication should include credentials in the URL format: `http://user:pass@proxy.com:port`

