import re
import urllib.parse
import requests

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,th;q=0.8',
}

def unpack_packer(packed_js):
    """Simple unpacker for eval(function(p,a,c,k,e,d)...)"""
    try:
        pattern = r"}\('(.*)',\s*(\d+),\s*(\d+),\s*'(.*?)'\.split\('\|'\)"
        m = re.search(pattern, packed_js, re.DOTALL)
        if not m:
            return ""
        payload, radix, count, words = m.groups()
        radix = int(radix)
        count = int(count)
        word_list = words.split('|')

        def baseN(num, b):
            digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if num == 0:
                return digits[0]
            res = ""
            while num:
                res = digits[num % b] + res
                num //= b
            return res

        for i in range(count - 1, -1, -1):
            key = baseN(i, radix)
            rep = word_list[i] if i < len(word_list) and word_list[i] else key
            payload = re.sub(r'\b' + re.escape(key) + r'\b', rep, payload)
        return payload
    except Exception:
        return ""

def resolve_vidmonstr(url, session):
    """Specialized handler for vidmonstr.com and vidoy.com embeds"""
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.lower()
    base_url = f"{parsed.scheme}://{domain}"

    r = session.get(url, headers={'Referer': base_url})
    if r.status_code != 200:
        return None

    page_title = None
    title_m = re.search(r'<title>(.*?)</title>', r.text, re.IGNORECASE)
    if title_m:
        page_title = title_m.group(1).strip()

    # Find iframe ID and embedToken
    iframe_id_match = re.search(r'iframeId\s*=\s*[\'"]([^\'"]+)[\'"]', r.text)
    embed_token_match = re.search(r'embedToken\s*=\s*[\'"]([^\'"]+)[\'"]', r.text)

    # Sometimes directly has iframe src="/ip...?"
    iframe_src_match = re.search(r'iframe\.src\s*=\s*[\'"]([^\'"]+)[\'"]', r.text)

    iframe_url = None
    if iframe_id_match and embed_token_match:
        iframe_path = f"/ip129jk?id={iframe_id_match.group(1)}&t={embed_token_match.group(1)}"
        iframe_url = urllib.parse.urljoin(base_url, iframe_path)
    elif iframe_src_match:
        iframe_url = urllib.parse.urljoin(base_url, iframe_src_match.group(1))

    if not iframe_url:
        return None

    # Fetch intermediate iframe
    r2 = session.get(iframe_url, headers={'Referer': url})
    if r2.status_code != 200:
        return None

    # Search for playerPath or stream.php
    player_match = re.search(r'playerPath\s*=\s*[\'"]([^\'"]+)[\'"]', r2.text) or \
                   re.search(r'href=[\'"]([^\'"]*stream\.php[^\'"]*)[\'"]', r2.text)
    
    if not player_match:
        return None

    player_url = player_match.group(1).replace(r'\u0026', '&').replace('&amp;', '&')
    player_url = urllib.parse.urljoin(base_url, player_url)

    # Fetch player page (stream.php)
    r3 = session.get(player_url, headers={'Referer': iframe_url})
    if r3.status_code != 200:
        return None

    # Look for video source or m3u8 in stream.php
    src_match = re.search(r'<source\s+[^>]*src=[\'"]([^\'"]+)[\'"]', r3.text, re.IGNORECASE)
    if not src_match:
        src_match = re.search(r'[\'"](https?://[^\'"]+?\.(?:mp4|m3u8)[^\'"]*)[\'"]', r3.text)

    title_match = re.search(r'title:\s*[\'"]([^\'"]+)[\'"]', r3.text) or \
                  re.search(r'<title>(.*?)</title>', r3.text)
    if title_match:
        page_title = title_match.group(1).strip()

    if src_match:
        stream_url = src_match.group(1)
        return {
            'resolved_url': stream_url,
            'title': page_title or 'video',
            'headers': {'Referer': base_url}
        }

    return None

