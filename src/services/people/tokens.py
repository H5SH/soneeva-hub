"""Token balances and what a change does to the giver's."""

from src.services.people.rules import Reach, Refused


def held(tokens: int | None) -> int:
    """What a user holds that came out of someone's balance. Overspent is nothing."""
    return max(0, tokens or 0)


def plan_tokens(giver_reach: Reach, self_target: bool, wanted: int | None):
    """The transfer plan for a token change, run inside the transaction (People.transfer). The plan gets the
    person's tokens and the giver's balance (None: no limit) and returns what to add to that balance."""

    def plan(current: int | None, balance: int | None) -> int:
        if wanted == current or giver_reach == "all":
            return 0
        if self_target:
            raise Refused("You can't change your own tokens")
        if balance is None:
            return 0
        if wanted is None:
            raise Refused("Only someone with no limit can give no limit")
        cost = wanted - held(current)
        if cost > held(balance):
            raise Refused(f"You have {held(balance)} tokens left to give", 409)
        return -cost

    return plan
