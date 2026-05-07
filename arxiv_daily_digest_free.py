"""
arxiv_daily_digest_free.py — Digest diario de arXiv math.CO, 100% gratis

Tres modos de resumen, configurables al inicio:
  - "none"   : usa el abstract original recortado (sin IA, totalmente gratis)
  - "gemini" : Google Gemini API (gratis sin tarjeta, hasta 1500 req/día)
  - "groq"   : Groq API con Llama (gratis sin tarjeta, hasta 30 req/min)

CONFIGURACIÓN:
  - Editar la variable RESUMEN_MODE más abajo.
  - Configurar emails y APIs en variables de entorno (ver workflow .yml).

DEPENDENCIAS según modo:
  pip install feedparser           # siempre
  pip install google-generativeai  # solo si RESUMEN_MODE = "gemini"
  pip install groq                 # solo si RESUMEN_MODE = "groq"
"""

import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage

import feedparser

# ============================================================
# CONFIGURACIÓN — EDITAR ACÁ
# ============================================================

# Modo de resumen: "none", "gemini", o "groq"
RESUMEN_MODE = "gemini"

# --- Email ---
DESTINATARIOS = [
    # Si tenés Google Group, usá solo eso:
    # "tu-grupo@googlegroups.com",
    # O emails individuales:
    "raulsebastian1999@gmail.com",
    "constanza.gacitua.f@gmail.com",
]

REMITENTE = os.environ.get("GMAIL_USER", "tu_email@gmail.com")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# --- APIs (según modo) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# --- Intereses ---
AUTORES = [
    "Maya Stein",
    "Matias Pavez-Signe", "Matias Pavez-Signé",
    "Marcos Kiwi",
    "Hiep Han", "Hiep Hàn",
    "Nicolas Sanhueza-Matamala", "Nicolás Sanhueza-Matamala",
]

KEYWORDS = [
    "graph", "graphs", "hypergraph", "bipartite",
    "extremal", "Ramsey", "Turán", "chromatic", "coloring", "colouring",
    "monochromatic", "tree cover", "tree partition",
    "random graph", "probabilistic", "threshold",
    "graphon", "graphons", "wordon", "wordons", "limit",
    "combinatorial", "combinatorics",
]

ARXIV_CATEGORY = "math.CO"
MAX_RESULTS = 100
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


def is_today(entry):
    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return pub_date.date() == datetime.now(timezone.utc).date()


def matches_interests(entry):
    text = f"{entry.title} {entry.summary}".lower()
    authors_str = ", ".join(a.name for a in entry.authors).lower()
    reasons = []
    for autor in AUTORES:
        if autor.lower() in authors_str:
            reasons.append(f"autor: {autor}")
    for kw in KEYWORDS:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
            reasons.append(f"keyword: {kw}")
    return (len(reasons) > 0, reasons)


def truncate_words(text, max_words=50):
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


# --- Modo "none": sin IA ---
def summary_none(title, abstract):
    """Solo recorta el abstract original a 50 palabras."""
    cleaned = re.sub(r"\s+", " ", abstract).strip()
    return truncate_words(cleaned, 50)


# --- Modo "gemini": Google Gemini API ---
def summary_gemini(title, abstract):
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
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
        return f"[Error Gemini: {e}] Abstract original: {truncate_words(abstract, 50)}"


# --- Modo "groq": Groq API con Llama ---
def summary_groq(title, abstract):
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        prompt = (
            f"Resumí el siguiente paper de matemática en español, máximo 50 palabras. "
            f"Sé concreto: enunciá el problema, el resultado principal y la técnica. "
            f"NO inventes nada que no esté en el abstract original.\n\n"
            f"Título: {title}\n\nAbstract:\n{abstract}\n\n"
            f"Resumen (máximo 50 palabras):"
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Error Groq: {e}] Abstract original: {truncate_words(abstract, 50)}"


def generate_summary(title, abstract):
    if RESUMEN_MODE == "none":
        return summary_none(title, abstract)
    elif RESUMEN_MODE == "gemini":
        return summary_gemini(title, abstract)
    elif RESUMEN_MODE == "groq":
        return summary_groq(title, abstract)
    else:
        return summary_none(title, abstract)


def format_email(papers_summarized):
    today_str = datetime.now().strftime("%Y-%m-%d")
    if not papers_summarized:
        return f"Reporte arXiv math.CO — {today_str}\n\nNo hay papers nuevos de interés hoy."
    
    lines = [
        f"📚 Reporte arXiv math.CO — {today_str}",
        f"Total de papers de interés: {len(papers_summarized)}",
        f"Modo de resumen: {RESUMEN_MODE}",
        "=" * 70,
        "",
    ]
    
    for paper, reasons, summary in papers_summarized:
        title = paper.title.replace("\n", " ").strip()
        authors = ", ".join(a.name for a in paper.authors)
        link = paper.link
        arxiv_id = paper.id.split("/abs/")[-1]
        
        lines.append(f"📄 {title}")
        lines.append(f"   Autores: {authors}")
        lines.append(f"   arXiv: {arxiv_id}")
        lines.append(f"   Link: {link}")
        lines.append(f"   Coincide por: {', '.join(reasons[:3])}")
        lines.append(f"")
        lines.append(f"   Resumen: {summary}")
        lines.append("")
        lines.append("-" * 70)
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
    print(f"   Modo de resumen: {RESUMEN_MODE}")
    
    entries = fetch_recent_papers()
    print(f"   Recibidos: {len(entries)} papers.")
    
    matches = []
    for entry in entries:
        if not is_today(entry):
            continue
        ok, reasons = matches_interests(entry)
        if ok:
            matches.append((entry, reasons))
    print(f"   De hoy y de interés: {len(matches)}")
    
    if not matches:
        print("   Nada que enviar hoy.")
        if "--dry-run" not in sys.argv:
            # Igual mandamos un email vacío para confirmar que el sistema corre
            today_str = datetime.now().strftime("%Y-%m-%d")
            send_email(
                f"[arXiv math.CO] Sin papers nuevos — {today_str}",
                f"Reporte arXiv math.CO — {today_str}\n\nNo hay papers nuevos de interés hoy.",
                DESTINATARIOS,
            )
        return
    
    print(f"📝 Generando resúmenes (modo: {RESUMEN_MODE})...")
    summarized = []
    for i, (entry, reasons) in enumerate(matches, 1):
        print(f"   [{i}/{len(matches)}] {entry.title[:60]}...")
        summary = generate_summary(entry.title, entry.summary)
        summarized.append((entry, reasons, summary))
    
    body = format_email(summarized)
    today_str = datetime.now().strftime("%Y-%m-%d")
    subject = f"[arXiv math.CO] Resúmenes diarios — {today_str}"
    
    if "--dry-run" in sys.argv:
        print("\n--- DRY RUN ---\n")
        print(body)
        return
    
    print(f"📧 Enviando a: {', '.join(DESTINATARIOS)}")
    send_email(subject, body, DESTINATARIOS)
    print("✓ Listo.")


if __name__ == "__main__":
    main()
