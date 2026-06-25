"""Remotive adapter — fetch remote jobs and normalize to the common schema.

Remotive public API: https://remotive.com/api/remote-jobs
- Ücretsiz, anahtar gerektirmez.
- İlanlar 24 saat gecikmeli; günde ~4 çağrıdan fazlası önerilmez.
- Bu adaptör storage'i BİLMEZ; sadece dış veriyi şemamıza çevirir.
"""

import hashlib
from datetime import datetime, timezone

import requests

BASE_URL = "https://remotive.com/api/remote-jobs"
SOURCE = "remotive"
TIMEOUT = 30  # saniye — askıda kalmamak için


def fetch_remotive(category: str = "data", limit: int = 50) -> list[dict]:
    """Remotive'den ham ilan listesi çek.

    category: Remotive kategori slug'u (örn. 'data', 'software-dev').
    limit: kaç ilan istendiği (API 'limit' parametresi).
    Döner: ham iş dict'lerinin listesi (henüz normalize edilmemiş).
    """
    params = {"category": category, "limit": limit}
    headers = {"User-Agent": "job-search-orchestrator (personal use)"}

    resp = requests.get(BASE_URL, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()  # 4xx/5xx → exception (sessiz hata yok)

    payload = resp.json()
    jobs = payload.get("jobs", [])
    print(f"[fetch] {SOURCE}/{category}: {len(jobs)} ilan geldi")
    return jobs


def _content_hash(title: str, description: str) -> str:
    """title+description'tan kısa bir hash — ilan değişti mi tespiti için."""
    raw = f"{title}\n{description}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def normalize_remotive(raw: dict) -> dict:
    """Tek bir ham Remotive ilanını ortak şemamıza çevir.

    Remotive alanları → bizim kolonlar:
      id                        -> external_id
      company_name              -> company
      candidate_required_location -> location
    """
    title = raw.get("title") or ""
    description = raw.get("description") or ""

    return {
        "source": SOURCE,
        "external_id": str(raw.get("id")),
        "url": raw.get("url"),
        "title": title,
        "company": raw.get("company_name"),
        "category": raw.get("category"),
        "job_type": raw.get("job_type"),
        "location": raw.get("candidate_required_location"),
        "salary": raw.get("salary") or None,
        "description": description,
        "publication_date": raw.get("publication_date"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_hash": _content_hash(title, description),
    }


# Mini test: python -m src.adapters.remotive
# Canlı API'yi çağırır (internet gerekir), DB'ye YAZMAZ.
if __name__ == "__main__":
    raw_jobs = fetch_remotive(category="data", limit=3)

    if not raw_jobs:
        print("[uyarı] Hiç ilan gelmedi — kategori slug'u yanlış olabilir.")
    else:
        first = normalize_remotive(raw_jobs[0])
        print("\n[normalize] İlk ilan, şemamıza çevrilmiş hali:")
        for key, value in first.items():
            preview = str(value)
            if len(preview) > 70:
                preview = preview[:70] + "..."
            print(f"  {key:18} = {preview}")