def resolve_generic_webpage(url, session):
    """Scrapes generic webpage for embedded video tags, m3u8, or mp4 streams"""
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return None

        content = r.text

        # 1. Check for unpacked code
        if 'eval(function(p,a,c,k,e,d)' in content:
            unpacked = unpack_packer(content)
            content += "\n" + unpacked

        # 2. Check for direct <video> or <source>
        src_match = re.search(r'<(?:video|source)\s+[^>]*src=[\'"]([^\'"]+?\.(?:mp4|m3u8|webm)[^\'"]*)[\'"]', content, re.IGNORECASE)
        if src_match:
            stream_url = urllib.parse.urljoin(url, src_match.group(1))
            return {
                'resolved_url': stream_url,
                'title': get_title_from_html(content),
                'headers': {'Referer': url}
            }

        # 3. Check for .m3u8 or .mp4 URL inside JavaScript variables
        m3u8_match = re.search(r'[\'"](https?://[^\'"\s<>]+?\.(?:m3u8|mp4)[^\'"\s<>]*)[\'"]', content)
        if m3u8_match:
            stream_url = m3u8_match.group(1)
            return {
                'resolved_url': stream_url,
                'title': get_title_from_html(content),
                'headers': {'Referer': url}
            }

        # 4. Check for nested iframes (e.g. video embed players)
        iframe_matches = re.findall(r'<iframe\s+[^>]*src=[\'"]([^\'"]+)[\'"]', content, re.IGNORECASE)
        for ifr_src in iframe_matches:
            if ifr_src.startswith('//'):
                ifr_src = 'https:' + ifr_src
            elif ifr_src.startswith('/'):
                ifr_src = urllib.parse.urljoin(url, ifr_src)

            if not ifr_src.startswith('http'):
                continue

            # Don't follow ad networks
            if any(ad in ifr_src for ad in ['google', 'doubleclick', 'facebook', 'adservice']):
                continue

            try:
                ifr_r = session.get(ifr_src, headers={'Referer': url}, timeout=10)
                ifr_text = ifr_r.text
                if 'eval(function(p,a,c,k,e,d)' in ifr_text:
                    ifr_text += "\n" + unpack_packer(ifr_text)

                sub_src = re.search(r'<(?:video|source)\s+[^>]*src=[\'"]([^\'"]+?\.(?:mp4|m3u8)[^\'"]*)[\'"]', ifr_text, re.IGNORECASE)
                if sub_src:
                    return {
                        'resolved_url': urllib.parse.urljoin(ifr_src, sub_src.group(1)),
                        'title': get_title_from_html(content),
                        'headers': {'Referer': ifr_src}
                    }

                sub_m3u8 = re.search(r'[\'"](https?://[^\'"\s<>]+?\.(?:m3u8|mp4)[^\'"\s<>]*)[\'"]', ifr_text)
                if sub_m3u8:
                    return {
                        'resolved_url': sub_m3u8.group(1),
                        'title': get_title_from_html(content),
                        'headers': {'Referer': ifr_src}
                    }
            except Exception:
                continue

    except Exception:
        pass

    return None

def get_title_from_html(html):
    m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
    if m:
        t = m.group(1).strip()
        # Clean standard fluff like "Watch online - SiteName"
        t = re.sub(r'[\r\n\t]+', ' ', t)
        return t
    return "video"

def resolve_video_url(url):
    """
    Given a URL, attempts to resolve it if it is an embed or streaming host.
    Returns dict with {'resolved_url', 'title', 'headers'} or None if no resolution needed.
    """
    url = url.strip()
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    # 1. Check vidmonstr / vidoy
    if 'vidmonstr.com' in url or 'vidoy.com' in url or 'vidoy.net' in url or 'vidoy.asia' in url:
        res = resolve_vidmonstr(url, session)
        if res:
            return res

    # 2. If it's already a direct mp4 or m3u8
    if url.lower().endswith('.mp4') or '.mp4?' in url.lower() or url.lower().endswith('.m3u8') or '.m3u8?' in url.lower():
        return {
            'resolved_url': url,
            'title': 'video',
            'headers': {'Referer': url}
        }

    # 3. Generic scrape attempt
    return resolve_generic_webpage(url, session)
