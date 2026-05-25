#!/usr/bin/env python3
"""Build quiz HTML + images from PDF."""
import fitz
import re
import json
import os
from io import BytesIO
from collections import defaultdict

try:
    from PIL import Image
except ImportError:
    Image = None

PDF = os.path.join(os.path.dirname(__file__), "sternik-motorowodny-pytania.pdf")
IMG_DIR = os.path.join(os.path.dirname(__file__), "quiz-images")
OUT_JSON = os.path.join(os.path.dirname(__file__), "quiz-data.json")
OUT_HTML = os.path.join(os.path.dirname(__file__), "quiz-sternik-motorowodny.html")

SKIP = {
    "L.P", "Pytanie", "Odpowiedź", "BAZA PYTAŃ",
    "STERNIK MOTOROWODNY", "WIND SAILING SCHOOL",
}


def is_yellow(fill):
    if not fill or len(fill) < 3:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    return r > 0.85 and g > 0.85 and b < 0.3


def rects_overlap(bbox, yellow_rects, thresh=0.15):
    for yr in yellow_rects:
        ir = bbox & yr
        if ir.is_empty:
            continue
        inter = ir.get_area()
        smaller = min(bbox.get_area(), yr.get_area())
        if smaller > 0 and inter / smaller > thresh:
            return True
    return False


def crop_jpeg(pix_bytes):
    if not Image:
        return pix_bytes
    img = Image.open(BytesIO(pix_bytes))
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = bg
    else:
        img = img.convert("RGB")
    bbox = img.convert("L").point(lambda x: 0 if x > 245 else 255).getbbox()
    if bbox:
        l, t, r, b = bbox
        img = img.crop((max(0, l - 4), max(0, t - 4), min(img.width, r + 4), min(img.height, b + 4)))
    out = BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


def parse_questions(doc):
    all_spans = []
    for pi in range(doc.page_count):
        page = doc[pi]
        yr = [d["rect"] for d in page.get_drawings() if is_yellow(d.get("fill"))]
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    bbox = fitz.Rect(span["bbox"])
                    t = span["text"].strip()
                    if not t:
                        continue
                    all_spans.append({
                        "page": pi + 1,
                        "text": t,
                        "x": bbox.x0,
                        "y": bbox.y0,
                        "yellow": rects_overlap(bbox, yr),
                    })

    all_spans.sort(key=lambda s: (s["page"], s["y"], s["x"]))
    raw = []
    current = None
    last_num = 0

    def push():
        nonlocal current
        if current and current.get("raw_num") is not None:
            raw.append(current)
        current = None

    for s in all_spans:
        x, t = s["x"], s["text"]
        if t in SKIP:
            continue

        m = re.match(r"^(\d{1,3})\s+(.+)$", t) if x < 55 else None
        if m:
            push()
            current = {
                "raw_num": int(m.group(1)),
                "q_parts": [m.group(2).strip()],
                "options": {},
                "correct": None,
                "page": s["page"],
                "y": s["y"],
            }
            last_num = current["raw_num"]
            continue

        if x < 55 and re.match(r"^\d{1,3}$", t):
            push()
            raw_num = int(t)
            if raw_num == 50 and last_num == 249:
                raw_num = 250
            current = {
                "raw_num": raw_num,
                "q_parts": [],
                "options": {},
                "correct": None,
                "page": s["page"],
                "y": s["y"],
            }
            last_num = raw_num
            continue

        if current is None:
            continue

        if 55 < x < 250 and not re.match(r"^[a-d]$", t, re.I):
            current["q_parts"].append(t)
            continue

        if 250 < x < 310 and re.match(r"^[a-d]$", t, re.I):
            letter = t.lower()
            if s["yellow"]:
                current["correct"] = letter
            current["options"].setdefault(letter, [])
            current["_opt_letter"] = letter
            continue

        if x > 310:
            letter = current.get("_opt_letter")
            if letter:
                current["options"].setdefault(letter, []).append(t)

    push()

    num_seen = {}
    by_num = {}
    for q in raw:
        n = q["raw_num"]
        num_seen[n] = num_seen.get(n, 0) + 1
        final = 62 if n == 61 and num_seen[n] == 2 else n
        qtext = re.sub(r"\s+", " ", " ".join(q["q_parts"])).strip()
        opts = {k: re.sub(r"\s+", " ", " ".join(v)).strip() for k, v in q["options"].items()}
        if not qtext or not opts:
            continue
        item = {
            "num": final,
            "question": qtext,
            "options": opts,
            "correct": q.get("correct"),
            "page": q["page"],
            "y": q["y"],
        }
        score = lambda x: (1 if x["correct"] else 0, len(x["question"]))
        if final not in by_num or score(item) > score(by_num[final]):
            by_num[final] = item

    fixes = {
        25: {"correct": "b"},
        58: {"correct": "a"},
        55: {"question": "Przedstawiony znak to:"},
        305: {"correct": "a", "question": "Udrażnianie dróg oddechowych polega na:"},
        185: {"correct": "a"},
    }
    for n, fix in fixes.items():
        if n in by_num:
            by_num[n].update(fix)

    return [by_num[k] for k in sorted(by_num.keys()) if by_num[k].get("correct")]


