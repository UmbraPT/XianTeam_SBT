# con_sbtxian.py
# SBT with on-chain traits and bulk updates (no forbidden builtins, no leading underscores)

owners = Hash()            # owners[address] = token_id
metadata = Hash()          # metadata[token_id] = uri (optional)
traits = Hash()            # traits[address, key] = str
token_counter = Variable()
operator = Variable()
sbt_holders = Hash()       # sbt_holders[address] = True

# Canonical on-chain trait keys
ALLOWED_KEYS = [
    "Score",
    "Tier",
    "Stake Duration",
    "DEX Volume",
    "Pulse Influence",
    "Trx Volume",
    "Xian Bridged",
    "Volume Played",
]

MintEvent = LogEvent(
    event='Mint',
    params={'to': {'type': str, 'idx': True}, 'token_id': {'type': int}}
)

TraitUpdatedEvent = LogEvent(
    event='TraitUpdated',
    params={'user': {'type': str, 'idx': True}, 'key': {'type': str}, 'value': {'type': str}}
)

TraitsBatchUpdatedEvent = LogEvent(
    event='TraitsBatchUpdated',
    params={'user': {'type': str, 'idx': True}, 'count': {'type': int}}
)

@construct
def seed():
    operator.set(ctx.caller)
    token_counter.set(0)

@export
def mint(to: str, uri: str):
    """
    Mint exactly one SBT for `to`. Seeds default traits.
    """
    assert owners[to] is None, 'User already has an SBT'

    new_id = token_counter.get() + 1
    token_counter.set(new_id)

    owners[to] = new_id
    metadata[new_id] = uri
    sbt_holders[to] = True

    init_defaults(to)

    MintEvent({'to': to, 'token_id': new_id})

def init_defaults(addr: str):
    """
    Defaults: numeric traits "0"; Tier "Leafling".
    """
    for k in ALLOWED_KEYS:
        if k == "Tier":
            traits[addr, "Tier"] = "Leafling"
        else:
            traits[addr, k] = "0"

@export
def update_trait(key: str, value: str):
    """
    Update a single trait for the caller (must own SBT).
    Auto-updates Tier if Score is updated.
    """
    assert sbt_holders[ctx.caller] is True, "Must hold an SBT"
    assert is_allowed(key), "key not allowed"
    v = str(value)

    if key == "Score":
        traits[ctx.caller, "Score"] = v
        traits[ctx.caller, "Tier"] = tier_for_score(to_int(v))
        TraitUpdatedEvent({'user': ctx.caller, 'key': "Score", 'value': v})
        TraitUpdatedEvent({'user': ctx.caller, 'key': "Tier", 'value': traits[ctx.caller, "Tier"]})
        return

    traits[ctx.caller, key] = v
    TraitUpdatedEvent({'user': ctx.caller, 'key': key, 'value': v})

@export
def update_traits(batch: dict):
    """
    Bulk update multiple traits in one transaction.
    Only keys present in `batch` are updated.
    If Score is present, Tier is recomputed automatically.
    Bounded to max 10 keys per call.
    """
    assert sbt_holders[ctx.caller] is True, "Must hold an SBT"

    # bound number of keys to protect stamps
    count_keys = 0
    for loop_key in batch:
        count_keys = count_keys + 1
        assert count_keys <= 10, "too many keys"

    changed = 0

    # If Score is included, apply first so Tier can be recomputed
    if "Score" in batch:
        vscore = str(batch["Score"])
        traits[ctx.caller, "Score"] = vscore
        traits[ctx.caller, "Tier"] = tier_for_score(to_int(vscore))
        TraitUpdatedEvent({'user': ctx.caller, 'key': "Score", 'value': vscore})
        TraitUpdatedEvent({'user': ctx.caller, 'key': "Tier", 'value': traits[ctx.caller, "Tier"]})
        changed = changed + 2

    # Update remaining keys (except Score/Tier which we handled)
    for k in ALLOWED_KEYS:
        if k == "Score" or k == "Tier":
            continue
        v_in = batch.get(k)
        if v_in is not None:
            v = str(v_in)
            if traits[ctx.caller, k] != v:
                traits[ctx.caller, k] = v
                TraitUpdatedEvent({'user': ctx.caller, 'key': k, 'value': v})
                changed = changed + 1

    TraitsBatchUpdatedEvent({'user': ctx.caller, 'count': changed})

@export
def get_trait(user: str, key: str):
    return traits[user, key]

@export
def get_all_traits(user: str):
    out = {}
    for k in ALLOWED_KEYS:
        out[k] = traits[user, k]
    return out

@export
def token_of(user: str):
    return owners[user]

@export
def token_uri(token_id: int):
    return metadata[token_id]

@export
def has_sbt(address: str):
    return sbt_holders[address] is not None

# -------- helpers (pure) --------

def tier_for_score(score_val: int):
    s = to_int(score_val)
    if s < 500:
        return "Leafling"
    if s < 1500:
        return "Vine Crawler"
    if s < 3000:
        return "Canopy Dweller"
    if s < 5000:
        return "Rainkeeper"
    if s < 10000:
        return "Jaguar Fang"
    return "Spirit of the Jungle"

def is_allowed(k: str):
    for x in ALLOWED_KEYS:
        if x == k:
            return True
    return False

def to_int(x):
    # safe int coercion without try/except and without len()
    s = str(x)
    if s == "":
        return 0
    negative = False
    index = 0
    first = s[0]
    if first == "-":
        negative = True
        index = 1

    acc = 0
    # iterate over remaining chars
    sub = s[index:]
    for ch in sub:
        if ch < "0" or ch > "9":
            return 0
        acc = acc * 10 + (ord(ch) - 48)

    if negative:
        acc = 0 - acc
    return acc
