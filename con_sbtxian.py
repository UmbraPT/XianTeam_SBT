# con_sbt_traits.py
# Soulbound Token with on-chain traits and user-controlled off-chain sync

owners = Hash()          # owners[address] = token_id
metadata = Hash()        # metadata[token_id] = uri/string
traits = Hash()          # traits[address, key] = value
token_counter = Variable()
operator = Variable()
sbt_holders = Hash()     # sbt_holders[address] = True

MintEvent = LogEvent(
    event='Mint',
    params={'to': {'type': str, 'idx': True}, 'token_id': {'type': int}}
)

UpdateMetadataEvent = LogEvent(
    event='UpdateMetadata',
    params={'token_id': {'type': int}, 'uri': {'type': str}}
)

TraitUpdatedEvent = LogEvent(
    event='TraitUpdated',
    params={'user': {'type': str, 'idx': True}, 'key': {'type': str}, 'value': {'type': str}}
)

@construct
def seed():
    operator.set(ctx.caller)
    token_counter.set(0)

@export
def mint(to: str, uri: str):
    """
    Mint an SBT to `to` with initial URI.
    Enforces one SBT per address.
    """
    assert owners[to] is None, 'User already has an SBT'

    token_id = token_counter.get() + 1
    token_counter.set(token_id)

    owners[to] = token_id
    metadata[token_id] = uri
    sbt_holders[to] = True

    MintEvent({'to': to, 'token_id': token_id})

@export
def update_trait(key: str, value: str):
    """
    Called by the SBT holder to update their own trait.
    The off-chain UI will fetch the latest DB value and
    call this function with the new value.
    """
    assert sbt_holders[ctx.caller] is True, "Must hold an SBT to update traits"
    traits[ctx.caller, key] = value
    TraitUpdatedEvent({'user': ctx.caller, 'key': key, 'value': value})

@export
def update_metadata():
    """
    Rebuilds the metadata string for the holder’s token using on-chain traits.
    Can be called by the holder at any time.
    """
    assert sbt_holders[ctx.caller] is True, "Must hold an SBT"

    token_id = owners[ctx.caller]
    assert token_id is not None, "No SBT found"

    # Example: build a simple metadata string "Score:123;Tier:Gold"
    keys = ["Score", "Tier", "Stake Duration", "DEX Volume", "Game Wins", "Bots Created", "Pulse Influence"]
    parts = []
    for k in keys:
        v = traits[ctx.caller, k]
        if v is None:
            v = ""
        parts.append(f"{k}:{v}")
    new_uri = ";".join(parts)

    metadata[token_id] = new_uri
    UpdateMetadataEvent({'token_id': token_id, 'uri': new_uri})

@export
def token_of(user: str):
    return owners[user]

@export
def token_uri(token_id: int):
    return metadata[token_id]

@export
def get_trait(user: str, key: str):
    return traits[user, key]

@export
def get_all_traits(user: str):
    keys = ["Score", "Tier", "Stake Duration", "DEX Volume", "Game Wins", "Bots Created", "Pulse Influence"]
    out = {}
    for k in keys:
        out[k] = traits[user, k]
    return out

@export
def change_operator(new_operator: str):
    assert ctx.caller == operator.get(), "Only operator can change operator"
    operator.set(new_operator)

@export
def has_sbt(address: str):
    return sbt_holders[address] is not None