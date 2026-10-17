#!/usr/bin/env python3
"""DownGrab blog sync: scrape public posts of a Telegram channel (t.me/s/<channel>)
and regenerate the static blog (blog/index.html, blog/<id>.html, blog/img/*) + sitemap.xml.

Pure standard library (runs on GitHub Actions without pip).
Touches ONLY: blog/  and  sitemap.xml.

  python .github/scripts/blog_sync.py            # fetch + rebuild (run from site root)
  python .github/scripts/blog_sync.py --offline  # rebuild from blog/posts.json, no network
Env: SITE_ROOT (default: two levels above this file), CHANNEL (default DownGrab), BASE_URL.

Manual content for posts the web preview can't show (Telegram "rich" posts, "Please open Telegram"):
  .github/blog-manual/posts.json   {"5": {"title": "...", "desc": "...", "html": "5.html", "image": "5-1.jpg", "date": "ISO"}}
  .github/blog-manual/5.html       trusted HTML fragment (h2, p, ul, figure/img src="img/...", blockquote)
  blog/img/5-1.jpg                 images referenced by manual posts are never deleted by the sync.
Manual entries always win over scraped content and are kept even if the scrape misses the post.
(Legacy: blog/manual.json {"5": {"text": "plain text"}} still works.)
"""
import html, json, os, re, sys, time, urllib.request
from datetime import datetime
from html.parser import HTMLParser

