import sys
from llm import providers
from pipeline import run

if not providers(): sys.exit("Set GEMINI_API_KEY, GROQ_API_KEY or XAI_API_KEY first")
url = sys.argv[1] if len(sys.argv) > 1 else input("YouTube link: ").strip()
for c in run(url): print(f"  {c['file']}  {c['title']}")
