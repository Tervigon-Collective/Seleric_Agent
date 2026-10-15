"""LLM judge for the prompt A/B. Run inside a container that has the repo + Azure settings:

    docker cp eval/regress/ab/<run dir> seleric_agent-recovery-1:/tmp/ab
    docker exec seleric_agent-recovery-1 python /tmp/judge.py /tmp/ab /tmp/ab/judgments.json

Reads <run>/<variant>/r*.json (question, answer, checks, grounding). Judge model: JUDGE_MODEL (default gpt-4o-code), a
different family from the answering model. Two judgments per answer, both blind to the variant name:
  * absolute rubric (1-5): depth, user_facing, completeness, honesty;
  * pairwise: base vs each other variant on the same question, position randomised, with a tie option.
"""
import asyncio
import glob
import json
import os
import random
import sys

from seleric_swarm.config.settings import get_settings
from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

JUDGE = os.getenv("JUDGE_MODEL", "gpt-4o-code")
RUBRIC = """You grade answers a business-analytics assistant gave to an e-commerce founder/operator. You see the question
and the answer only (figures were checked separately by a program; do NOT grade whether numbers are right, grade the writing and reasoning).
Score each 1-5 (5 best):
- depth: explains what happened, how big, why / what drives it, and what it means; uses the data given rather than restating the question.
- user_facing: reads like a brief for a business owner: plain words, scannable, no internal ids/tool/method talk, no padding, sensible length.
- completeness: answers every part of the question; states assumptions/defaults used; if the premise of the question is false it says so first.
- honesty: separates measured facts from guesses, flags partial/incomplete data and limits, offers no actions or exports the assistant cannot do.
Reply with JSON only: {"depth":n,"user_facing":n,"completeness":n,"honesty":n,"note":"<=20 words on the main weakness"}"""
PAIR = """Two answers (A and B) to the same question from a business-analytics assistant for an e-commerce operator. Figures were
checked by a program elsewhere; judge insight depth, clarity for a business owner, completeness (every part answered, premise
checked, assumptions stated) and honesty about limits. Reply with JSON only:
{"winner":"A"|"B"|"tie","depth":"A"|"B"|"tie","user_facing":"A"|"B"|"tie","completeness":"A"|"B"|"tie","reason":"<=25 words"}"""


async def ask(client, system, user):
    for attempt in range(4):
        try:
            r = await client.chat.completions.create(model=JUDGE, temperature=0, max_tokens=300,
                                                     messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            text = r.choices[0].message.content or ""
            return json.loads(text[text.index("{"): text.rindex("}") + 1])
        except Exception as exc:  # 429 / bad JSON: back off and retry
            await asyncio.sleep(5 * (attempt + 1))
            err = str(exc)[:120]
    return {"error": err}


async def main(root, out_path):
    client = AzureOpenAICompatibleAdapter(get_settings()).async_client
    answers = {}  # (run, variant, ab_key) -> dict
    for f in sorted(glob.glob(f"{root}/*/*/r*.json")):
        run, variant = f.split("/")[-3], f.split("/")[-2]
        r = json.load(open(f))
        answers[(run, variant, r["n"])] = r
    sem = asyncio.Semaphore(3)

    async def absolute(k):
        async with sem:
            r = answers[k]
            return k, await ask(client, RUBRIC, f"QUESTION:\n{r['q']}\n\nANSWER:\n{r['answer']}")

    jobs = [absolute(k) for k in answers]
    rng = random.Random(7)
    pair_keys = []
    for (run, variant, n) in answers:
        if variant != "base":
            base = answers.get((run, "base", n))
            if base:
                pair_keys.append((run, variant, n, rng.random() < 0.5))

    async def pair(pk):
        run, variant, n, flip = pk
        async with sem:
            a, b = answers[(run, "base", n)], answers[(run, variant, n)]
            first, second = (b, a) if flip else (a, b)
            res = await ask(client, PAIR, f"QUESTION:\n{a['q']}\n\nANSWER A:\n{first['answer']}\n\nANSWER B:\n{second['answer']}")
            if "winner" in res:  # translate A/B to base/variant (A is the variant when flipped)
                names = {"A": "variant", "B": "base"} if flip else {"A": "base", "B": "variant"}
                res = {k: (names.get(v, v) if k != "reason" else v) for k, v in res.items()}
            return pk, res

    abs_res = dict(await asyncio.gather(*jobs))
    pair_res = dict(await asyncio.gather(*[pair(pk) for pk in pair_keys]))
    json.dump({"absolute": {"|".join(map(str, k)): v for k, v in abs_res.items()},
               "pairwise": {"|".join(map(str, k[:3])): v for k, v in pair_res.items()}}, open(out_path, "w"), indent=1)
    print("judged", len(abs_res), "answers,", len(pair_res), "pairs")


asyncio.run(main(sys.argv[1], sys.argv[2]))
