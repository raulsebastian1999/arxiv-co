"""
arxiv_daily_digest_v3.py — Digest diario de arXiv math.CO

Cambios respecto a v2:
  - Solo se resumen los papers de ALTA relevancia.
  - Los de relevancia media: solo título + link (sin resumen).
  - Manejo robusto de errores: nunca se imprime el error en el email.
    Si Gemini falla, se usa silenciosamente el abstract original recortado.
  - Pausa entre requests para respetar rate limit del tier gratis (5/min).
  - Sin sección de "extras" (era ruido).
"""

import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage

import feedparser

# ============================================================
# CONFIGURACIÓN
# ============================================================

RESUMEN_MODE = "gemini"  # "none", "gemini", o "groq"
VENTANA_HORAS = 48

# Pausa entre requests al API (segundos). Tier gratis Gemini = 5 req/min,
# así que 13s entre requests da margen de seguridad.
PAUSA_ENTRE_REQUESTS = 30

# Email
DESTINATARIOS = [
    # "arxiv-co@googlegroups.com",  # CAMBIAR por tu Google Group
  "raulsebastian1999@gmail.com",
  "constanza.gacitua.f@gmail.com"
]
REMITENTE = os.environ.get("GMAIL_USER", "")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# APIs
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Intereses: nivel ALTO (autores + keywords muy específicas)
AUTORES = [
    "Maya Stein",
    "Matias Pavez-Signe", "Matias Pavez-Signé",
    "Marcos Kiwi",
    "Hiep Han", "Hiep Hàn",
    "Nicolas Sanhueza-Matamala", "Nicolás Sanhueza-Matamala",
  "Jan Hladký, "Jan Hladky",
  "Frederik Garbe"
]

KEYWORDS_ALTAS = [
    "Ramsey", "Turán", "Turan",
    "monochromatic", "chromatic number", "edge coloring", "edge colouring",
    "tree cover", "tree partition", "biclique cover",
    "graphon", "graphons", "wordon", "wordons",
    "Gyárfás", "Gyarfas", "Lehel",
    "extremal graph", "extremal hypergraph",
    "random graph threshold", "random hypergraph",
    "spanning subgraph", "Hamilton cycle", "Hamiltonian",
    "saturation", "anti-Ramsey","graphon", "random graph", "graph limit","quasirandom permutation",
    "quasirandom graph", "quasirandom words",
    "word limit",
]

# Intereses: nivel MEDIO (keywords genéricas — solo título + link en email)
KEYWORDS_MEDIAS = [
    "hypergraph", "hypergraphs",
    "bipartite", "tripartite",
    "combinatorial", "combinatorics",
    "extremal", "probabilistic",
    "coloring", "colouring",
    
]
# Nota: saqué "graph" y "graphs" porque matchean casi todo math.CO.

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
    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return pub_date >= cutoff


def classify_paper(entry):
    text = f"{entry.title} {entry.summary}".lower()
    authors_str = ", ".join(a.name for a in entry.authors).lower()
    razones_altas, razones_medias = [], []
    
    for autor in AUTORES:
        if autor.lower() in authors_str:
            razones_altas.append(f"autor: {autor}")
    
    for kw in KEYWORDS_ALTAS:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
            razones_altas.append(f"keyword: {kw}")
    
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


def summary_fallback(abstract):
    """Fallback silencioso: abstract original recortado a 50 palabras."""
    cleaned = re.sub(r"\s+", " ", abstract).strip()
    return truncate_words(cleaned, 50)


def summary_gemini(title, abstract):
    """Intenta resumir con Gemini. Si falla, devuelve fallback (sin imprimir error)."""
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
        # Silencioso: log a stdout pero NO al email
        print(f"   (Gemini error, usando fallback: {type(e).__name__})")
        return summary_fallback(abstract)


def summary_groq(title, abstract):
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        prompt = (
            f"Resumí en español, máximo 50 palabras. NO inventes nada.\n\n"
            f"Título: {title}\n\nAbstract:\n{abstract}"
        )
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"   (Groq error, usando fallback: {type(e).__name__})")
        return summary_fallback(abstract)


