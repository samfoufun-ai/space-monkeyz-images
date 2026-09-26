#!/usr/bin/env python3
import csv, io, re, time, unicodedata
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "personalities.csv"
OUT_DIR = ROOT / "personalities"
MANIFEST = ROOT / "manifest.csv"
FAILED = ROOT / "failed.csv"
RAW_BASE = "https://raw.githubusercontent.com/samfoufun-ai/space-monkeyz-images/main/personalities"

UA = "SpaceMonkeyzImageBuilder/2.0 (educational project; GitHub Actions)"
session = requests.Session()
retry = Retry(
    total=5, connect=4, read=4, status=5,
    backoff_factor=0.8,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    respect_retry_after_header=True,
)
session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8))
session.headers.update({"User-Agent": UA})

ALIASES = {
    "Sting": "Sting (musician)",
    "Prince": "Prince (musician)",
    "Drake": "Drake (musician)",
    "Michou": "Michou (YouTuber)",
}

def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "personality"

def batch_pageimages(names, lang):
    out = {}
    for start in range(0, len(names), 50):
        original = names[start:start+50]
        titles = [ALIASES.get(n, n) for n in original]
        params = {
            "action":"query","format":"json","redirects":"1",
            "prop":"pageimages","piprop":"thumbnail|original",
            "pithumbsize":"700","titles":"|".join(titles)
        }
        r = session.get(f"https://{lang}.wikipedia.org/w/api.php", params=params, timeout=45)
        r.raise_for_status()
        data = r.json().get("query", {})
        alias = {t:t for t in titles}
        for item in data.get("normalized", []) or []:
            alias[item.get("from","")] = item.get("to","")
        for item in data.get("redirects", []) or []:
            alias[item.get("from","")] = item.get("to","")
        # Resolve chains: requested -> normalized -> redirect target.
        for _ in range(3):
            for k,v in list(alias.items()):
                if v in alias and alias[v] != v:
                    alias[k] = alias[v]
        pages = {p.get("title",""):p for p in (data.get("pages", {}) or {}).values()}
        pages_cf = {k.casefold():v for k,v in pages.items()}
        for requested, query_title in zip(original, titles):
            target = alias.get(query_title, query_title)
            p = pages.get(target) or pages_cf.get(target.casefold())
            if not p or "missing" in p:
                continue
            src = (p.get("thumbnail") or p.get("original") or {}).get("source")
            if not src:
                continue
            title = p.get("title", target)
            out[requested] = {
                "source_image":src,
                "resolved_title":title,
                "source_page":f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                "wiki_lang":lang,
            }
        time.sleep(0.45)
    return out

def search_one(name, lang):
    params = {
        "action":"query","format":"json","generator":"search","gsrsearch":name,
        "gsrlimit":"4","prop":"pageimages","piprop":"thumbnail|original","pithumbsize":"700"
    }
    r = session.get(f"https://{lang}.wikipedia.org/w/api.php", params=params, timeout=45)
    r.raise_for_status()
    pages = list((r.json().get("query",{}).get("pages",{}) or {}).values())
    pages.sort(key=lambda x:x.get("index",999))
    for p in pages:
        src=(p.get("thumbnail") or p.get("original") or {}).get("source")
        if src:
            title=p.get("title",name)
            return {
                "source_image":src,
                "resolved_title":title,
                "source_page":f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                "wiki_lang":lang,
            }
    return None

def download_as_jpg(url, dest):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((700,700), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=86, optimize=True, progressive=True)

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows=list(csv.DictReader(f))
    names=[r["name"].strip() for r in rows]

    print(f"Resolving {len(names)} personalities in batched Wikipedia queries...")
    resolved = batch_pageimages(names, "fr")
    unresolved=[n for n in names if n not in resolved]
    print(f"French Wikipedia: {len(resolved)} images; {len(unresolved)} unresolved")

    if unresolved:
        en = batch_pageimages(unresolved, "en")
        resolved.update(en)
        unresolved=[n for n in unresolved if n not in resolved]
        print(f"After English Wikipedia: {len(resolved)} images; {len(unresolved)} unresolved")

    # Only ambiguous/mismatched names require individual search.
    for idx,name in enumerate(list(unresolved),1):
        hit=None
        for lang in ("fr","en"):
            try:
                hit=search_one(name,lang)
                if hit: break
            except Exception as e:
                print(f"Search warning {name}/{lang}: {e}")
        if hit:
            resolved[name]=hit
        time.sleep(0.35)
    print(f"Resolved total: {len(resolved)}/{len(names)}")

    manifest=[]
    failed=[]
    for i,row in enumerate(rows,1):
        name=row["name"].strip()
        category=row["category"].strip()
        filename=slugify(name)+".jpg"
        dest=OUT_DIR/filename
        hit=resolved.get(name)
        if not hit:
            err="aucune image Wikipedia/Commons trouvée"
            failed.append({"id":row["id"],"name":name,"category":category,"error":err})
            manifest.append({
                "id":row["id"],"name":name,"category":category,
                "filename":"","raw_url":"","source_page":"","source_image":"",
                "wiki_lang":"","resolved_title":"","status":"failed"
            })
            print(f"[{i:03d}/{len(rows)}] MISS {name}")
            continue
        try:
            download_as_jpg(hit["source_image"], dest)
            raw_url=f"{RAW_BASE}/{filename}"
            manifest.append({
                "id":row["id"],"name":name,"category":category,
                "filename":filename,"raw_url":raw_url,
                "source_page":hit["source_page"],"source_image":hit["source_image"],
                "wiki_lang":hit["wiki_lang"],"resolved_title":hit["resolved_title"],"status":"ok"
            })
            print(f"[{i:03d}/{len(rows)}] OK   {name}")
        except Exception as e:
            err=f"échec téléchargement/conversion: {e}"
            failed.append({"id":row["id"],"name":name,"category":category,"error":err})
            manifest.append({
                "id":row["id"],"name":name,"category":category,
                "filename":"","raw_url":"","source_page":hit["source_page"],
                "source_image":hit["source_image"],"wiki_lang":hit["wiki_lang"],
                "resolved_title":hit["resolved_title"],"status":"failed"
            })
            print(f"[{i:03d}/{len(rows)}] ERR  {name}: {e}")
        time.sleep(0.10)

    fields=["id","name","category","filename","raw_url","source_page","source_image","wiki_lang","resolved_title","status"]
    with MANIFEST.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(manifest)
    with FAILED.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["id","name","category","error"]); w.writeheader(); w.writerows(failed)
    print(f"Done: {len(manifest)-len(failed)} ok, {len(failed)} failed")

if __name__=="__main__":
    main()
