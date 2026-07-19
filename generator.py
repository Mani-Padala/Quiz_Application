"""
generator.py — Question generation for the Quiz Application

Uses Groq (fast, free-tier friendly) to generate multiple-choice questions
from retrieved document context, at a specified difficulty level.

Design note on few-shot examples:
FEW_SHOT_EXAMPLES below is a plain string constant — a placeholder generic
format for now. When you have real exam papers to use as reference, replace
this constant's contents (or better: load it from a file, see
load_few_shot_examples() at the bottom) without touching any other function.
Every function that builds a prompt accepts an optional `reference_examples`
argument that overrides FEW_SHOT_EXAMPLES — that's the lever for plugging in
real reference material later without changing this file's structure.
"""

import os
import json
from groq import Groq

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROQ_MODEL = "llama-3.3-70b-versatile"  # good balance of quality/speed on Groq's free tier

DIFFICULTY_GUIDANCE = {
    "easy": "Test direct recall of a single fact stated explicitly in the context. "
            "The answer should be findable by locating one sentence.",
    "medium": "Test understanding that requires connecting two related facts from "
              "the context, or correctly applying a definition to a described scenario.",
    "hard": "Test deeper reasoning — comparing two concepts from the context, "
            "identifying an edge case, or spotting why a plausible-sounding "
            "distractor is actually wrong.",
}

# Generic placeholder few-shot format. Swap this out (or use
# load_few_shot_examples()) once real reference exam papers are available.
FEW_SHOT_EXAMPLES = """
Example question format (follow this structure exactly):

Context: "The mitochondria is the organelle responsible for producing ATP
through cellular respiration."

Question: {
  "question": "Which organelle is primarily responsible for producing ATP?",
  "options": {
    "A": "Nucleus",
    "B": "Mitochondria",
    "C": "Ribosome",
    "D": "Golgi apparatus"
  },
  "correct_answer": "B",
  "explanation": "The mitochondria produces ATP through cellular respiration, as stated directly in the context."
}
"""


# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

def get_groq_client(api_key=None):
    """
    Creates a Groq client. If api_key is provided (e.g. a per-user key from
    the web app's quiz-start form), that's used — each user then consumes
    their own free-tier quota instead of everyone sharing one server key.
    Falls back to GROQ_API_KEY from the environment if no key is passed in
    (used by main.py's CLI flow, where there's no per-user key concept).
    """
    resolved_key = api_key or os.environ.get("GROQ_API_KEY")
    if not resolved_key:
        raise EnvironmentError(
            "No Groq API key provided and GROQ_API_KEY not found in environment. "
            "Pass an api_key explicitly, or set: export GROQ_API_KEY=your_key_here"
        )
    return Groq(api_key=resolved_key)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(context_chunks, topic, difficulty, num_questions, reference_examples=None):
    """
    Builds the full prompt sent to Groq.

    context_chunks: list of strings (retrieved chunks from retriever.py)
    topic: string, the section/topic name these questions belong to
    difficulty: one of "easy", "medium", "hard"
    num_questions: how many questions to generate in this call
    reference_examples: optional string overriding FEW_SHOT_EXAMPLES —
        this is the lever for plugging in real exam papers later.
    """
    if difficulty not in DIFFICULTY_GUIDANCE:
        raise ValueError(f"difficulty must be one of {list(DIFFICULTY_GUIDANCE.keys())}")

    examples = reference_examples if reference_examples else FEW_SHOT_EXAMPLES
    combined_context = "\n\n".join(context_chunks)

    prompt = f"""You are generating exam questions for a quiz application.

TOPIC: {topic}
DIFFICULTY: {difficulty}
DIFFICULTY GUIDANCE: {DIFFICULTY_GUIDANCE[difficulty]}

CONTEXT (generate questions ONLY from this material, do not use outside knowledge):
---
{combined_context}
---

{examples}

Generate exactly {num_questions} multiple-choice questions following the JSON
format shown in the example above. Each question must have exactly 4 options
(A-D), exactly one correct_answer, and a one-sentence explanation.

Return ONLY a JSON array of question objects, no other text, no markdown
code fences. Example shape:
[
  {{"question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_answer": "A", "explanation": "..."}}
]
"""
    return prompt


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_groq(prompt, api_key=None):
    """
    Sends the prompt to Groq and returns the raw text response.
    """
    client = get_groq_client(api_key=api_key)
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": "You are a precise exam question generator. Output only valid JSON, nothing else."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,  # lower temperature: favor consistent, well-formed output over creative variation
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_questions(raw_response):
    """
    Parses Groq's raw text response into a Python list of question dicts.
    Handles the common case where the model wraps JSON in markdown fences
    despite being told not to.
    """
    text = raw_response.strip()

    # Strip markdown code fences if the model added them anyway
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        text = text.replace("```json", "").replace("```", "").strip()

    try:
        questions = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse LLM response as JSON. Raw response was:\n{raw_response}"
        ) from e

    if not isinstance(questions, list):
        raise ValueError("Expected a JSON array of questions, got something else.")

    for q in questions:
        required_keys = {"question", "options", "correct_answer", "explanation"}
        if not required_keys.issubset(q.keys()):
            raise ValueError(f"Question missing required keys: {q}")
        if q["correct_answer"] not in q["options"]:
            raise ValueError(f"correct_answer '{q['correct_answer']}' not in options: {q}")

    return questions


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_questions(context_chunks, topic, difficulty, num_questions=5, reference_examples=None, api_key=None):
    """
    Main function called by quiz.py.

    context_chunks: list of strings — output of retriever.py's retrieve_context()
    topic: string — section/topic name
    difficulty: "easy" | "medium" | "hard"
    num_questions: how many questions to generate
    reference_examples: optional string to override the generic few-shot
        format — pass in real exam paper excerpts here once available.
    api_key: optional per-user Groq API key. If omitted, falls back to
        GROQ_API_KEY from the environment (see get_groq_client).

    Returns: list of dicts, each with keys:
        question, options (dict A-D), correct_answer, explanation
    """
    prompt = build_prompt(context_chunks, topic, difficulty, num_questions, reference_examples)
    raw_response = call_groq(prompt, api_key=api_key)
    return parse_questions(raw_response)


# ---------------------------------------------------------------------------
# Optional: load real reference examples from a file later
# ---------------------------------------------------------------------------

def load_few_shot_examples(file_path):
    """
    Loads a reference exam paper (or curated example questions) from a text
    file, to pass as `reference_examples` into generate_questions().

    Usage once you have real material:
        examples = load_few_shot_examples("reference_exams/finance_exam.txt")
        questions = generate_questions(chunks, "Finance", "hard", reference_examples=examples)
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()