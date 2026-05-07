"""
arxiv_daily_digest_v2.py — Digest diario de arXiv math.CO, mejorado

Cambios respecto a v1:
  - Ventana configurable (24h, 48h, 72h) para no perder los lunes después
    del fin de semana.
  - Dos niveles de relevancia: alta (autores + keywords específicas) y
    media (keywords genéricas).
  - Tres secciones en el email: alta relevancia, media relevancia, y
    también hoy en math.CO (todo, sin filtro).
"""

import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage

import feedparser

# ============================================================
# CONFIGURACIÓN
# ============================================================

RESUMEN_MODE = "gemini"  # "none", "gemini", o "groq"

# Cuántas horas atrás considerar como "reciente"
# 24 = solo hoy (UTC)
# 48 = últimas 48 horas (recomendado para no perder ayer)
# 72 = últimas 72 horas (útil para los lunes)
VENTANA_HORAS = 48

# Cuántos papers extra mostrar en la sección "También hoy en math.CO"
# (de los que no matchearon filtros, los más recientes)
EXTRA_PAPERS = 10

# --- Email ---
DESTINATARIOS = [
    # "arxiv-co@googlegroups.com",  # CAMBIAR por tu Google Group
  "raulsebastian1999@gmail.com",
  "constanza.gacitua.f@gmail.com"
]
REMITENTE = os.environ.get("GMAIL_USER", "")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# --- APIs ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# --- Intereses: nivel ALTO (matchea por autor o keyword específica) ---
AUTORES = [
    "Maya Stein",
    "Matias Pavez-Signe", "Matias Pavez-Signé",
    "Marcos Kiwi",
    "Hiep Han", "Hiep Hàn",
    "Nicolas Sanhueza-Matamala", "Nicolás Sanhueza-Matamala",
]

KEYWORDS_ALTAS = [
    # Específicas: si aparecen, casi seguro es relevante
    "Ramsey", "Turán", "Turan",
    "monochromatic", "chromatic number", "edge coloring", "edge colouring",
    "tree cover", "tree partition", "biclique cover",
    "graphon", "graphons", "wordon", "wordons",
    "Gyárfás", "Gyarfas", "Lehel",
    "extremal graph", "extremal hypergraph",
    "random graph threshold", "random hypergraph",
    "spanning subgraph", "Hamilton cycle", "Hamiltonian",
    "saturation", "anti-Ramsey",
]

# --- Intereses: nivel MEDIO (keywords genéricas, área general) ---
KEYWORDS_MEDIAS = [
    "graph", "graphs", "hypergraph", "hypergraphs",
    "bipartite", "tripartite",
    "combinatorial", "combinatorics",
    "extremal", "probabilistic",
    "coloring", "colouring", "partition",
    "limit", "convergence",
]

ARXIV_CATEGORY = "math.CO"
MAX_RESULTS = 200
ARXIV_API = "http://export.arxiv.org/api/query"


# ============================================================
# Funciones
# ============================================================

def fetch_recent_papers():
    url = (
        f"{ARXIV_API}?search_query=cat:{ARXIV_CATEGORY}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={MAX_RESULTS}"
    )
    return feedparser.parse(url).entries


def is_in_window(entry, hours):
    """¿El paper fue publicado en las últimas `hours` horas?"""
    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return pub_date >= cutoff


def classify_paper(entry):
    """Clasifica un paper como 'alta', 'media' o 'baja' relevancia.
    Devuelve (nivel, razones)."""
    text = f"{entry.title} {entry.summary}".lower()
    authors_str = ", ".join(a.name for a in entry.authors).lower()
    
    razones_altas = []
    razones_medias = []
    
    # Autores
    for autor in AUTORES:
        if autor.lower() in authors_str:
            razones_altas.append(f"autor: {autor}")
    
    # Keywords altas
    for kw in KEYWORDS_ALTAS:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
            razones_altas.append(f"keyword: {kw}")
    
    # Keywords medias
    for kw in KEYWORDS_MEDIAS:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
            razones_medias.append(f"keyword: {kw}")
    
    if razones_altas:
        return ("alta", razones_altas)
    elif razones_medias:
        return ("media", razones_medias)
    else:
        return ("baja", [])


def truncate_words(text, max_words=50):
    words = text.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


def summary_none(title, abstract):
    cleaned = re.sub(r"\s+", " ", abstract).strip()
    return truncate_words(cleaned, 50)