ROOT = os.environ.get("SITE_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHANNEL = os.environ.get("CHANNEL", "DownGrab")
BASE_URL = os.environ.get("BASE_URL", "https://downgrab.github.io").rstrip("/")
BOT = "https://t.me/DownGrabBot?start=blog"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
BLOG = os.path.join(ROOT, "blog")
IMG = os.path.join(BLOG, "img")
MANUAL_DIR = os.path.join(ROOT, ".github", "blog-manual")
LANDING = {"ru": "/", "en": "/en/", "uk": "/uk/"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
ALLOWED = {"b", "strong", "i", "em", "u", "s", "del", "code", "pre", "blockquote", "a", "br"}

# ---- share block (same code in downgrab-site-build.py and .github/scripts/blog_sync.py) ----
from urllib.parse import quote as _q
SHARE_ICONS = {
 "tg": '<path d="M9.78 15.27l-.4 5.6c.57 0 .82-.25 1.12-.54l2.69-2.57 5.58 4.08c1.02.57 1.75.27 2.02-.94L24.45 3.2c.33-1.5-.54-2.09-1.53-1.72L1.4 9.73c-1.47.57-1.45 1.39-.25 1.76l5.5 1.71 12.77-8.05c.6-.4 1.15-.18.7.22" transform="translate(-.3 0) scale(.96)"/>',
 "wa": '<path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm0 1.8a8.2 8.2 0 1 1-4.2 15.2l-.3-.2-3 .8.8-2.9-.2-.3A8.2 8.2 0 0 1 12 3.8zm-3.3 4.4c-.2 0-.5 0-.7.3-.3.3-1 1-1 2.4s1 2.8 1.2 3c.1.2 2 3.2 5 4.4 2.5 1 3 .8 3.5.7.6 0 1.8-.7 2-1.4.3-.7.3-1.3.2-1.4l-.6-.4-2-1c-.3 0-.5-.1-.7.2l-.9 1.1c-.2.2-.3.2-.6.1-.3-.2-1.3-.5-2.4-1.5-.9-.8-1.5-1.8-1.7-2.1-.2-.3 0-.5.1-.6l.5-.5.3-.5v-.5l-.9-2.2c-.2-.6-.5-.5-.7-.5h-.6z"/>',
 "vk": '<path d="M13.2 18C6.6 18 2.8 13.5 2.6 6h3.3c.1 5.5 2.6 7.8 4.5 8.3V6h3.1v4.7c1.9-.2 3.9-2.4 4.6-4.7h3.1c-.5 2.9-2.7 5-4.2 5.9 1.5.7 4 2.6 4.9 6.1h-3.4c-.7-2.3-2.6-4.1-5-4.4V18h-.3z"/>',
 "x": '<path d="M17.8 3h3.1l-6.8 7.7L22 21h-6.2l-4.9-6.4L5.3 21H2.2l7.2-8.3L1.8 3h6.4l4.4 5.8L17.8 3zm-1.1 16.2h1.7L7.4 4.7H5.6l11.1 14.5z"/>',
 "copy": '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4L11.8 5.8M14 10a4.5 4.5 0 0 0-6.4 0l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1.4-1.4"/>',
 "ok": '<path fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" d="M5 12.5l4.5 4.5L19 7.5"/>',
 "share": '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M12 3v12M7.5 7.5 12 3l4.5 4.5M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/>',
}
SHARE_L10N = {
 "ru": {"tg": "Поделиться в Telegram", "wa": "Поделиться в WhatsApp", "vk": "Поделиться во ВКонтакте", "x": "Поделиться в X", "copy": "Скопировать ссылку", "copied": "Ссылка скопирована", "share": "Поделиться…"},
 "en": {"tg": "Share on Telegram", "wa": "Share on WhatsApp", "vk": "Share on VK", "x": "Share on X", "copy": "Copy link", "copied": "Link copied", "share": "Share…"},
 "uk": {"tg": "Поділитися в Telegram", "wa": "Поділитися у WhatsApp", "vk": "Поділитися у VK", "x": "Поділитися в X", "copy": "Скопіювати посилання", "copied": "Посилання скопійовано", "share": "Поділитися…"},
}
SHARE_CSS = """.share{display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:10px}
.share .sl{color:var(--mut);font-size:15px;margin-right:4px}
.share a,.share button{display:inline-grid;place-items:center;width:42px;height:42px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--mut);cursor:pointer;padding:0;transition:color .15s,border-color .15s,transform .15s}
.share a:hover,.share button:hover,.share a:focus-visible,.share button:focus-visible{color:var(--txt);border-color:#3a4a66;transform:translateY(-1px);text-decoration:none}
.share svg{width:20px;height:20px;fill:currentColor}
.share [hidden]{display:none}
.share .ok{color:#3ddc84;border-color:#2c6b48}
.share .tip{font-size:13px;color:#3ddc84;min-width:1px}
@media(max-width:480px){.share .sl{flex-basis:100%;text-align:center;margin:0}.shrow .share .sl{text-align:left}}"""
SHARE_JS = ("<script>(function(){document.querySelectorAll('.share').forEach(function(b){var u=b.dataset.url,t=b.dataset.text;"
 "var n=b.querySelector('.sh-native');if(n&&navigator.share&&matchMedia('(pointer:coarse)').matches){n.hidden=false;n.onclick=function(){navigator.share({title:document.title,text:t,url:u}).catch(function(){})}}"
 "var c=b.querySelector('.sh-copy'),tip=b.querySelector('.tip');if(c){c.hidden=false;var ic=c.innerHTML;c.onclick=function(){var done=function(){c.classList.add('ok');c.innerHTML=b.dataset.okicon;if(tip)tip.textContent=c.dataset.done;"
 "setTimeout(function(){c.classList.remove('ok');c.innerHTML=ic;if(tip)tip.textContent=''},2000)};"
 "if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(u).then(done,function(){prompt('',u)})}else{prompt('',u)}}}})})()</script>")

def share_block(url, text, lang, label):
    L = SHARE_L10N[lang]; e = lambda s: html.escape(s, True)
    svg = lambda k: f'<svg viewBox="0 0 24 24" aria-hidden="true">{SHARE_ICONS[k]}</svg>'
    links = [("tg", f"https://t.me/share/url?url={_q(url, safe='')}&text={_q(text, safe='')}"),
             ("wa", f"https://wa.me/?text={_q(text + ' ' + url, safe='')}"),
             ("vk", f"https://vk.com/share.php?url={_q(url, safe='')}"),
             ("x", f"https://twitter.com/intent/tweet?url={_q(url, safe='')}&text={_q(text, safe='')}")]
    a = "".join(f'<a href="{e(h)}" target="_blank" rel="noopener nofollow" aria-label="{e(L[k])}" title="{e(L[k])}">{svg(k)}</a>' for k, h in links)
    return (f'<div class="share" role="group" aria-label="{e(label)}" data-url="{e(url)}" data-text="{e(text)}" data-okicon="{e(svg("ok"))}">'
            f'<span class="sl">{e(label)}</span>'
            f'<button type="button" class="sh-native" hidden aria-label="{e(L["share"])}" title="{e(L["share"])}">{svg("share")}</button>{a}'
            f'<button type="button" class="sh-copy" hidden aria-label="{e(L["copy"])}" title="{e(L["copy"])}" data-done="{e(L["copied"])}">{svg("copy")}</button>'
            f'<span class="tip" aria-live="polite"></span></div>')
# ---- end share block ----

def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")

class Sanitizer(HTMLParser):
    """Keep simple formatting, drop everything else; emoji <i class=emoji><b>X</b></i> -> X."""
    def __init__(self):
        super().__init__(convert_charrefs=True); self.out = []; self.stack = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs); cls = a.get("class") or ""
        if tag == "br": self.out.append("<br>"); return
        if tag == "i" and "emoji" in cls: self.stack.append(None); return
        if tag == "span" and "tg-spoiler" in cls: tag = "span-spoiler"
        if tag == "a":
            href = a.get("href") or ""
            if href.startswith("//"): href = "https:" + href
            if not re.match(r"^(https?://|tg://)", href): self.stack.append(None); return
            ext = "" if re.match(r"^https://t\.me/", href) else ' rel="nofollow noopener"'
            self.out.append(f'<a href="{html.escape(href, True)}"{ext}>'); self.stack.append("a"); return
        if tag == "span-spoiler": self.out.append('<span class="sp">'); self.stack.append("span"); return
        if tag in ALLOWED: self.out.append(f"<{tag}>"); self.stack.append(tag); return
        self.stack.append(None)
    def handle_startendtag(self, tag, attrs):
        if tag == "br": self.out.append("<br>")
    def handle_endtag(self, tag):
        if tag == "br" or not self.stack: return
        t = self.stack.pop()
        if t: self.out.append(f"</{t}>")
    def handle_data(self, d): self.out.append(html.escape(d, False))

def sanitize(fragment):
    s = Sanitizer(); s.feed(fragment); s.close()
    out = "".join(s.out).strip()
    return re.sub(r"(<br>\s*){3,}", "<br><br>", out)

def html_to_text(fragment):
    t = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()

class ChannelPage(HTMLParser):
    """Collects posts from t.me/s/<channel>."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.posts = []; self.cur = None; self.depth = 0
        self.cap = None  # (kind, depth_at_start, buffer)
    def _raw(self):
        return self.get_starttag_text() or ""
    def handle_starttag(self, tag, attrs):
        a = dict(attrs); cls = a.get("class") or ""
        if tag == "div" and "tgme_widget_message " in cls + " " and a.get("data-post"):
            pid = int(a["data-post"].split("/")[-1])
            self.cur = {"id": pid, "text_html": "", "photos": [], "videos": 0, "service": "service_message" in cls,
                        "unsupported": False, "date": "", "views": "", "reply": False}
            self.posts.append(self.cur); self.depth = 0
        if self.cur is None: return
        if tag in ("div", "a", "span", "i", "b", "u", "s", "code", "pre", "blockquote", "time", "em", "strong", "tg-spoiler", "del"):
            self.depth += 1
        if self.cap is not None:
            if tag != "br": self.cap[2].append(self._raw())
            else: self.cap[2].append("<br>")
            return
        st = a.get("style") or ""
        if "tgme_widget_message_reply" in cls: self.cur["reply"] = True
        if "js-message_text" in cls and not self.cur["text_html"]:
            self.cap = ("text", self.depth, [])
        elif "tgme_widget_message_photo_wrap" in cls or "tgme_widget_message_video_thumb" in cls or "link_preview_image" in cls:
            m = re.search(r"background-image:url\('([^']+)'\)", st)
            if m and "link_preview_image" not in cls: self.cur["photos"].append(m.group(1))
            if "video_thumb" in cls: self.cur["videos"] += 1
        elif "message_media_not_supported" in cls:
            self.cur["unsupported"] = True
        elif tag == "time" and a.get("datetime") and not self.cur["date"]:
            self.cur["date"] = a["datetime"]
        elif "tgme_widget_message_views" in cls:
            self.cap = ("views", self.depth, [])
    def handle_startendtag(self, tag, attrs):
        if self.cap is not None and self.cur is not None:
            self.cap[2].append("<br>" if tag == "br" else self._raw())
    def handle_endtag(self, tag):
        if self.cur is None: return
        if self.cap is not None:
            if tag in ("div", "a", "span", "i", "b", "u", "s", "code", "pre", "blockquote", "time", "em", "strong", "tg-spoiler", "del") and self.depth == self.cap[1]:
                kind, _, buf = self.cap; self.cap = None
                if kind == "text": self.cur["text_html"] = "".join(buf)
                else: self.cur["views"] = re.sub(r"<[^>]+>", "", "".join(buf)).strip()
            elif tag != "br":
                self.cap[2].append(f"</{tag}>")
        if tag in ("div", "a", "span", "i", "b", "u", "s", "code", "pre", "blockquote", "time", "em", "strong", "tg-spoiler", "del"):
            self.depth -= 1
    def handle_data(self, d):
        if self.cap is not None: self.cap[2].append(d)
    def handle_entityref(self, name):
        if self.cap is not None: self.cap[2].append(f"&{name};")
    def handle_charref(self, name):
        if self.cap is not None: self.cap[2].append(f"&#{name};")

def scrape(channel, max_pages=60):
    posts, before = {}, None
    for _ in range(max_pages):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        p = ChannelPage(); p.feed(fetch(url)); p.close()
        new = [x for x in p.posts if x["id"] not in posts]
        for x in new: posts[x["id"]] = x
        if not new: break
        lo = min(x["id"] for x in p.posts)
        if lo <= 1 or (before and lo >= before): break
        before = lo; time.sleep(1)
    return [posts[k] for k in sorted(posts)]

def clip(s, n):
    if len(s) <= n: return s
    return s[:n].rsplit(" ", 1)[0].rstrip(",.:;—-") + "…"

def ru_date(iso):
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    except Exception:
        return ""

def download_images(post):
    os.makedirs(IMG, exist_ok=True); local = []
    for i, url in enumerate(post.get("photos", []), 1):
        if url.startswith("//"): url = "https:" + url
        ext = ".jpg" if not re.search(r"\.(png|webp)(\?|$)", url) else "." + re.search(r"\.(png|webp)", url).group(1)
        name = f"{post['id']}_{i}{ext}"; path = os.path.join(IMG, name)
        if not os.path.exists(path):
            try:
                with open(path, "wb") as f: f.write(fetch(url, binary=True))
            except Exception as ex:
                print("image failed", url[:80], ex, file=sys.stderr); continue
        local.append(name)
    return local

def load_manual():
    manual = {}
    legacy = os.path.join(BLOG, "manual.json")
    if os.path.exists(legacy): manual.update(json.load(open(legacy, encoding="utf-8")))
    idx = os.path.join(MANUAL_DIR, "posts.json")
    if os.path.exists(idx):
        for k, v in json.load(open(idx, encoding="utf-8")).items():
            if v.get("html"):
                v = dict(v); v["fragment"] = open(os.path.join(MANUAL_DIR, v["html"]), encoding="utf-8").read().strip()
            manual[str(k)] = {**manual.get(str(k), {}), **v}
    return manual

def manual_images(manual):
    keep = set()
    for m in manual.values():
        if m.get("image"): keep.add(m["image"])
        keep.update(re.findall(r'src="img/([^"]+)"', m.get("fragment", "")))
    return keep

def normalize(raw, manual):
    """Turn scraped posts into blog entries (skip service posts and posts without usable content)."""
    entries, seen = [], set()
    by_id = {p["id"]: p for p in raw}
    ids = sorted(set(by_id) | {int(k) for k in manual})
    for pid in ids:
        p = by_id.get(pid, {"id": pid})
        m = manual.get(str(pid), {})
        if p.get("service") and not m: continue
        body = sanitize(p.get("text_html", "")) if p.get("text_html") else ""
        imgs = p.get("images") or []
        cover = imgs[0] if imgs else None
        rich = False
        if m.get("fragment"):
            body, imgs, rich = m["fragment"], [], True
            cover = m.get("image") or (re.findall(r'src="img/([^"]+)"', body) or [None])[0]
        elif m.get("text"):
            body = "<br>".join(html.escape(line) for line in m["text"].strip().split("\n"))
        if not body and not imgs:
            continue  # nothing to show (service/empty, or web preview says "open in Telegram")
        text = html_to_text(re.sub(r"</(p|h2|h3|li|blockquote|figcaption)>", "\n", body))
        first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
        title = m.get("title") or (first[:80] + ("…" if len(first) > 80 else "")) or f"Пост #{pid}"
        desc = m.get("desc") or re.sub(r"\s+", " ", text)[:180]
        entries.append({"id": pid, "title": title, "desc": desc, "body": body, "images": imgs, "cover": cover, "rich": rich,
                        "videos": 0 if rich else p.get("videos", 0), "date": p.get("date") or m.get("date", "")})
    entries.sort(key=lambda e: e["id"], reverse=True)
    return entries

CSS = """:root{--bg:#0b0d12;--bg2:#12151d;--card:#161a24;--line:#232838;--txt:#e9ecf3;--mut:#9aa3b5;--acc:#2aabee;--acc2:#7c5cff}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}body{margin:0;font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif;background:var(--bg);color:var(--txt)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}.w{max-width:760px;margin:0 auto;padding:0 20px}
header{position:sticky;top:0;z-index:5;background:rgba(11,13,18,.85);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.nav{display:flex;align-items:center;justify-content:space-between;height:60px;max-width:1080px}
.logo{display:flex;align-items:center;gap:8px;font-weight:800;font-size:20px;color:var(--txt)}.logo:hover{text-decoration:none}.logo i{font-style:normal;font-size:26px}
.links{display:flex;gap:4px;align-items:center}.links a{padding:6px 10px;border-radius:8px;color:var(--mut);font-size:14px;font-weight:600}.links a[aria-current]{background:var(--card);color:var(--txt);border:1px solid var(--line)}.links a:hover{text-decoration:none;color:var(--txt)}
h1{font-size:clamp(28px,5vw,40px);line-height:1.15;margin:36px 0 8px;letter-spacing:-.02em}.sub{color:var(--mut);margin:0 0 28px}
.post{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;margin:0 0 16px;display:block;color:var(--txt)}
a.post:hover{text-decoration:none;border-color:#33405a}.post h2{font-size:19px;margin:0 0 6px;line-height:1.3}.post p{margin:0;color:var(--mut)}
.meta{color:var(--mut);font-size:13px;margin-bottom:8px}.thumb{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:12px;margin-bottom:12px;background:var(--bg2)}
article .body{font-size:17px}article .body blockquote{border-left:3px solid var(--acc);margin:12px 0;padding:4px 14px;color:var(--mut)}
article .body pre,article .body code{background:var(--bg2);border-radius:6px;padding:2px 6px}.sp{background:var(--line);color:transparent;border-radius:4px}.sp:hover{color:inherit}
article .body h2{font-size:23px;line-height:1.3;margin:30px 0 10px}article .body p{margin:0 0 14px}article .body ul{margin:0 0 16px;padding-left:22px}article .body li{margin:0 0 8px}
article figure{margin:22px 0}article figure img{width:100%;height:auto;display:block;border-radius:14px}article figcaption{color:var(--mut);font-size:14px;margin-top:8px}
article .body.rich blockquote{border-left:3px solid var(--acc);background:rgba(42,171,238,.08);border-radius:0 10px 10px 0;margin:22px 0;padding:12px 16px;color:var(--txt);font-size:17px}
.gal{display:grid;gap:8px;margin:16px 0}.gal img{width:100%;height:auto;border-radius:12px;display:block}
.note{color:var(--mut);font-size:14px;margin-top:10px}
.btns{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0}.btn{display:inline-flex;align-items:center;gap:8px;min-height:48px;padding:0 20px;border-radius:14px;font-weight:700;border:1px solid var(--line);color:var(--txt);background:var(--card)}
.btn:hover{text-decoration:none;filter:brightness(1.1)}.btn.p{background:linear-gradient(135deg,#2aabee,#229ed9);border:0;color:#fff}
.empty{text-align:center;padding:40px 20px}.empty .em{font-size:56px}
""" + SHARE_CSS + """
article+.shrow{margin:28px 0 0;padding:18px 0 0;border-top:1px solid var(--line)}article+.shrow .share{justify-content:flex-start}
footer{border-top:1px solid var(--line);padding:24px 0 32px;margin-top:40px;color:var(--mut);font-size:14px;text-align:center}footer a{margin:0 8px}"""

def shell(title, desc, canonical, body, rel, og_image=None, ld=None, current="blog", js=""):
    og = og_image or f"{BASE_URL}/og.png"
    ldj = f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>' if ld else ""
    e = lambda s: html.escape(s, True)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<meta name="theme-color" content="#0b0d12">
<meta name="color-scheme" content="dark">
<link rel="canonical" href="{canonical}">
<link rel="icon" href="{rel}favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="{rel}apple-touch-icon.png">
<link rel="alternate" type="application/rss+xml" title="DownGrab — блог" href="{BASE_URL}/blog/feed.xml">
<meta property="og:type" content="{'article' if current == 'post' else 'website'}">
<meta property="og:site_name" content="DownGrab">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og}">
<meta property="og:locale" content="ru_RU">
<meta name="twitter:card" content="summary_large_image">
{ldj}
<style>{CSS}</style>
</head>
<body>
<header><div class="w nav"><a class="logo" href="{rel}"><i aria-hidden="true">😺</i>DownGrab</a>
<nav class="links"><a href="{rel}">Главная</a><a href="{rel}blog/"{' aria-current="page"' if current == 'blog' else ''}>Блог</a><a href="{BOT}">Бот</a></nav></div></header>
<main class="w">
{body}
</main>
<footer><a href="{BOT}">🤖 @DownGrabBot</a><a href="https://t.me/{CHANNEL}">📢 @{CHANNEL}</a><a href="https://t.me/kartkino">👤 @kartkino</a><br>© 2026 DownGrab 😺</footer>
{js}</body>
</html>
"""

def render(entries, keep_imgs=frozenset()):
    os.makedirs(BLOG, exist_ok=True)
    e = lambda s: html.escape(s, True)
    cards = []
    for p in entries:
        thumb = f'<img class="thumb" src="img/{p["cover"]}" alt="" loading="lazy">' if p["cover"] else ""
        meta = ru_date(p["date"])
        cards.append(f'<a class="post" href="{p["id"]}.html">{thumb}<div class="meta">{e(meta)}</div><h2>{e(p["title"])}</h2><p>{e(clip(p["desc"], 170))}</p></a>')
    if cards:
        listing = "\n".join(cards)
    else:
        listing = (f'<div class="post empty"><div class="em">😺</div><p>Скоро здесь появятся новости.<br>'
                   f'Пока читайте наш канал в Telegram.</p><div class="btns" style="justify-content:center">'
                   f'<a class="btn p" href="https://t.me/{CHANNEL}">📢 Открыть канал @{CHANNEL}</a></div></div>')
    body = (f'<h1>Блог DownGrab</h1><p class="sub">Новости и обновления бота @DownGrabBot — копия постов из канала '
            f'<a href="https://t.me/{CHANNEL}">@{CHANNEL}</a>.</p>{listing}'
            f'<div class="btns"><a class="btn p" href="{BOT}">🤖 Открыть бота</a><a class="btn" href="https://t.me/{CHANNEL}">📢 Подписаться на канал</a></div>')
    ld = {"@context": "https://schema.org", "@type": "Blog", "name": "Блог DownGrab", "url": f"{BASE_URL}/blog/", "inLanguage": "ru"}
    write(os.path.join(BLOG, "index.html"), shell("Блог DownGrab — новости бота для скачивания видео",
          "Новости и обновления DownGrab (@DownGrabBot) — Telegram-бота для скачивания видео из YouTube, TikTok, Pinterest и других сайтов.",
          f"{BASE_URL}/blog/", body, "../", ld=ld))
    keep = {"index.html", "posts.json", "manual.json", "feed.xml", "img"}
    for p in entries:
        name = f'{p["id"]}.html'; keep.add(name)
        gal = "".join(f'<img src="img/{i}" alt="" loading="lazy">' for i in p["images"])
        vid = f'<p class="note">🎬 В посте есть видео — смотрите его в Telegram.</p>' if p["videos"] else ""
        meta = ru_date(p["date"])
        body = (f'<article><h1>{e(p["title"])}</h1><div class="meta">{e(meta)}</div>'
                f'{f"<div class=gal>{gal}</div>" if gal else ""}<div class="body{' rich' if p['rich'] else ''}">{p["body"]}</div>{vid}</article>'
                f'<div class="shrow">{share_block(f"{BASE_URL}/blog/{name}", p["title"] + " — DownGrab 😺", "ru", "Поделиться:")}</div>'
                f'<div class="btns"><a class="btn p" href="{BOT}">🤖 Скачать видео в @DownGrabBot</a>'
                f'<a class="btn" href="https://t.me/{CHANNEL}/{p["id"]}">✈️ Открыть пост в Telegram</a></div>'
                f'<p><a href="./">← Все посты</a></p>')
        og = f'{BASE_URL}/blog/img/{p["cover"]}' if p["cover"] else None
        ld = {"@context": "https://schema.org", "@type": "BlogPosting", "headline": p["title"][:110], "datePublished": p["date"],
              "url": f'{BASE_URL}/blog/{name}', "inLanguage": "ru", "author": {"@type": "Organization", "name": "DownGrab"},
              "mainEntityOfPage": f'{BASE_URL}/blog/{name}'}
        if og: ld["image"] = og
        write(os.path.join(BLOG, name), shell(f'{p["title"]} — блог DownGrab', p["desc"] or p["title"],
              f'{BASE_URL}/blog/{name}', body, "../", og_image=og, ld=ld, current="post", js=SHARE_JS))
    # remove stale post pages / images
    used = {i for p in entries for i in p["images"]} | set(keep_imgs) | {p["cover"] for p in entries if p["cover"]}
    for f in os.listdir(BLOG):
        if f not in keep and re.match(r"^\d+\.html$", f): os.remove(os.path.join(BLOG, f))
    if os.path.isdir(IMG):
        for f in os.listdir(IMG):
            if f not in used: os.remove(os.path.join(IMG, f))
    # RSS
    items = "".join(f'<item><title>{e(p["title"])}</title><link>{BASE_URL}/blog/{p["id"]}.html</link><guid>{BASE_URL}/blog/{p["id"]}.html</guid>'
                    f'<description>{e(p["desc"])}</description></item>' for p in entries[:30])
    write(os.path.join(BLOG, "feed.xml"), f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Блог DownGrab</title>'
          f'<link>{BASE_URL}/blog/</link><description>Новости DownGrab</description><language>ru</language>{items}</channel></rss>\n')

def write_sitemap(entries):
    alts = "".join(f'<xhtml:link rel="alternate" hreflang="{l}" href="{BASE_URL}{p}"/>' for l, p in LANDING.items())
    alts += f'<xhtml:link rel="alternate" hreflang="x-default" href="{BASE_URL}/"/>'
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    out += [f"<url><loc>{BASE_URL}{p}</loc>{alts}</url>" for p in LANDING.values()]
    out.append(f"<url><loc>{BASE_URL}/blog/</loc></url>")
    out += [f'<url><loc>{BASE_URL}/blog/{p["id"]}.html</loc>' + (f'<lastmod>{p["date"][:10]}</lastmod>' if p["date"] else "") + "</url>" for p in entries]
    out.append("</urlset>\n")
    write(os.path.join(ROOT, "sitemap.xml"), "\n".join(out))

def write(path, content):
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old != content:
        with open(path, "w", encoding="utf-8") as f: f.write(content)

def main():
    offline = "--offline" in sys.argv
    os.makedirs(BLOG, exist_ok=True)
    cache = os.path.join(BLOG, "posts.json")
    manual = load_manual()
    if offline:
        raw = json.load(open(cache, encoding="utf-8")) if os.path.exists(cache) else []
    else:
        raw = scrape(CHANNEL)
        if not raw:
            print("no posts scraped — keeping existing blog", file=sys.stderr); return 0
        for p in raw:
            p["images"] = download_images(p)
            p.pop("photos", None)
            p.pop("views", None)  # view counts change constantly -> would cause a commit every run
        for p in raw: p.pop("text_html_clean", None)
        write(cache, json.dumps(raw, ensure_ascii=False, indent=1) + "\n")
    entries = normalize(raw, manual)
    render(entries, manual_images(manual)); write_sitemap(entries)
    skipped = [p["id"] for p in raw if not p.get("service") and p["id"] not in {e["id"] for e in entries}]
    print(f"blog: {len(entries)} posts rendered" + (f"; skipped (service/empty or not web-visible; add to .github/blog-manual/ if needed): {skipped}" if skipped else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