def extract_images(doc, questions):
    os.makedirs(IMG_DIR, exist_ok=True)
    page_images = {}
    for pi in range(doc.page_count):
        page = doc[pi]
        imgs = []
        for im in page.get_images(full=True):
            xref = im[0]
            for rect in page.get_image_rects(xref):
                if rect.width > 40 and rect.height > 40:
                    imgs.append({"xref": xref, "rect": rect, "y_mid": (rect.y0 + rect.y1) / 2})
        page_images[pi + 1] = sorted(imgs, key=lambda x: x["y_mid"])

    page_rows = defaultdict(list)
    last_num = 0
    for pi in range(doc.page_count):
        page = doc[pi]
        seen61 = 0
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    bbox = fitz.Rect(span["bbox"])
                    t = span["text"].strip()
                    if bbox.x0 >= 55:
                        continue
                    m = re.match(r"^(\d{1,3})\s", t)
                    if m:
                        n = int(m.group(1))
                    elif re.match(r"^\d{1,3}$", t):
                        n = int(t)
                        if n == 50 and last_num == 249:
                            n = 250
                    else:
                        continue
                    if n == 61:
                        seen61 += 1
                        if seen61 == 2:
                            n = 62
                    page_rows[pi + 1].append({"num": n, "y": bbox.y0})
                    last_num = n

    for page_num, imgs in page_images.items():
        if not imgs:
            continue
        rows = sorted(page_rows.get(page_num, []), key=lambda x: x["y"])
        available = list(imgs)
        assignments = []
        for row in rows:
            best_i, best_d = None, 9999
            for i, im in enumerate(available):
                d = abs(im["y_mid"] - row["y"] - 50)
                if d < best_d:
                    best_d, best_i = d, i
            if best_i is not None and best_d < 150:
                assignments.append((row["num"], available.pop(best_i)))

        page_doc = doc[page_num - 1]
        for qnum, im in assignments:
            pix = page_doc.get_pixmap(clip=im["rect"], matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            data = crop_jpeg(pix.tobytes("jpeg"))
            fname = f"q{qnum}.jpg"
            with open(os.path.join(IMG_DIR, fname), "wb") as f:
                f.write(data)

    # Assign images to questions; share nearest image for sign Q without own file
    sign_nums = {
        q["num"] for q in questions
        if "przedstawiony" in q["question"].lower() and "znak" in q["question"].lower()
    }
    for q in questions:
        path = os.path.join(IMG_DIR, f"q{q['num']}.jpg")
        if os.path.exists(path):
            q["image"] = f"quiz-images/q{q['num']}.jpg"

    for q in questions:
        if q.get("image") or q["num"] not in sign_nums:
            continue
        page = q["page"]
        imgs = page_images.get(page, [])
        if not imgs:
            continue
        nearest = min(imgs, key=lambda im: abs(im["y_mid"] - q["y"]))
        pix = doc[page - 1].get_pixmap(clip=nearest["rect"], matrix=fitz.Matrix(2.5, 2.5), alpha=False)
        data = crop_jpeg(pix.tobytes("jpeg"))
        fname = f"q{q['num']}.jpg"
        with open(os.path.join(IMG_DIR, fname), "wb") as f:
            f.write(data)
        q["image"] = f"quiz-images/{fname}"


def build_html(quiz_data):
    data_json = json.dumps(
        [{"id": q["num"], "question": q["question"], "options": q["options"],
          "correct": q["correct"], **({"image": q["image"]} if q.get("image") else {})}
         for q in quiz_data],
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Quiz – Sternik motorowodny</title>
  <style>
    :root {{
      --bg: #0f172a; --card: #1e293b; --text: #f1f5f9; --muted: #94a3b8;
      --accent: #38bdf8; --ok: #22c55e; --bad: #ef4444; --yellow: #facc15;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: "Segoe UI", system-ui, sans-serif; background: var(--bg);
      color: var(--text); min-height: 100vh; line-height: 1.5; }}
    .container {{ max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }}
    header {{ text-align: center; margin-bottom: 28px; }}
    header h1 {{ font-size: 1.5rem; margin-bottom: 6px; }}
    header p {{ color: var(--muted); font-size: 0.9rem; }}
    .progress-wrap {{ background: var(--card); border-radius: 12px; padding: 16px 20px; margin-bottom: 20px; }}
    .progress-label {{ display: flex; justify-content: space-between; font-size: 0.85rem;
      color: var(--muted); margin-bottom: 8px; }}
    .progress-bar {{ height: 8px; background: #334155; border-radius: 4px; overflow: hidden; }}
    .progress-fill {{ height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.3s; }}
    .card {{ background: var(--card); border-radius: 16px; padding: 24px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.3); }}
    .q-num {{ font-size: 0.8rem; color: var(--accent); font-weight: 600; margin-bottom: 8px; }}
    .q-text {{ font-size: 1.1rem; font-weight: 600; margin-bottom: 16px; }}
    .q-image {{ display: block; max-width: 100%; max-height: 220px; margin: 0 auto 20px;
      border-radius: 10px; background: #fff; padding: 8px; object-fit: contain; }}
    .options {{ display: flex; flex-direction: column; gap: 10px; }}
    .option {{ display: block; width: 100%; text-align: left; padding: 14px 16px;
      border: 2px solid #334155; border-radius: 10px; background: #0f172a; color: var(--text);
      font-size: 0.95rem; cursor: pointer; transition: border-color 0.15s, background 0.15s; }}
    .option:hover:not(:disabled) {{ border-color: var(--accent); background: #1e3a5f; }}
    .option:disabled {{ cursor: default; }}
    .option.correct-pick {{ border-color: var(--ok); background: rgba(34,197,94,0.15); }}
    .option.wrong-pick {{ border-color: var(--bad); background: rgba(239,68,68,0.15); }}
    .option.show-correct {{ border-color: var(--yellow); background: rgba(250,204,21,0.12); }}
    .feedback {{ margin-top: 16px; padding: 12px 16px; border-radius: 10px; font-weight: 600; display: none; }}
    .feedback.show {{ display: block; }}
    .feedback.ok {{ background: rgba(34,197,94,0.2); color: var(--ok); }}
    .feedback.bad {{ background: rgba(239,68,68,0.2); color: var(--bad); }}
    .btn-next {{ margin-top: 16px; width: 100%; padding: 14px; border: none; border-radius: 10px;
      background: var(--accent); color: #0f172a; font-size: 1rem; font-weight: 700;
      cursor: pointer; display: none; }}
    .btn-next.show {{ display: block; }}
    .results {{ text-align: center; display: none; }}
    .results.show {{ display: block; }}
    .score-circle {{ width: 140px; height: 140px; border-radius: 50%; border: 6px solid var(--accent);
      display: flex; align-items: center; justify-content: center; margin: 0 auto 20px;
      font-size: 2rem; font-weight: 800; }}
    .results p {{ color: var(--muted); margin-bottom: 8px; }}
    .btn-restart, .btn-start {{ padding: 14px 32px; border: none; border-radius: 10px;
      background: var(--accent); color: #0f172a; font-size: 1rem; font-weight: 700; cursor: pointer; }}
    .start-screen {{ text-align: center; }}
    .start-screen p {{ color: var(--muted); margin: 16px 0 24px; }}
    #quiz-area {{ display: none; }}
    #quiz-area.active {{ display: block; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Quiz – Sternik motorowodny</h1>
      <p>Wind Sailing School · 368 pytań</p>
    </header>
    <div id="start-screen" class="card start-screen">
      <p id="q-count"></p>
      <p>Pytania w losowej kolejności. Przy znakach nawigacyjnych wyświetlany jest obraz z PDF.</p>
      <button class="btn-start" id="btn-start">Rozpocznij quiz</button>
    </div>
    <div id="quiz-area">
      <div class="progress-wrap">
        <div class="progress-label">
          <span id="progress-text"></span>
          <span id="score-live">Poprawne: 0</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
      </div>
      <div class="card" id="question-card">
        <div class="q-num" id="q-num"></div>
        <div class="q-text" id="q-text"></div>
        <img class="q-image" id="q-image" alt="Znak nawigacyjny" style="display:none">
        <div class="options" id="options"></div>
        <div class="feedback" id="feedback"></div>
        <button class="btn-next" id="btn-next">Następne pytanie</button>
      </div>
      <div class="card results" id="results">
        <div class="score-circle" id="score-pct">0%</div>
        <h2>Koniec quizu!</h2>
        <p id="score-detail"></p>
        <p id="score-effectiveness"></p>
        <button class="btn-restart" id="btn-restart">Zagraj ponownie</button>
      </div>
    </div>
  </div>
  <script>
    const QUESTIONS = {data_json};

    function shuffle(arr) {{
      const a = [...arr];
      for (let i = a.length - 1; i > 0; i--) {{
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
      }}
      return a;
    }}

    let order = [], index = 0, correctCount = 0, answered = false;
    const startScreen = document.getElementById("start-screen");
    const quizArea = document.getElementById("quiz-area");
    const qCountEl = document.getElementById("q-count");
    const progressText = document.getElementById("progress-text");
    const progressFill = document.getElementById("progress-fill");
    const scoreLive = document.getElementById("score-live");
    const qNum = document.getElementById("q-num");
    const qText = document.getElementById("q-text");
    const qImage = document.getElementById("q-image");
    const optionsEl = document.getElementById("options");
    const feedback = document.getElementById("feedback");
    const btnNext = document.getElementById("btn-next");
    const questionCard = document.getElementById("question-card");
    const results = document.getElementById("results");

    qCountEl.textContent = `Baza: ${{QUESTIONS.length}} pytań`;

    function startQuiz() {{
      order = shuffle(QUESTIONS.map((_, i) => i));
      index = 0; correctCount = 0;
      startScreen.style.display = "none";
      quizArea.classList.add("active");
      questionCard.style.display = "block";
      results.classList.remove("show");
      renderQuestion();
    }}

    function renderQuestion() {{
      answered = false;
      const q = QUESTIONS[order[index]];
      const total = order.length;
      progressText.textContent = `Pytanie ${{index + 1}} z ${{total}}`;
      progressFill.style.width = ((index) / total) * 100 + "%";
      scoreLive.textContent = `Poprawne: ${{correctCount}}`;
      qNum.textContent = `Pytanie nr ${{q.id}}`;
      qText.textContent = q.question;
      if (q.image) {{
        qImage.src = q.image;
        qImage.style.display = "block";
      }} else {{
        qImage.style.display = "none";
        qImage.removeAttribute("src");
      }}
      feedback.className = "feedback";
      btnNext.classList.remove("show");
      optionsEl.innerHTML = "";
      shuffle(Object.keys(q.options)).forEach(letter => {{
        const btn = document.createElement("button");
        btn.className = "option";
        btn.type = "button";
        btn.dataset.letter = letter;
        btn.innerHTML = `<strong>${{letter.toUpperCase()}})</strong> ${{q.options[letter]}}`;
        btn.addEventListener("click", () => pickAnswer(btn, letter, q));
        optionsEl.appendChild(btn);
      }});
    }}

    function pickAnswer(btn, letter, q) {{
      if (answered) return;
      answered = true;
      const ok = letter === q.correct;
      if (ok) correctCount++;
      document.querySelectorAll(".option").forEach(opt => {{
        opt.disabled = true;
        const l = opt.dataset.letter;
        if (l === q.correct) opt.classList.add("show-correct");
        if (opt === btn && ok) opt.classList.add("correct-pick");
        if (opt === btn && !ok) opt.classList.add("wrong-pick");
      }});
      feedback.className = "feedback show " + (ok ? "ok" : "bad");
      feedback.textContent = ok ? "✓ Poprawna odpowiedź!"
        : `✗ Błędna. Prawidłowa: ${{q.correct.toUpperCase()}}) ${{q.options[q.correct]}}`;
      btnNext.classList.add("show");
      scoreLive.textContent = `Poprawne: ${{correctCount}}`;
    }}

    function nextQuestion() {{
      index++;
      if (index >= order.length) showResults();
      else renderQuestion();
    }}

    function showResults() {{
      questionCard.style.display = "none";
      results.classList.add("show");
      progressFill.style.width = "100%";
      const total = order.length;
      const pct = Math.round((correctCount / total) * 100);
      document.getElementById("score-pct").textContent = pct + "%";
      document.getElementById("score-detail").textContent =
        `Poprawnie: ${{correctCount}} z ${{total}} pytań.`;
      document.getElementById("score-effectiveness").textContent =
        `Twoja skuteczność: ${{pct}}%`;
    }}

    document.getElementById("btn-start").onclick = startQuiz;
    btnNext.onclick = nextQuestion;
    document.getElementById("btn-restart").onclick = () => {{
      results.classList.remove("show");
      startScreen.style.display = "block";
      quizArea.classList.remove("active");
    }};
  </script>
</body>
</html>"""


def main():
    doc = fitz.open(PDF)
    questions = parse_questions(doc)
    print(f"Pytań: {len(questions)}")
    extract_images(doc, questions)
    with_img = sum(1 for q in questions if q.get("image"))
    print(f"Z obrazami: {with_img}")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(
            [{"id": q["num"], "question": q["question"], "options": q["options"],
              "correct": q["correct"], **({"image": q["image"]} if q.get("image") else {})}
             for q in questions],
            f, ensure_ascii=False, indent=2,
        )
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(questions))
    print(f"Zapisano: {OUT_HTML}")


if __name__ == "__main__":
    main()