def summary_gemini(title, abstract):
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = (
            f"Resumí el siguiente paper de matemática en español, máximo 50 palabras. "
            f"Sé concreto: enunciá el problema, el resultado principal y la técnica. "
            f"NO inventes nada que no esté en el abstract original.\n\n"
            f"Título: {title}\n\nAbstract:\n{abstract}\n\n"
            f"Resumen (máximo 50 palabras):"
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"[Error Gemini: {e}] Original: {truncate_words(abstract, 40)}"


def summary_groq(title, abstract):
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        prompt = (
            f"Resumí el siguiente paper de matemática en español, máximo 50 palabras. "
            f"NO inventes nada.\n\nTítulo: {title}\n\nAbstract:\n{abstract}"
        )
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"[Error Groq: {e}] Original: {truncate_words(abstract, 40)}"


def generate_summary(title, abstract):
    if RESUMEN_MODE == "gemini":
        return summary_gemini(title, abstract)
    elif RESUMEN_MODE == "groq":
        return summary_groq(title, abstract)
    else:
        return summary_none(title, abstract)


def format_paper_block(paper, razones, summary, include_full=True):
    title = paper.title.replace("\n", " ").strip()
    authors = ", ".join(a.name for a in paper.authors)
    arxiv_id = paper.id.split("/abs/")[-1]
    link = paper.link
    pub_date = datetime(*paper.published_parsed[:6], tzinfo=timezone.utc)
    pub_str = pub_date.strftime("%Y-%m-%d")
    
    lines = [
        f"📄 {title}",
        f"   Autores: {authors}",
        f"   arXiv: {arxiv_id}  ({pub_str})",
        f"   Link: {link}",
    ]
    if razones:
        lines.append(f"   Coincide por: {', '.join(razones[:3])}")
    if include_full and summary:
        lines.append(f"")
        lines.append(f"   Resumen: {summary}")
    lines.append("")
    return "\n".join(lines)


def format_email(altas, medias, extras, ventana):
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    lines = [
        f"📚 Reporte arXiv math.CO — {today_str}",
        f"Ventana: últimas {ventana} horas",
        f"Modo de resumen: {RESUMEN_MODE}",
        "=" * 70,
        "",
    ]
    
    if not altas and not medias and not extras:
        lines.append("No hay papers en math.CO para esta ventana.")
        return "\n".join(lines)
    
    # ALTA RELEVANCIA — con resumen completo
    if altas:
        lines.append(f"🟢 ALTA RELEVANCIA ({len(altas)} papers)")
        lines.append("-" * 70)
        lines.append("")
        for paper, razones, summary in altas:
            lines.append(format_paper_block(paper, razones, summary, include_full=True))
        lines.append("")
    
    # MEDIA RELEVANCIA — con resumen completo
    if medias:
        lines.append(f"🟡 RELEVANCIA MEDIA ({len(medias)} papers)")
        lines.append("-" * 70)
        lines.append("")
        for paper, razones, summary in medias:
            lines.append(format_paper_block(paper, razones, summary, include_full=True))
        lines.append("")
    
    # EXTRAS — sin resumen, solo título y link
    if extras:
        lines.append(f"⚪ TAMBIÉN EN math.CO ({len(extras)} papers, sin resumir)")
        lines.append("-" * 70)
        lines.append("")
        for paper in extras:
            lines.append(format_paper_block(paper, [], None, include_full=False))
        lines.append("")
    
    return "\n".join(lines)


def send_email(subject, body, destinatarios):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = REMITENTE
    msg["To"] = ", ".join(destinatarios)
    msg.set_content(body)
    
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(REMITENTE, APP_PASSWORD)
        server.send_message(msg)


def main():
    print(f"🔍 Buscando papers en arXiv {ARXIV_CATEGORY}...")
    print(f"   Ventana: últimas {VENTANA_HORAS}h. Modo: {RESUMEN_MODE}")
    
    entries = fetch_recent_papers()
    print(f"   Recibidos: {len(entries)} papers desde la API.")
    
    # Filtrar por ventana
    recientes = [e for e in entries if is_in_window(e, VENTANA_HORAS)]
    print(f"   En la ventana de {VENTANA_HORAS}h: {len(recientes)}")
    
    # Clasificar
    altas_raw, medias_raw, bajas_raw = [], [], []
    for entry in recientes:
        nivel, razones = classify_paper(entry)
        if nivel == "alta":
            altas_raw.append((entry, razones))
        elif nivel == "media":
            medias_raw.append((entry, razones))
        else:
            bajas_raw.append(entry)
    
    print(f"   Alta: {len(altas_raw)} | Media: {len(medias_raw)} | Baja: {len(bajas_raw)}")
    
    # Generar resúmenes para alta y media
    print(f"📝 Generando resúmenes...")
    altas = []
    for i, (entry, razones) in enumerate(altas_raw, 1):
        print(f"   [alta {i}/{len(altas_raw)}] {entry.title[:50]}...")
        summary = generate_summary(entry.title, entry.summary)
        altas.append((entry, razones, summary))
    
    medias = []
    for i, (entry, razones) in enumerate(medias_raw, 1):
        print(f"   [media {i}/{len(medias_raw)}] {entry.title[:50]}...")
        summary = generate_summary(entry.title, entry.summary)
        medias.append((entry, razones, summary))
    
    # Extras: los más recientes que no clasificaron, sin resumen
    extras = bajas_raw[:EXTRA_PAPERS]
    
    # Armar y mandar
    body = format_email(altas, medias, extras, VENTANA_HORAS)
    today_str = datetime.now().strftime("%Y-%m-%d")
    n_total = len(altas) + len(medias) + len(extras)
    subject = f"[arXiv math.CO] {n_total} papers — {today_str}"
    
    if "--dry-run" in sys.argv:
        print("\n--- DRY RUN ---\n")
        print(body)
        return
    
    print(f"📧 Enviando a: {', '.join(DESTINATARIOS)}")
    send_email(subject, body, DESTINATARIOS)
    print("✓ Listo.")


if __name__ == "__main__":
    main()
