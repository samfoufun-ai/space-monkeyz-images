#!/usr/bin/env python3
import csv, io, json, os, re, time, unicodedata
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "personalities.csv"
OUT_DIR = ROOT / "personalities"
MANIFEST = ROOT / "manifest.csv"
FAILED = ROOT / "failed.csv"
RAW_BASE = "https://raw.githubusercontent.com/samfoufun-ai/space-monkeyz-images/main/personalities"

UA = "SpaceMonkeyzImageBuilder/1.0 (educational project; GitHub Actions)"
session = requests.Session()
session.headers.update({"User-Agent": UA})

ALIASES = {
    "Pelé": "Pelé",
    "Neymar": "Neymar",
    "Sting": "Sting (musician)",
    "Bono": "Bono",
    "Prince": "Prince (musician)",
    "Drake": "Drake (musician)",
    "Sia": "Sia",
    "Banksy": "Banksy",
    "Molière": "Molière",
    "Cleopatra": "Cleopatra",
    "Michou": "Michou (YouTuber)",
    "Mister V": "Mister V",
    "Natoo": "Natoo",
    "HugoDécrypte": "HugoDécrypte",
    "Inoxtag": "Inoxtag",
}

def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "personality"

def wiki_thumb(name, lang):
    title = ALIASES.get(name, name)
    params = {
        "action": "query",
        "format": "json",
        "redirects": "1",
        "prop": "pageimages",
        "piprop": "thumbnail|original",
        "pithumbsize": "700",
        "titles": title,
    }
    r = session.get(f"https://{lang}.wikipedia.org/w/api.php", params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    pages = list(data.get("query", {}).get("pages", {}).values())
    if not pages or "missing" in pages[0]:
        return None
    p = pages[0]
    src = (p.get("thumbnail") or p.get("original") or {}).get("source")
    if not src:
        return None
    return src, p.get("title", title), f"https://{lang}.wikipedia.org/wiki/{quote(p.get('title', title).replace(' ', '_'))}"

def search_thumb(name, lang):
    # Fallback for names that are not exact page titles.
    params = {
        "action":"query","format":"json","generator":"search","gsrsearch":name,
        "gsrlimit":"3","prop":"pageimages","piprop":"thumbnail|original","pithumbsize":"700"
    }
    r=session.get(f"https://{lang}.wikipedia.org/w/api.php", params=params, timeout=25)
    r.raise_for_status()
    pages=list(r.json().get("query",{}).get("pages",{}).values())
    pages.sort(key=lambda x:x.get("index",999))
    for p in pages:
        src=(p.get("thumbnail") or p.get("original") or {}).get("source")
        if src:
            title=p.get("title",name)
            return src,title,f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
    return None

def fetch_image(name):
    for lang in ("fr","en"):
        try:
            hit=wiki_thumb(name,lang)
            if hit: return (*hit, lang)
        except Exception:
            pass
    for lang in ("fr","en"):
        try:
            hit=search_thumb(name,lang)
            if hit: return (*hit, lang)
        except Exception:
            pass
    return None

def download_as_jpg(url, dest):
    r=session.get(url, timeout=40)
    r.raise_for_status()
    img=Image.open(io.BytesIO(r.content))
    img=ImageOps.exif_transpose(img).convert("RGB")
    # Keep files compact but good enough for cards/quiz UI.
    img.thumbnail((700,700), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=86, optimize=True, progressive=True)

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest=[]
    failed=[]
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows=list(csv.DictReader(f))

    for i,row in enumerate(rows,1):
        name=row["name"].strip()
        category=row["category"].strip()
        filename=slugify(name)+".jpg"
        dest=OUT_DIR/filename
        try:
            hit=fetch_image(name)
            if not hit:
                raise RuntimeError("aucune image Wikipedia trouvée")
            source_url,page_title,page_url,lang=hit
            download_as_jpg(source_url,dest)
            raw_url=f"{RAW_BASE}/{filename}"
            manifest.append({
                "id":row["id"],"name":name,"category":category,
                "filename":filename,"raw_url":raw_url,
                "source_page":page_url,"source_image":source_url,
                "wiki_lang":lang,"resolved_title":page_title,"status":"ok"
            })
            print(f"[{i:03d}/{len(rows)}] OK  {name} -> {filename}")
        except Exception as e:
            failed.append({"id":row["id"],"name":name,"category":category,"error":str(e)})
            manifest.append({
                "id":row["id"],"name":name,"category":category,
                "filename":"","raw_url":"","source_page":"","source_image":"",
                "wiki_lang":"","resolved_title":"","status":"failed"
            })
            print(f"[{i:03d}/{len(rows)}] ERR {name}: {e}")
        time.sleep(0.08)

    fields=["id","name","category","filename","raw_url","source_page","source_image","wiki_lang","resolved_title","status"]
    with MANIFEST.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(manifest)
    with FAILED.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["id","name","category","error"]); w.writeheader(); w.writerows(failed)
    print(f"Done: {len(manifest)-len(failed)} ok, {len(failed)} failed")

if __name__=="__main__":
    main()

# trigger build
