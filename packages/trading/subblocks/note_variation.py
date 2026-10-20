import random

ANGLES = {
    "stop":    ("the stop: how wide it was and how that compares to my usual",
                ["stop_vs_usual"]),
    "streak":  ("where this trade sat in my recent run of results",
                ["streak", "last_in_name"]),
    "location":("where price sat when I entered",
                ["location", "prior20"]),
    "path":    ("how the trade actually moved between entry and exit",
                ["mae_mfe"]),
    "history": ("how this setup has played out in this name before",
                ["setup_hist"]),
    "clock":   ("when in the day and week I entered, and how long it lasted",
                ["clock"]),
}
LENGTHS = ["450 to 600 words", "550 to 750 words", "650 to 850 words"]

def pick(seed, pool):
    """pool: dict key -> fact line (only keys that were computable)."""
    r = random.Random(seed)
    usable = [a for a, (_, need) in ANGLES.items()
              if all(k in pool for k in need)]
    angle = r.choice(usable) if usable else None
    chosen = dict(pool.get("_core", {}))          # core trade lines, always
    keys = [k for k in pool if k != "_core"]
    must = ANGLES[angle][1] if angle else []
    extras = [k for k in keys if k not in must]
    r.shuffle(extras)
    for k in must + extras[: r.randint(3, 4)]:
        chosen[k] = pool[k]
    return angle, chosen, r.choice(LENGTHS)
def variation_block(seed: str) -> str:
    r = random.Random(seed)
    angle_text = r.choice([v[0] for v in ANGLES.values()])
    length = r.choice(LENGTHS)
    return (
        f"Spine: {angle_text}. Length: {length}. "
        "Use only the facts listed; leave out the ones that don't serve the spine."
    )