def generate_summary(title, abstract):
    if RESUMEN_MODE == "gemini":
        return summary_gemini(title, abstract)
    elif RESUMEN_MODE == "groq":
        return summary_groq(title, abstract)
    else:
        return summary_fallback(abstract)


def format_alta(paper, razones, summary):
    title = paper.title.replace("\n", " ").strip()
    authors = ", ".join(a.name for a in paper.authors)
    arxiv_id = paper.id.split("/abs/")[-1]
    link = paper.link
    pub_date = datetime(*paper.published_parsed[:6], tzinfo=timezone.utc)
    pub_str = pub_date.strftime("%Y-%m-%d")
    
    return "\n".join([
        f"📄 {title}",
        f"   {authors}",
        f"   arXiv:{arxiv_id} ({pub_str}) — {link}",
        f"   Match: {', '.join(razones[:3])}",
        f"",
        f"   {summary}",
        f"",
    ])


def format_media(paper, razones):
    """Solo título + link, compacto."""
    title = paper.title.replace("\n", " ").strip()
    arxiv_id = paper.id.split("/abs/")[-1]
    link = paper.link
    return f"• {title}\n  arXiv:{arxiv_id} — {link}\n"


def format_email(altas, medias, ventana):
    today_str = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"📚 arXiv math.CO — {today_str}  ({ventana}h)",
        "=" * 70,
        "",
    ]
    
    if not altas and not medias:
        lines.append("Sin papers nuevos de interés en esta ventana.")
        return "\n".join(lines)
    
    if altas:
        lines.append(f"🟢 ALTA RELEVANCIA ({len(altas)})")
        lines.append("-" * 70)
        lines.append("")
        for paper, razones, summary in altas:
            lines.append(format_alta(paper, razones, summary))
    
    if medias:
        lines.append(f"🟡 OTROS POSIBLEMENTE RELEVANTES ({len(medias)})")
        lines.append("-" * 70)
        for paper, razones in medias:
            lines.append(format_media(paper, razones))
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
    print(f"🔍 arXiv {ARXIV_CATEGORY}, ventana {VENTANA_HORAS}h, modo {RESUMEN_MODE}")
    
    entries = fetch_recent_papers()
    recientes = [e for e in entries if is_in_window(e, VENTANA_HORAS)]
    print(f"   Recibidos: {len(entries)} | En ventana: {len(recientes)}")
    
    altas_raw, medias_raw = [], []
    for entry in recientes:
        nivel, razones = classify_paper(entry)
        if nivel == "alta":
            altas_raw.append((entry, razones))
        elif nivel == "media":
            medias_raw.append((entry, razones))
    
    print(f"   Alta: {len(altas_raw)} | Media: {len(medias_raw)}")
    
    # Solo resumir alta relevancia, con pausa entre requests
    altas = []
    for i, (entry, razones) in enumerate(altas_raw, 1):
        print(f"   [{i}/{len(altas_raw)}] {entry.title[:55]}...")
        summary = generate_summary(entry.title, entry.summary)
        altas.append((entry, razones, summary))
        # Pausa entre requests para respetar rate limit (excepto en el último)
        if i < len(altas_raw) and RESUMEN_MODE in ("gemini", "groq"):
            time.sleep(PAUSA_ENTRE_REQUESTS)
    
    body = format_email(altas, medias_raw, VENTANA_HORAS)
    today_str = datetime.now().strftime("%Y-%m-%d")
    n_alta = len(altas)
    n_media = len(medias_raw)
    subject = f"[arXiv math.CO {today_str}] {n_alta} alta + {n_media} otros"
    
    if "--dry-run" in sys.argv:
        print("\n--- DRY RUN ---\n")
        print(body)
        return
    
    print(f"📧 Enviando a: {', '.join(DESTINATARIOS)}")
    send_email(subject, body, DESTINATARIOS)
    print("✓ Listo.")


if __name__ == "__main__":
    main()
